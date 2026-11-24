from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from scope_agent.domain.impact import ImpactRecord
from scope_agent.persistence.database import Database, impact_areas, impact_records, insert_stmt, joined_session


def _to_row(r: ImpactRecord) -> dict:
    return {
        "repo": r.repo, "commit": r.commit, "subject": r.subject, "epic_key": r.epic_key,
        "author_name": r.author_name, "author_email": r.author_email, "committed_at": r.committed_at,
        "max_severity": r.max_severity.value if r.max_severity else None,
        "data": r.model_dump(mode="json"),
    }


class ImpactRepository:
    """Impact records are immutable: the first record saved for (repo, commit) wins."""

    def __init__(self, db: Database):
        self._db = db

    def save(self, records: list[ImpactRecord], session: Session | None = None) -> None:
        if not records:
            return
        with joined_session(self._db, session) as s:
            unique = list({(r.repo, r.commit): r for r in reversed(records)}.values())[::-1]  # first wins
            inserted = s.execute(
                insert_stmt(s, impact_records).on_conflict_do_nothing()
                .returning(impact_records.c.repo, impact_records.c.commit),
                [_to_row(r) for r in unique],
            ).all()
            won = {(row.repo, row.commit) for row in inserted}
            areas = [
                {"repo": r.repo, "commit": r.commit, "area": a}
                for r in unique if (r.repo, r.commit) in won for a in dict.fromkeys(r.areas)
            ]
            if areas:
                s.execute(insert_stmt(s, impact_areas).on_conflict_do_nothing(), areas)

    def _load(self, *where) -> list[ImpactRecord]:
        q = (select(impact_records.c.data).where(*where)
             .order_by(impact_records.c.committed_at, impact_records.c.repo, impact_records.c.commit))
        with self._db.session() as s:
            return [ImpactRecord.model_validate(row.data) for row in s.execute(q).all()]

    def for_subject(self, subject: str) -> list[ImpactRecord]:
        return self._load(impact_records.c.subject == subject)

    def for_ticket(self, key: str) -> list[ImpactRecord]:
        # jira_keys lives in the JSON document; filter in Python for dialect independence.
        return [
            r for r in self._load()
            if key in r.jira_keys
        ]

    def subjects(self) -> list[str]:
        with self._db.session() as s:
            q = select(impact_records.c.subject).distinct().order_by(impact_records.c.subject)
            return [r[0] for r in s.execute(q).all()]

    def latest_change(self, subject: str) -> datetime | None:
        with self._db.session() as s:
            q = (select(impact_records.c.committed_at).where(impact_records.c.subject == subject)
                 .order_by(impact_records.c.committed_at.desc()).limit(1))
            row = s.execute(q).first()
        return row[0] if row else None
