from sqlalchemy import select
from sqlalchemy.orm import Session

from scope_agent.persistence.database import (
    Clock, Database, analyzed_commits, insert_stmt, joined_session, utcnow,
)


class CommitLedger:
    def __init__(self, db: Database, clock: Clock = utcnow):
        self._db = db
        self._clock = clock

    def is_analyzed(self, repo: str, sha: str) -> bool:
        with self._db.session() as s:
            q = select(analyzed_commits.c.sha).where(
                analyzed_commits.c.repo == repo, analyzed_commits.c.sha == sha
            )
            return s.execute(q).first() is not None

    def mark_analyzed(self, repo: str, shas: list[str], session: Session | None = None) -> None:
        if not shas:
            return
        now = self._clock()
        with joined_session(self._db, session) as s:
            stmt = insert_stmt(s, analyzed_commits).on_conflict_do_nothing(index_elements=["repo", "sha"])
            s.execute(stmt, [{"repo": repo, "sha": sha, "analyzed_at": now} for sha in dict.fromkeys(shas)])
