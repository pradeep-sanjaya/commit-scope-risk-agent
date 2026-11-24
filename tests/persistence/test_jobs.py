import os
from datetime import timedelta

import pytest

from scope_agent.persistence.database import Database
from scope_agent.persistence.jobs import JobQueue


@pytest.fixture
def jobs(db, clock):
    return JobQueue(db, clock)


def test_enqueue_dedups_forever(jobs):
    assert jobs.enqueue("push", {"a": 1}, "push:PAY/r:development:abc") is not None
    assert jobs.enqueue("push", {"a": 1}, "push:PAY/r:development:abc") is None


def test_forever_blocks_even_after_completion(jobs):
    jobs.enqueue("push", {}, "k")
    jobs.complete(jobs.claim(60).id, 1)
    assert jobs.enqueue("push", {}, "k") is None


def test_enqueue_pending_scope_debounces_then_allows_after_completion(jobs, clock):
    jobs.enqueue("epic_report", {"epic": "PAY-100"}, "epic_report:PAY-100", "pending", not_before=clock.now)
    assert jobs.enqueue("epic_report", {"epic": "PAY-100"}, "epic_report:PAY-100", "pending") is None
    c = jobs.claim(60)
    jobs.complete(c.id, 1)
    assert jobs.enqueue("epic_report", {"epic": "PAY-100"}, "epic_report:PAY-100", "pending") is not None


def test_claim_returns_payload_and_attempts(jobs):
    jid = jobs.enqueue("push", {"a": 1}, "k")
    c = jobs.claim(60)
    assert (c.id, c.kind, c.payload, c.attempts) == (jid, "push", {"a": 1}, 1)
    assert jobs.claim(60) is None


def test_claim_oldest_first_and_respects_not_before(jobs, clock):
    later = jobs.enqueue("push", {}, "later", not_before=clock.now + timedelta(hours=1))
    first = jobs.enqueue("push", {}, "first")
    second = jobs.enqueue("push", {}, "second")
    assert jobs.claim(60).id == first
    assert jobs.claim(60).id == second
    assert jobs.claim(60) is None
    clock.advance(hours=2)
    assert jobs.claim(60).id == later


def test_claim_skips_future_deferred(jobs, clock):
    jid = jobs.enqueue("push", {}, "k")
    jobs.claim(60)
    jobs.defer(jid, 1, until=clock.now + timedelta(hours=1))
    assert jobs.claim(60) is None
    clock.advance(hours=2)
    assert jobs.claim(60).id == jid


def test_expired_lease_is_reclaimed(jobs, clock):
    jid = jobs.enqueue("push", {}, "k")
    jobs.claim(60)
    assert jobs.claim(60) is None
    clock.advance(seconds=61)
    claimed = jobs.claim(60)
    assert claimed.id == jid and claimed.attempts == 2


def test_release_returns_job_without_counting_attempt(jobs):
    jid = jobs.enqueue("push", {}, "k")
    jobs.claim(60)
    jobs.release(jid, 1)
    assert jobs.claim(60).attempts == 1


def test_retry_schedules_and_records_error(jobs, clock, db):
    jid = jobs.enqueue("push", {}, "k")
    jobs.claim(60)
    jobs.retry(jid, 1, clock.now + timedelta(minutes=5), "boom")
    assert jobs.claim(60) is None
    clock.advance(minutes=6)
    assert jobs.claim(60).id == jid


def test_fail_and_count_by_status(jobs):
    a = jobs.enqueue("push", {}, "a")
    jobs.enqueue("push", {}, "b")
    jobs.claim(60)
    jobs.fail(a, 1, "bad")
    assert jobs.count_by_status() == {"failed": 1, "queued": 1}
    assert jobs.claim(60).id != a


def test_count_deferred(jobs, clock):
    jid = jobs.enqueue("push", {}, "a")
    jobs.claim(60)
    jobs.defer(jid, 1, clock.now + timedelta(days=1))
    assert jobs.count_by_status() == {"deferred": 1}


@pytest.mark.skipif(not os.environ.get("SCOPE_TEST_DATABASE_URL"), reason="needs Postgres")
def test_claim_postgres_skip_locked(clock):
    db = Database(os.environ["SCOPE_TEST_DATABASE_URL"])
    db.create_all()
    q = JobQueue(db, clock)
    a = q.enqueue("push", {}, f"pg-a-{clock.now.timestamp()}")
    b = q.enqueue("push", {}, f"pg-b-{clock.now.timestamp()}")
    with db.session() as s1, db.session() as s2:
        c1 = q.claim(60, session=s1)
        c2 = q.claim(60, session=s2)
    assert {c1.id, c2.id} == {a, b}


@pytest.mark.skipif(not os.environ.get("SCOPE_TEST_DATABASE_URL"), reason="needs Postgres")
def test_postgres_forever_dedup_race_and_hold(clock, monkeypatch):
    from scope_agent.persistence.jobs import LeaseLost
    db = Database(os.environ["SCOPE_TEST_DATABASE_URL"])
    db.create_all()
    q = JobQueue(db, clock)
    key = f"pg-dedup-{clock.now.timestamp()}-{os.getpid()}"
    jid = q.enqueue("push", {}, key)
    monkeypatch.setattr(JobQueue, "_blocked", lambda self, s, k, scope: False)
    with db.session() as s:  # the failed insert must not poison the caller's transaction
        assert q.enqueue("push", {}, key, session=s) is None
        assert q.enqueue("push", {}, key + "-other", session=s) is not None
    monkeypatch.undo()
    while (job := q.claim(60)) is not None and job.id != jid:
        q.complete(job.id, job.attempts)
    with db.session() as s:
        q.hold(job.id, job.attempts, s)
    with pytest.raises(LeaseLost), db.session() as s:
        q.hold(job.id, job.attempts + 1, s)


