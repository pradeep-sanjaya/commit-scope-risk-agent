import threading
from datetime import datetime, timedelta, timezone

import pytest
from structlog.testing import capture_logs
from sqlalchemy import select

from scope_agent.domain.models import PushJob
from scope_agent.llm.base import BudgetExceeded
from scope_agent.persistence.database import Database, analysis_errors, jobs as jobs_table
from scope_agent.persistence.findings import FindingRepository
from scope_agent.persistence.jobs import JobQueue
from scope_agent.services.pipeline import PushResult
from scope_agent.worker.handlers import PushJobHandler
from scope_agent.worker.retry_policy import RetryPolicy
from scope_agent.worker.worker import Worker
from tests.persistence.conftest import FakeClock


@pytest.fixture
def env():
    db = Database("sqlite://")
    db.create_all()
    clock = FakeClock()
    return db, clock, JobQueue(db, clock), FindingRepository(db)


class Rec:
    def __init__(self, exc=None):
        self.calls, self.exc = [], exc

    def handle(self, job_id, payload, attempts):
        self.calls.append((job_id, payload))
        if self.exc:
            raise self.exc


def row(db, jid):
    with db.session() as s:
        return s.execute(select(jobs_table).where(jobs_table.c.id == jid)).one()


def worker(env, handlers, backoff=(30,), tz="UTC"):
    db, clock, q, errors = env
    return Worker(q, handlers, errors, RetryPolicy(list(backoff)), 60, tz, clock)


def test_worker_happy_path_completes(env):
    db, clock, q, _ = env
    jid = q.enqueue("push", {"repo": "P/r"}, "k")
    h = Rec()
    assert worker(env, {"push": h}).run_once() is True
    assert h.calls == [(jid, {"repo": "P/r"})]
    assert row(db, jid).status == "done"
    assert worker(env, {"push": h}).run_once() is False


def test_worker_defers_on_budget(env):
    db, clock, q, _ = env
    jid = q.enqueue("push", {}, "k")
    worker(env, {"push": Rec(BudgetExceeded("x"))}, tz="Asia/Colombo").run_once()
    r = row(db, jid)
    assert r.status == "deferred"
    # 2026-01-01 12:00 UTC = 17:30 Colombo (+5:30); next local midnight = Jan 2 00:00 +05:30 = Jan 1 18:30 UTC
    assert r.not_before == datetime(2026, 1, 1, 18, 35, tzinfo=timezone.utc)
    assert r.attempts == 0  # a budget deferral does not use up a retry


def test_worker_retries_then_fails_records_error(env):
    db, clock, q, errors = env
    jid = q.enqueue("push", {"repo": "P/r"}, "k")
    w = worker(env, {"push": Rec(RuntimeError("boom"))}, backoff=(30,))
    w.run_once()
    r = row(db, jid)
    assert r.status == "queued" and r.last_error == "boom"
    assert r.not_before == clock.now + timedelta(seconds=30)
    assert w.run_once() is False
    clock.advance(seconds=31)
    w.run_once()
    r = row(db, jid)
    assert r.status == "failed" and r.last_error == "boom"
    with db.session() as s:
        errs = s.execute(select(analysis_errors.c.repo, analysis_errors.c.commit, analysis_errors.c.message)).all()
    assert [tuple(e) for e in errs] == [("P/r", None, "boom")]


def test_worker_survives_exception_and_processes_next(env):
    db, clock, q, _ = env
    a = q.enqueue("bad", {}, "a")
    b = q.enqueue("good", {}, "b")
    w = worker(env, {"bad": Rec(RuntimeError("x")), "good": Rec()})
    w.run_once()
    w.run_once()
    assert row(db, b).status == "done"
    assert row(db, a).status == "queued"


def test_worker_dispatches_by_kind_and_fails_unknown_kind(env):
    db, clock, q, _ = env
    a = q.enqueue("push", {}, "a")
    b = q.enqueue("mystery", {"repo": "P/r"}, "b")
    h = Rec()
    w = worker(env, {"push": h})
    w.run_once()
    w.run_once()
    assert row(db, a).status == "done" and len(h.calls) == 1
    r = row(db, b)
    assert r.status == "failed" and "mystery" in r.last_error


