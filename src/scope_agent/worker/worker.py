import threading
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import structlog

from scope_agent.llm.base import BudgetExceeded
from scope_agent.persistence.database import Clock
from scope_agent.persistence.findings import FindingRepository
from scope_agent.persistence.jobs import ClaimedJob, JobQueue
from scope_agent.worker.handlers import JobHandler
from scope_agent.worker.retry_policy import RetryPolicy

log = structlog.get_logger()

DEFER_PAST_MIDNIGHT = timedelta(minutes=5)


class Worker:
    def __init__(
        self,
        jobs: JobQueue,
        handlers: dict[str, JobHandler],
        errors: FindingRepository,
        retry: RetryPolicy,
        lease_seconds: int,
        timezone: str,
        clock: Clock,
    ):
        self._jobs = jobs
        self._handlers = handlers
        self._errors = errors
        self._retry = retry
        self._lease_seconds = lease_seconds
        self._tz = ZoneInfo(timezone)
        self._clock = clock

    def run_once(self) -> bool:
        job = self._jobs.claim(self._lease_seconds)
        if job is None:
            return False
        bound = log.bind(job_id=job.id, kind=job.kind, attempts=job.attempts)
        handler = self._handlers.get(job.kind)
        if handler is None:
            self._fail(job, f"unknown job kind: {job.kind}", bound)
            return True
        try:
            handler.handle(job.id, job.payload)
        except BudgetExceeded:
            until = self._next_local_midnight_plus()
            bound.info("job_deferred_budget", until=until.isoformat())
            self._transition(self._jobs.defer(job.id, until), "defer", bound)
        except Exception as exc:
            self._on_error(job, exc, bound)
        else:
            self._transition(self._jobs.complete(job.id), "complete", bound)
        return True

    def run(self, stop: threading.Event, poll_seconds: float) -> None:
        while not stop.is_set():
            if not self.run_once():
                stop.wait(poll_seconds)

    def _on_error(self, job: ClaimedJob, exc: Exception, bound) -> None:
        message = str(exc) or type(exc).__name__
        next_at = self._retry.next_attempt_at(job.attempts, self._clock())
        if next_at is not None:
            bound.warning("job_retry", error=message, next_attempt_at=next_at.isoformat())
            self._transition(self._jobs.retry(job.id, next_at, message), "retry", bound)
        else:
            self._fail(job, message, bound)

    def _fail(self, job: ClaimedJob, message: str, bound) -> None:
        bound.error("job_failed", error=message)
        if self._transition(self._jobs.fail(job.id, message), "fail", bound):
            repo = job.payload.get("repo", "?") if isinstance(job.payload, dict) else "?"
            self._errors.save(job.id, [], {}, [(repo, None, message)])

    @staticmethod
    def _transition(applied: bool, action: str, bound) -> bool:
        if not applied:
            bound.warning("job_transition_lost", action=action)
        return applied

    def _next_local_midnight_plus(self) -> datetime:
        local = self._clock().astimezone(self._tz)
        next_day = local.date() + timedelta(days=1)
        midnight = datetime(next_day.year, next_day.month, next_day.day, tzinfo=self._tz)
        return (midnight + DEFER_PAST_MIDNIGHT).astimezone(timezone.utc)
