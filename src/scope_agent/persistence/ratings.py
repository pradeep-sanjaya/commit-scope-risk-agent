from typing import Literal

from sqlalchemy import case, func, select

from scope_agent.persistence.database import Clock, Database, findings, insert_stmt, ratings, utcnow


class RatingRepository:
    def __init__(self, db: Database, clock: Clock = utcnow):
        self._db = db
        self._clock = clock

    def save(self, finding_id: str, value: Literal["useful", "noise"]) -> None:
        now = self._clock()
        with self._db.session() as s:
            stmt = insert_stmt(s, ratings).values(finding_id=finding_id, value=value, rated_at=now)
            s.execute(stmt.on_conflict_do_update(
                index_elements=["finding_id"], set_={"value": value, "rated_at": now}
            ))

    def report(self) -> list[dict]:
        useful = func.sum(case((ratings.c.value == "useful", 1), else_=0))
        noise = func.sum(case((ratings.c.value == "noise", 1), else_=0))
        q = (select(findings.c.type, findings.c.severity, useful, noise)
             .select_from(ratings.join(findings, findings.c.id == ratings.c.finding_id))
             .group_by(findings.c.type, findings.c.severity)
             .order_by(findings.c.type, findings.c.severity))
        with self._db.session() as s:
            return [{"type": t, "severity": sev, "useful": int(u), "noise": int(n)}
                    for t, sev, u, n in s.execute(q).all()]