def test_worker_stop_event_exits_loop(env):
    db, clock, q, _ = env
    stop = threading.Event()
    h = Rec()
    q.enqueue("push", {}, "a")

    class StopAfter(Rec):
        def handle(self, job_id, payload, attempts):
            stop.set()

    w = worker(env, {"push": StopAfter()})
    w.run(stop, 0.01)  # returns because the handler set stop
    stop2 = threading.Event()
    threading.Timer(0.05, stop2.set).start()
    w.run(stop2, 0.01)  # idle loop exits when stopped


def test_worker_lost_transition_is_logged_not_raised(env):
    db, clock, q, _ = env
    q.enqueue("push", {}, "a")

    class Slow(Rec):
        def handle(self, job_id, payload, attempts):
            clock.advance(seconds=120)
            other = q.claim(60)  # another worker reclaims and finishes
            q.complete(other.id, other.attempts)

    with capture_logs() as logs:
        worker(env, {"push": Slow()}).run_once()
    assert any(e["event"] == "job_transition_lost" and e["action"] == "complete" for e in logs)


class FakePipeline:
    """Mimics PushPipeline's commit step: fence, then on_commit, in one session."""

    def __init__(self, db, before_commit=lambda: None):
        self.db, self.before_commit, self.ran = db, before_commit, []

    def run(self, job_id, job, force=False, fence=None, on_commit=None):
        self.ran.append((job_id, job))
        result = PushResult(job_id=job_id, job=job)
        self.before_commit()
        with self.db.session() as s:
            if fence is not None:
                fence(s)
            if on_commit is not None:
                on_commit(result, s)
        return result


class BadNotifier:
    def notify(self, result):
        raise RuntimeError("smtp down")


class Notifier:
    def __init__(self):
        self.sent = []

    def notify(self, result):
        self.sent.append(result)


PUSH = PushJob(repo="P/r", branch="development", from_hash=None, to_hash="abc", pushed_by=None)


def test_worker_email_failure_still_completes(env):
    db, clock, q, _ = env
    jid = q.enqueue("push", PUSH.model_dump(mode="json"), "k")
    pipeline, committed = FakePipeline(db), []
    handler = PushJobHandler(pipeline, BadNotifier(), q, lambda r, s: committed.append(r))
    worker(env, {"push": handler}).run_once()
    assert row(db, jid).status == "done"
    assert pipeline.ran == [(jid, PUSH)] and len(committed) == 1


def test_impact_recording_failure_retries_job_without_email(env):
    db, clock, q, _ = env
    jid = q.enqueue("push", PUSH.model_dump(mode="json"), "k")
    notifier = Notifier()

    def boom(result, session):
        raise RuntimeError("impact down")

    worker(env, {"push": PushJobHandler(FakePipeline(db), notifier, q, boom)}).run_once()
    r = row(db, jid)
    assert (r.status, r.last_error) == ("queued", "impact down")
    assert notifier.sent == []


def test_stale_worker_writes_nothing_and_sends_no_email(env):
    db, clock, q, _ = env
    jid = q.enqueue("push", PUSH.model_dump(mode="json"), "k")
    notifier, committed, reclaimed = Notifier(), [], []

    def lease_expires_and_job_is_reclaimed():
        clock.advance(seconds=120)
        reclaimed.append(q.claim(60))

    pipeline = FakePipeline(db, lease_expires_and_job_is_reclaimed)
    handler = PushJobHandler(pipeline, notifier, q, lambda r, s: committed.append(r))
    with capture_logs() as logs:
        worker(env, {"push": handler}).run_once()
    assert committed == [] and notifier.sent == []
    assert any(e["event"] == "job_lease_lost" for e in logs)
    r = row(db, jid)
    assert (r.status, r.attempts) == ("running", reclaimed[0].attempts)  # untouched for the new holder
