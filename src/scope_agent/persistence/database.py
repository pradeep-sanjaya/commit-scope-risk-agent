from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone

from sqlalchemy import (
    JSON, BigInteger, Column, Date, DateTime, Integer, MetaData, String, Table, Text,
    TypeDecorator, create_engine,
)
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

Clock = Callable[[], datetime]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime) -> datetime:
    """Normalise to UTC-aware; naive values are taken to be UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class UTCDateTime(TypeDecorator):
    """Stores UTC; always returns UTC-aware datetimes (SQLite drops tzinfo)."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return None if value is None else as_utc(value)

    def process_result_value(self, value, dialect):
        return None if value is None else as_utc(value)


metadata = MetaData()

jobs = Table(
    "jobs", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("kind", String(64), nullable=False),
    Column("payload", JSON, nullable=False),
    Column("dedup_key", String(512), nullable=False, index=True),
    Column("status", String(16), nullable=False, index=True),
    Column("not_before", UTCDateTime, nullable=False),
    Column("lease_until", UTCDateTime),
    Column("attempts", Integer, nullable=False, default=0),
    Column("last_error", Text),
    Column("created_at", UTCDateTime, nullable=False),
    Column("updated_at", UTCDateTime, nullable=False),
)

analyzed_commits = Table(
    "analyzed_commits", metadata,
    Column("repo", String(256), primary_key=True),
    Column("sha", String(64), primary_key=True),
    Column("analyzed_at", UTCDateTime, nullable=False),
)

findings = Table(
    "findings", metadata,
    Column("id", String(64), primary_key=True),
    Column("job_id", Integer, nullable=False, index=True),
    Column("repo", String(256), nullable=False),
    Column("commit", String(64), nullable=False),
    Column("author_email", String(320)),
    Column("jira_keys", JSON, nullable=False),
    Column("type", String(64), nullable=False),
    Column("category", String(128), nullable=False),
    Column("severity", String(16), nullable=False),
    Column("title", Text, nullable=False),
    Column("explanation", Text, nullable=False),
    Column("evidence", JSON, nullable=False),
    Column("files", JSON, nullable=False),
    Column("suggested_reviewers", JSON, nullable=False),
    Column("created_at", UTCDateTime, nullable=False, index=True),
)

library_summaries = Table(
    "library_summaries", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("job_id", Integer, nullable=False, index=True),
    Column("commit", String(64), nullable=False),
    Column("library", String(256), nullable=False),
    Column("kind", String(16), nullable=False),
    Column("summary", Text, nullable=False),
    Column("links", JSON, nullable=False),
)

analysis_errors = Table(
    "analysis_errors", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("job_id", Integer, nullable=False, index=True),
    Column("repo", String(256), nullable=False),
    Column("commit", String(64)),
    Column("message", Text, nullable=False),
    Column("created_at", UTCDateTime, nullable=False, index=True),
)

ratings = Table(
    "ratings", metadata,
    Column("finding_id", String(64), primary_key=True),
    Column("value", String(16), nullable=False),
    Column("rated_at", UTCDateTime, nullable=False),
)

cache = Table(
    "cache", metadata,
    Column("namespace", String(64), primary_key=True),
    Column("key", String(512), primary_key=True),
    Column("value", Text, nullable=False),
    Column("expires_at", UTCDateTime),
)

token_usage = Table(
    "token_usage", metadata,
    Column("day", Date, primary_key=True),
    Column("tokens", BigInteger, nullable=False, default=0),
)

meta = Table(
    "meta", metadata,
    Column("key", String(128), primary_key=True),
    Column("value", Text, nullable=False),
)


class Database:
    def __init__(self, url: str):
        kwargs: dict = {}
        if url.startswith("sqlite") and url in ("sqlite://", "sqlite:///:memory:"):
            # One shared in-memory DB for every session/thread.
            kwargs = {"poolclass": StaticPool, "connect_args": {"check_same_thread": False}}
        self.engine: Engine = create_engine(url, **kwargs)
        self._sessions = sessionmaker(self.engine, expire_on_commit=False)

    def create_all(self) -> None:
        metadata.create_all(self.engine)

    @contextmanager
    def session(self) -> Iterator[Session]:
        """Commit on success, roll back on exception."""
        s = self._sessions()
        try:
            yield s
            s.commit()
        except BaseException:
            s.rollback()
            raise
        finally:
            s.close()


@contextmanager
def joined_session(db: Database, session: Session | None) -> Iterator[Session]:
    """Join the caller's session if given (caller commits); otherwise own one."""
    if session is not None:
        yield session
    else:
        with db.session() as s:
            yield s


def insert_stmt(session: Session, table: Table):
    """Dialect insert supporting on_conflict_* (SQLite and PostgreSQL)."""
    name = session.get_bind().dialect.name
    return (postgresql.insert if name == "postgresql" else sqlite.insert)(table)
