from collections.abc import Callable
from typing import Protocol

import structlog
from sqlalchemy.orm import Session

from scope_agent.domain.models import PushJob
from scope_agent.persistence.jobs import JobQueue
from scope_agent.services.epic_report import EpicReportBuilder
from scope_agent.services.pipeline import PushPipeline, PushResult
from scope_agent.services.push_notifier import PushNotifier

log = structlog.get_logger()


class JobHandler(Protocol):
    def handle(self, job_id: int, payload: dict, attempts: int) -> None:
        """`attempts` is the claimed attempt number; handlers use it to fence their writes."""
        ...


class PushJobHandler:
    def __init__(
        self,
        pipeline: PushPipeline,
        notifier: PushNotifier,
        jobs: JobQueue | None = None,
        on_commit: Callable[[PushResult, Session], None] | None = None,
    ):
        self._pipeline = pipeline
        self._notifier = notifier
        self._jobs = jobs
        self._on_commit = on_commit

    def handle(self, job_id: int, payload: dict, attempts: int) -> None:
        jobs = self._jobs
        fence = None if jobs is None else (lambda s: jobs.hold(job_id, attempts, s))
        # Findings, analyzed marks and impact records commit together, and only while this worker still
        # holds the job: a failure retries the whole push, and a stale worker whose job was reclaimed
        # writes nothing and sends no email (LeaseLost propagates to the worker).
        result = self._pipeline.run(
            job_id, PushJob.model_validate(payload), fence=fence, on_commit=self._on_commit,
        )
        try:
            self._notifier.notify(result)
        except Exception as exc:
            # Findings are already saved; a failed email must not trigger re-analysis.
            log.error("push_notify_failed", job_id=job_id, error=str(exc))


class EpicReportJobHandler:
    def __init__(self, builder: EpicReportBuilder):
        self._builder = builder

    def handle(self, job_id: int, payload: dict, attempts: int) -> None:
        self._builder.rebuild(payload["subject"])
