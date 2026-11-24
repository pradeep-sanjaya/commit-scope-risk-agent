from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from scope_agent.persistence.database import Clock, Database, joined_session, jobs, utcnow

DedupScope = Literal["forever", "pending"]


class LeaseLost(Exception):
    """The worker no longer holds the job: its lease expired and another worker reclaimed it."""


@dataclass(frozen=True)
class ClaimedJob:
    id: int
    kind: str
    payload: dict
    attempts: int


class JobQueue:
    """Generic leased job queue; knows nothing about job contents."""

    def __init__(self, db: Database, clock: Clock = utcnow):
        self._db = db
        self._clock = clock

    def enqueue(
        self,
        kind: str,
        payload: dict,
        dedup_key: str,
        dedup_scope: DedupScope = "forever",
        not_before: datetime | None = None,
        session: Session | None = None,
    ) -> int | None:
        """Queue a job unless one with the same dedup key blocks it. "forever": any job with the key
        blocks, enforced by a unique constraint so racing enqueues still yield one job. "pending": only
        a queued or deferred job blocks (a running one does not, so changes made while it runs are
        picked up); a race here can queue a second job, which is acceptable for idempotent work."""
        now = self._clock()
        with joined_session(self._db, session) as s:
            if self._blocked(s, dedup_key, dedup_scope):
                return None
            values = dict(
                kind=kind, payload=payload, dedup_key=dedup_key, status="queued",
                unique_key=dedup_key if dedup_scope == "forever" else None,
                not_before=not_before or now, attempts=0, created_at=now, updated_at=now,
            )
            try:
                with s.begin_nested():
                    result = s.execute(jobs.insert().values(**values))
            except IntegrityError:
                return None  # a concurrent enqueue of the same forever key won
            return int(result.inserted_primary_key[0])

    def _blocked(self, s: Session, dedup_key: str, scope: DedupScope) -> bool:
        blocking = jobs.c.dedup_key == dedup_key
        if scope == "pending":
            blocking = and_(blocking, jobs.c.status.in_(("queued", "deferred")))
        return s.execute(select(jobs.c.id).where(blocking).limit(1)).first() is not None

    @staticmethod
    def _runnable(now: datetime):
        return or_(
            and_(jobs.c.status.in_(("queued", "deferred")), jobs.c.not_before <= now),
            and_(jobs.c.status == "running", jobs.c.lease_until < now),
        )

    def _candidate(self, s: Session, now: datetime) -> int | None:
        stmt = select(jobs.c.id).where(self._runnable(now)).order_by(jobs.c.id).limit(1)
        if s.get_bind().dialect.name == "postgresql":
            stmt = stmt.with_for_update(skip_locked=True)
        row = s.execute(stmt).first()
        return None if row is None else row[0]

    def claim(self, lease_seconds: int, session: Session | None = None) -> ClaimedJob | None:
        now = self._clock()
        with joined_session(self._db, session) as s:
            job_id = self._candidate(s, now)
            if job_id is None:
                return None
            # Re-check runnability in the UPDATE itself: without SKIP LOCKED (SQLite) another worker
            # may have claimed this job between the SELECT and here.
            claimed = s.execute(
                update(jobs).where(jobs.c.id == job_id, self._runnable(now)).values(
                    status="running", lease_until=now + timedelta(seconds=lease_seconds),
                    attempts=jobs.c.attempts + 1, updated_at=now,
                )
            )
            if claimed.rowcount == 0:
                return None
            job = s.execute(select(jobs).where(jobs.c.id == job_id)).one()
            return ClaimedJob(job.id, job.kind, job.payload, job.attempts)

    def hold(self, job_id: int, expected_attempts: int, session: Session) -> None:
        """Assert, inside the caller's transaction, that the caller still holds the job; raise LeaseLost
        otherwise. On PostgreSQL the job row stays locked until that transaction ends, so the job cannot
        be reclaimed while the caller commits its results."""
        cond = and_(jobs.c.id == job_id, jobs.c.status == "running", jobs.c.attempts == expected_attempts)
        stmt = select(jobs.c.id).where(cond)
        if session.get_bind().dialect.name == "postgresql":
            stmt = stmt.with_for_update()
        if session.execute(stmt).first() is None:
            raise LeaseLost(f"job {job_id} attempt {expected_attempts} no longer held")

    def _transition(self, job_id: int, expected_attempts: int, **values) -> bool:
        """Fenced state change: applies only if the job is still running AND still on the attempt
        the caller claimed. A worker whose lease expired (job reclaimed, attempts incremented) or
        whose job already finished therefore cannot alter it. The fence is the attempt number, not
        a lease token: it does not distinguish two holders of the same attempt (which cannot happen,
        as every claim increments attempts)."""
        cond = and_(jobs.c.id == job_id, jobs.c.status == "running", jobs.c.attempts == expected_attempts)
        with self._db.session() as s:
            result = s.execute(update(jobs).where(cond).values(updated_at=self._clock(), **values))
            return result.rowcount > 0

    def complete(self, job_id: int, expected_attempts: int) -> bool:
        return self._transition(job_id, expected_attempts, status="done", lease_until=None)

    def retry(self, job_id: int, expected_attempts: int, at: datetime, error: str) -> bool:
        return self._transition(
            job_id, expected_attempts, status="queued", not_before=at, lease_until=None, last_error=error
        )

    def fail(self, job_id: int, expected_attempts: int, error: str) -> bool:
        return self._transition(job_id, expected_attempts, status="failed", lease_until=None, last_error=error)

    def defer(self, job_id: int, expected_attempts: int, until: datetime) -> bool:
        """Postpone without counting the attempt: a budget deferral is not a failure, so it must not use
        up the retry budget."""
        return self._transition(
            job_id, expected_attempts, status="deferred", not_before=until, lease_until=None,
            attempts=jobs.c.attempts - 1,
        )

    def release(self, job_id: int, expected_attempts: int) -> bool:
        """Back to queued without counting the attempt (used on SIGTERM)."""
        return self._transition(
            job_id, expected_attempts, status="queued", lease_until=None, attempts=jobs.c.attempts - 1
        )

    def count_by_status(self) -> dict[str, int]:
        with self._db.session() as s:
            rows = s.execute(select(jobs.c.status, func.count()).group_by(jobs.c.status)).all()
        return {status: n for status, n in rows}
