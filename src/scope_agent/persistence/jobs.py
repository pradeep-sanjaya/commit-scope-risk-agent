from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.orm import Session

from scope_agent.persistence.database import Clock, Database, joined_session, jobs, utcnow


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
        dedup_scope: Literal["forever", "pending"] = "forever",
        not_before: datetime | None = None,
    ) -> int | None:
        now = self._clock()
        with self._db.session() as s:
            blocking = jobs.c.dedup_key == dedup_key
            if dedup_scope == "pending":
                blocking = and_(blocking, jobs.c.status.in_(("queued", "deferred")))
            if s.execute(select(jobs.c.id).where(blocking).limit(1)).first():
                return None
            result = s.execute(
                jobs.insert().values(
                    kind=kind, payload=payload, dedup_key=dedup_key, status="queued",
                    not_before=not_before or now, attempts=0, created_at=now, updated_at=now,
                )
            )
            return int(result.inserted_primary_key[0])

    def claim(self, lease_seconds: int, session: Session | None = None) -> ClaimedJob | None:
        now = self._clock()
        with joined_session(self._db, session) as s:
            runnable = or_(
                and_(jobs.c.status.in_(("queued", "deferred")), jobs.c.not_before <= now),
                and_(jobs.c.status == "running", jobs.c.lease_until < now),
            )
            stmt = select(jobs.c.id).where(runnable).order_by(jobs.c.id).limit(1)
            if s.get_bind().dialect.name == "postgresql":
                stmt = stmt.with_for_update(skip_locked=True)
            row = s.execute(stmt).first()
            if row is None:
                return None
            job_id = row[0]
            s.execute(
                update(jobs).where(jobs.c.id == job_id).values(
                    status="running", lease_until=now + timedelta(seconds=lease_seconds),
                    attempts=jobs.c.attempts + 1, updated_at=now,
                )
            )
            job = s.execute(select(jobs).where(jobs.c.id == job_id)).one()
            return ClaimedJob(job.id, job.kind, job.payload, job.attempts)

    def _set(self, job_id: int, **values) -> None:
        with self._db.session() as s:
            s.execute(update(jobs).where(jobs.c.id == job_id).values(updated_at=self._clock(), **values))

    def complete(self, job_id: int) -> None:
        self._set(job_id, status="done", lease_until=None)

    def retry(self, job_id: int, at: datetime, error: str) -> None:
        self._set(job_id, status="queued", not_before=at, lease_until=None, last_error=error)

    def fail(self, job_id: int, error: str) -> None:
        self._set(job_id, status="failed", lease_until=None, last_error=error)

    def defer(self, job_id: int, until: datetime) -> None:
        self._set(job_id, status="deferred", not_before=until, lease_until=None)

    def release(self, job_id: int) -> None:
        """Back to queued without counting the attempt (used on SIGTERM)."""
        self._set(job_id, status="queued", lease_until=None, attempts=jobs.c.attempts - 1)

    def count_by_status(self) -> dict[str, int]:
        with self._db.session() as s:
            rows = s.execute(select(jobs.c.status, func.count()).group_by(jobs.c.status)).all()
        return {status: n for status, n in rows}
