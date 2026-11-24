from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from scope_agent.domain.models import Finding, LibrarySummary
from scope_agent.persistence.database import (
    Clock, Database, analysis_errors, findings, joined_session, library_summaries, utcnow,
)


def _to_row(job_id: int, f: Finding) -> dict:
    return {
        "id": f.id, "job_id": job_id, "repo": f.repo, "commit": f.commit,
        "author_email": f.author_email, "jira_keys": f.jira_keys, "type": f.type.value,
        "category": f.category, "severity": f.severity.value, "title": f.title,
        "explanation": f.explanation, "evidence": f.evidence, "files": f.files,
        "suggested_reviewers": f.suggested_reviewers, "created_at": f.created_at,
    }


def _from_row(r) -> Finding:
    return Finding(
        id=r.id, repo=r.repo, commit=r.commit, author_email=r.author_email,
        jira_keys=r.jira_keys, type=r.type, category=r.category, severity=r.severity,
        title=r.title, explanation=r.explanation, evidence=r.evidence, files=r.files,
        suggested_reviewers=r.suggested_reviewers, created_at=r.created_at,
    )


class FindingRepository:
    def __init__(self, db: Database, clock: Clock = utcnow):
        self._db = db
        self._clock = clock

    def save(
        self,
        job_id: int,
        found: list[Finding],
        summaries: dict[str, list[LibrarySummary]],
        errors: list[tuple[str, str | None, str]],
        session: Session | None = None,
    ) -> None:
        now = self._clock()
        with joined_session(self._db, session) as s:
            if found:
                s.execute(findings.insert(), [_to_row(job_id, f) for f in found])
            rows = [
                {"job_id": job_id, "commit": commit, **ls.model_dump()}
                for commit, items in summaries.items() for ls in items
            ]
            if rows:
                s.execute(library_summaries.insert(), rows)
            if errors:
                s.execute(
                    analysis_errors.insert(),
                    [{"job_id": job_id, "repo": r, "commit": c, "message": m, "created_at": now}
                     for r, c, m in errors],
                )

    def get(self, finding_id: str) -> Finding | None:
        with self._db.session() as s:
            row = s.execute(select(findings).where(findings.c.id == finding_id)).first()
        return _from_row(row) if row else None

    def since(self, start: datetime, end: datetime) -> list[Finding]:
        """Findings with start <= created_at < end."""
        with self._db.session() as s:
            q = (select(findings)
                 .where(findings.c.created_at >= start, findings.c.created_at < end)
                 .order_by(findings.c.created_at, findings.c.id))
            return [_from_row(r) for r in s.execute(q).all()]

    def summaries_for(self, job_id: int) -> dict[str, list[LibrarySummary]]:
        out: dict[str, list[LibrarySummary]] = {}
        with self._db.session() as s:
            q = select(library_summaries).where(library_summaries.c.job_id == job_id).order_by(library_summaries.c.id)
            for r in s.execute(q).all():
                out.setdefault(r.commit, []).append(
                    LibrarySummary(library=r.library, kind=r.kind, summary=r.summary, links=r.links)
                )
        return out

    def errors_between(self, start: datetime, end: datetime) -> int:
        with self._db.session() as s:
            q = select(func.count()).select_from(analysis_errors).where(
                analysis_errors.c.created_at >= start, analysis_errors.c.created_at < end
            )
            return int(s.execute(q).scalar_one())
