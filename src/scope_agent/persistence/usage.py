from datetime import date

from sqlalchemy import select

from scope_agent.persistence.database import Clock, Database, insert_stmt, token_usage, utcnow


class TokenUsageMeter:
    def __init__(self, db: Database, clock: Clock = utcnow):
        self._db = db
        self._clock = clock

    def add(self, day: date, tokens: int) -> None:
        with self._db.session() as s:
            stmt = insert_stmt(s, token_usage).values(day=day, tokens=tokens)
            s.execute(stmt.on_conflict_do_update(
                index_elements=["day"], set_={"tokens": token_usage.c.tokens + stmt.excluded.tokens}
            ))

    def used(self, day: date) -> int:
        with self._db.session() as s:
            row = s.execute(select(token_usage.c.tokens).where(token_usage.c.day == day)).first()
        return int(row[0]) if row else 0
