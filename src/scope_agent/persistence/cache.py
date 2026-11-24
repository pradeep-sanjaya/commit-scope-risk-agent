from datetime import timedelta

from sqlalchemy import select

from scope_agent.persistence.database import Clock, Database, cache, insert_stmt, utcnow


class KeyValueCache:
    def __init__(self, db: Database, clock: Clock = utcnow):
        self._db = db
        self._clock = clock

    def get(self, namespace: str, key: str) -> str | None:
        with self._db.session() as s:
            row = s.execute(
                select(cache.c.value, cache.c.expires_at).where(cache.c.namespace == namespace, cache.c.key == key)
            ).first()
        if row is None or (row.expires_at is not None and row.expires_at <= self._clock()):
            return None
        return row.value

    def put(self, namespace: str, key: str, value: str, ttl_seconds: int | None) -> None:
        expires = None if ttl_seconds is None else self._clock() + timedelta(seconds=ttl_seconds)
        with self._db.session() as s:
            stmt = insert_stmt(s, cache).values(namespace=namespace, key=key, value=value, expires_at=expires)
            s.execute(stmt.on_conflict_do_update(
                index_elements=["namespace", "key"], set_={"value": value, "expires_at": expires}
            ))
