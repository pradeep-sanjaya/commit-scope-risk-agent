from datetime import datetime, timezone

from scope_agent.domain.models import CommitData, Finding, FindingType, Severity


def test_severity_rank():
    assert Severity.high.rank > Severity.medium.rank > Severity.low.rank > Severity.info.rank == 0
    assert Severity.high.rank == 3


def _commit(parents):
    return CommitData(repo="PAY/x", sha="a", author_name="n", author_email=None, message="m",
                      parents=parents, files=[], committed_at=datetime(2026, 1, 1, tzinfo=timezone.utc))


def test_is_merge():
    assert _commit(["1", "2"]).is_merge
    assert not _commit(["1"]).is_merge


def test_finding_defaults():
    f = Finding(repo="PAY/x", commit="a", author_email=None, jira_keys=[], type=FindingType.risky_change,
                category="c", severity=Severity.low, title="t", explanation="e", evidence=[], files=[],
                suggested_reviewers=[])
    assert f.id and f.created_at is not None
