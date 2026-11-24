from sqlalchemy import select

from scope_agent.persistence.database import Clock, Database, insert_stmt, meta, utcnow


class MetaStore:
    def __init__(self, db: Database, clock: Clock = utcnow):
        self._db = db
        self._clock = clock

    def get(self, key: str) -> str | None:
        with self._db.session() as s:
            row = s.execute(select(meta.c.value).where(meta.c.key == key)).first()
        return row[0] if row else None

    def set(self, key: str, value: str) -> None:
        with self._db.session() as s:
            stmt = insert_stmt(s, meta).values(key=key, value=value)
            s.execute(stmt.on_conflict_do_update(index_elements=["key"], set_={"value": value}))
