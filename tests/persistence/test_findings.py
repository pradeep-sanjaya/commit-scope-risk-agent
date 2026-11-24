from datetime import timedelta

import pytest

from scope_agent.domain.models import LibrarySummary
from scope_agent.persistence.commits import CommitLedger
from scope_agent.persistence.findings import FindingRepository


def test_findings_save_atomic_with_ledger(db, finding):
    with db.session() as s:
        FindingRepository(db).save(1, [finding], {"abc": []}, [("PAY/r", "abc", "boom")], session=s)
        CommitLedger(db).mark_analyzed("PAY/r", ["abc"], session=s)
    assert CommitLedger(db).is_analyzed("PAY/r", "abc") and FindingRepository(db).get(finding.id)


def test_rollback_discards_both(db, finding):
    with pytest.raises(RuntimeError):
        with db.session() as s:
            FindingRepository(db).save(1, [finding], {}, [], session=s)
            CommitLedger(db).mark_analyzed("PAY/r", ["abc"], session=s)
            raise RuntimeError
    assert not CommitLedger(db).is_analyzed("PAY/r", "abc")
    assert FindingRepository(db).get(finding.id) is None


def test_roundtrip_and_since(db, finding, clock):
    repo = FindingRepository(db, clock)
    repo.save(1, [finding], {}, [])
    got = repo.get(finding.id)
    assert got == finding and got.created_at.tzinfo is not None
    start = finding.created_at - timedelta(seconds=1)
    end = finding.created_at + timedelta(seconds=1)
    assert [f.id for f in repo.since(start, end)] == [finding.id]
    assert repo.since(end, end + timedelta(days=1)) == []


def test_summaries_and_errors(db, finding, clock):
    repo = FindingRepository(db, clock)
    ls = LibrarySummary(library="lib", kind="upgrade", summary="s", links=["http://x"])
    repo.save(7, [], {"abc": [ls]}, [("PAY/r", None, "e1"), ("PAY/r", "abc", "e2")])
    assert repo.summaries_for(7) == {"abc": [ls]}
    assert repo.summaries_for(8) == {}
    assert repo.errors_between(clock.now - timedelta(seconds=1), clock.now + timedelta(seconds=1)) == 2
    assert repo.errors_between(clock.now + timedelta(seconds=1), clock.now + timedelta(days=1)) == 0
