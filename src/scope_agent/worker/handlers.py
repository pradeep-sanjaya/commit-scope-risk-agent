from collections.abc import Callable
from typing import Protocol

import structlog

from scope_agent.domain.models import PushJob
from scope_agent.services.pipeline import PushPipeline, PushResult
from scope_agent.services.push_notifier import PushNotifier

log = structlog.get_logger()


class JobHandler(Protocol):
    def handle(self, job_id: int, payload: dict) -> None: ...


class PushJobHandler:
    def __init__(
        self,
        pipeline: PushPipeline,
        notifier: PushNotifier,
        after_push: Callable[[PushResult], None] = lambda r: None,
    ):
        self._pipeline = pipeline
        self._notifier = notifier
        self._after_push = after_push

    def handle(self, job_id: int, payload: dict) -> None:
        result = self._pipeline.run(job_id, PushJob.model_validate(payload))
        try:
            self._after_push(result)
        except Exception as exc:
            # Commits are already analyzed; a retry would skip them and lose the email too.
            log.error("after_push_failed", job_id=job_id, error=str(exc))
        try:
            self._notifier.notify(result)
        except Exception as exc:
            # Findings are already saved; a failed email must not trigger re-analysis.
            log.error("push_notify_failed", job_id=job_id, error=str(exc))
