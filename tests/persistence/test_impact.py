from datetime import datetime, timedelta, timezone

from scope_agent.domain.impact import Endpoint, ImpactRecord
from scope_agent.domain.models import DependencyChange, Severity
from scope_agent.persistence.impact import ImpactRepository

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def rec(commit="c1", subject="PAY-100", keys=("PAY-1",), note="n", at=T0, areas=("A",), repo="PAY/r"):
    return ImpactRecord(
        repo=repo, commit=commit, author_name="A", author_email=None, committed_at=at,
        jira_keys=list(keys), epic_key="PAY-100" if subject == "PAY-100" else None, subject=subject,
        areas=list(areas), endpoints=[Endpoint("GET", "/a", "added", "C.java")], migrations=[], config_files=[],
        dependency_changes=[DependencyChange(ecosystem="npm", name="x", old_version="1", new_version="2", kind="upgraded", file="package.json")],
        finding_ids=["f1"], max_severity=Severity.high, test_note=[note],
    )


def test_repository_save_is_idempotent_and_immutable(db):
    repo = ImpactRepository(db)
    repo.save([rec(note="first")])
    repo.save([rec(note="second", areas=("B",)), rec(commit="c2")])
    got = {r.commit: r for r in repo.for_subject("PAY-100")}
    assert set(got) == {"c1", "c2"}
    assert got["c1"].test_note == ["first"] and got["c1"].areas == ["A"]
    assert got["c1"] == rec(note="first")  # full round trip


def test_for_subject_ordered_and_subjects_and_latest_change(db):
    repo = ImpactRepository(db)
    repo.save([rec("c2", at=T0 + timedelta(hours=1)), rec("c1", at=T0), rec("c3", subject="PAY-7", keys=("PAY-7",))])
    assert [r.commit for r in repo.for_subject("PAY-100")] == ["c1", "c2"]
    assert repo.subjects() == ["PAY-100", "PAY-7"]
    assert repo.latest_change("PAY-100") == T0 + timedelta(hours=1)
    assert repo.latest_change("nope") is None


def test_for_ticket_matches_every_key(db):
    repo = ImpactRepository(db)
    repo.save([rec("c1", keys=("PAY-1", "PAY-2")), rec("c2", keys=("PAY-10",), subject="PAY-10")])
    assert [r.commit for r in repo.for_ticket("PAY-1")] == ["c1"]
    assert [r.commit for r in repo.for_ticket("PAY-2")] == ["c1"]
    assert [r.commit for r in repo.for_ticket("PAY-10")] == ["c2"]


def test_save_joins_caller_session(db):
    repo = ImpactRepository(db)
    try:
        with db.session() as s:
            repo.save([rec()], session=s)
            raise RuntimeError
    except RuntimeError:
        pass
    assert repo.for_subject("PAY-100") == []
