from datetime import datetime

from pydantic import BaseModel
from sqlalchemy import func, select

from scope_agent.domain.epic_report import ReportContent, ReportDiff
from scope_agent.persistence.database import Clock, Database, epic_reports, utcnow


class StoredReportMeta(BaseModel):
    version: int
    created_at: datetime
    record_count: int
    diff: ReportDiff


class StoredReport(BaseModel):
    subject: str
    version: int
    created_at: datetime
    content: ReportContent
    diff: ReportDiff
    record_count: int


class ReportRepository:
    """Append-only: each rebuild adds a new version; nothing is updated or deleted."""

    def __init__(self, db: Database, clock: Clock = utcnow):
        self._db = db
        self._clock = clock

    def append(self, subject: str, content: ReportContent, diff: ReportDiff, record_count: int) -> int:
        with self._db.session() as s:
            current = s.execute(
                select(func.max(epic_reports.c.version)).where(epic_reports.c.subject == subject)
            ).scalar()
            version = (current or 0) + 1  # unique (subject, version) rejects a concurrent duplicate
            s.execute(epic_reports.insert().values(
                subject=subject, version=version, created_at=self._clock(), record_count=record_count,
                content=content.model_dump(mode="json"), diff=diff.model_dump(mode="json"),
            ))
        return version

    @staticmethod
    def _stored(r) -> StoredReport:
        return StoredReport(
            subject=r.subject, version=r.version, created_at=r.created_at, record_count=r.record_count,
            content=ReportContent.model_validate(r.content), diff=ReportDiff.model_validate(r.diff),
        )

    def _one(self, subject: str, *order_or_where):
        with self._db.session() as s:
            return s.execute(select(epic_reports).where(epic_reports.c.subject == subject, *order_or_where)
                             .order_by(epic_reports.c.version.desc()).limit(1)).first()

    def latest(self, subject: str) -> StoredReport | None:
        row = self._one(subject)
        return self._stored(row) if row else None

    def get(self, subject: str, version: int) -> StoredReport | None:
        row = self._one(subject, epic_reports.c.version == version)
        return self._stored(row) if row else None

    def versions(self, subject: str) -> list[StoredReportMeta]:
        with self._db.session() as s:
            rows = s.execute(select(epic_reports).where(epic_reports.c.subject == subject)
                             .order_by(epic_reports.c.version)).all()
        return [StoredReportMeta(version=r.version, created_at=r.created_at, record_count=r.record_count,
                                 diff=ReportDiff.model_validate(r.diff)) for r in rows]
