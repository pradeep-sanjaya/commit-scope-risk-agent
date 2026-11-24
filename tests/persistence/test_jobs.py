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
    jobs.complete(jobs.claim(60).id)
    assert jobs.enqueue("push", {}, "k") is None


def test_enqueue_pending_scope_debounces_then_allows_after_completion(jobs, clock):
    jobs.enqueue("epic_report", {"epic": "PAY-100"}, "epic_report:PAY-100", "pending", not_before=clock.now)
    assert jobs.enqueue("epic_report", {"epic": "PAY-100"}, "epic_report:PAY-100", "pending") is None
    c = jobs.claim(60)
    jobs.complete(c.id)
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
    jobs.defer(jid, until=clock.now + timedelta(hours=1))
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
    jobs.release(jid)
    assert jobs.claim(60).attempts == 1


def test_retry_schedules_and_records_error(jobs, clock, db):
    jid = jobs.enqueue("push", {}, "k")
    jobs.claim(60)
    jobs.retry(jid, clock.now + timedelta(minutes=5), "boom")
    assert jobs.claim(60) is None
    clock.advance(minutes=6)
    assert jobs.claim(60).id == jid


def test_fail_and_count_by_status(jobs):
    a = jobs.enqueue("push", {}, "a")
    jobs.enqueue("push", {}, "b")
    jobs.claim(60)
    jobs.fail(a, "bad")
    assert jobs.count_by_status() == {"failed": 1, "queued": 1}
    assert jobs.claim(60).id != a


def test_count_deferred(jobs, clock):
    jid = jobs.enqueue("push", {}, "a")
    jobs.claim(60)
    jobs.defer(jid, clock.now + timedelta(days=1))
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