def test_transitions_on_non_running_job_are_noops(jobs, clock):
    jid = jobs.enqueue("push", {}, "k")  # queued, never claimed
    assert jobs.complete(jid, 1) is False
    assert jobs.retry(jid, 1, clock.now, "x") is False
    assert jobs.fail(jid, 1, "x") is False
    assert jobs.defer(jid, 1, clock.now) is False
    assert jobs.release(jid, 1) is False
    assert jobs.count_by_status() == {"queued": 1}


def test_stale_worker_cannot_touch_reclaimed_then_finished_job(jobs, clock):
    jid = jobs.enqueue("push", {}, "k")
    jobs.claim(60)
    clock.advance(seconds=61)
    jobs.claim(60)  # second worker reclaims
    assert jobs.complete(jid, 2) is True  # second worker finishes
    assert jobs.retry(jid, 1, clock.now, "stale") is False  # first worker, late
    assert jobs.fail(jid, 1, "stale") is False
    assert jobs.count_by_status() == {"done": 1}


def test_release_never_drives_attempts_negative(jobs):
    jid = jobs.enqueue("push", {}, "k")
    jobs.claim(60)
    assert jobs.release(jid, 1) is True
    assert jobs.release(jid, 1) is False
    assert jobs.claim(60).attempts == 1


def test_transitions_return_true_when_running(jobs, clock):
    jid = jobs.enqueue("push", {}, "k")
    jobs.claim(60)
    assert jobs.complete(jid, 1) is True


def test_stale_attempt_cannot_transition_job_running_on_newer_attempt(jobs, clock, db):
    from sqlalchemy import select
    from scope_agent.persistence.database import jobs as t
    jid = jobs.enqueue("push", {}, "k")
    a = jobs.claim(60)
    clock.advance(seconds=61)
    b = jobs.claim(60)  # B reclaims; A is stale
    assert (a.attempts, b.attempts) == (1, 2)
    assert jobs.complete(jid, a.attempts) is False
    assert jobs.retry(jid, a.attempts, clock.now, "stale") is False
    assert jobs.fail(jid, a.attempts, "stale") is False
    assert jobs.defer(jid, a.attempts, clock.now) is False
    assert jobs.release(jid, a.attempts) is False
    with db.session() as s:
        row = s.execute(select(t.c.status, t.c.attempts, t.c.last_error).where(t.c.id == jid)).one()
    assert tuple(row) == ("running", 2, None)
    assert jobs.complete(jid, b.attempts) is True


def test_defer_does_not_count_attempt(jobs, clock):
    """Budget deferrals must not use up the retry budget."""
    jid = jobs.enqueue("epic_report", {}, "k")
    for _ in range(3):
        job = jobs.claim(60)
        assert job.attempts == 1
        assert jobs.defer(jid, job.attempts, clock.now) is True
    assert jobs.claim(60).attempts == 1


def test_forever_dedup_enforced_by_constraint_when_check_races(jobs, monkeypatch):
    """Two deliveries that both pass the existence check still yield one job."""
    assert jobs.enqueue("push", {}, "push:k") is not None
    monkeypatch.setattr(JobQueue, "_blocked", lambda self, s, key, scope: False)
    assert jobs.enqueue("push", {}, "push:k") is None
    assert jobs.count_by_status() == {"queued": 1}


def test_pending_scope_allows_requeue_while_running(jobs, clock):
    jid = jobs.enqueue("epic_report", {}, "r", "pending")
    job = jobs.claim(60)
    assert jobs.enqueue("epic_report", {}, "r", "pending") is not None
    assert jobs.retry(jid, job.attempts, clock.now, "boom") is True


def test_hold_fences_stale_worker(jobs, clock, db):
    from scope_agent.persistence.jobs import LeaseLost
    jobs.enqueue("push", {}, "k")
    a = jobs.claim(60)
    with db.session() as s:
        jobs.hold(a.id, a.attempts, s)  # current holder: no error
    clock.advance(seconds=61)
    b = jobs.claim(60)
    with pytest.raises(LeaseLost):
        with db.session() as s:
            jobs.hold(a.id, a.attempts, s)
    with db.session() as s:
        jobs.hold(b.id, b.attempts, s)


def test_create_all_upgrades_jobs_table_without_unique_key(tmp_path, monkeypatch):
    from sqlalchemy import MetaData, Table
    from scope_agent.persistence.database import jobs as t
    db = Database(f"sqlite:///{tmp_path}/old.db")
    old = MetaData()  # the jobs table as created before unique_key existed
    Table("jobs", old, *[c._copy() for c in t.columns if c.name != "unique_key"]).create(db.engine)
    db.create_all()
    db.create_all()  # idempotent
    q = JobQueue(db)
    assert q.enqueue("push", {}, "push:k") is not None
    monkeypatch.setattr(JobQueue, "_blocked", lambda self, s, key, scope: False)
    assert q.enqueue("push", {}, "push:k") is None


def test_claim_does_not_take_job_claimed_in_between(jobs, monkeypatch):
    """SQLite has no SKIP LOCKED: the claiming UPDATE must re-check the job is still runnable."""
    jobs.enqueue("push", {}, "k")
    first = jobs.claim(60)
    monkeypatch.setattr(JobQueue, "_candidate", lambda self, s, now: first.id)
    assert jobs.claim(60) is None
