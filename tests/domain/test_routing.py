from datetime import datetime, timezone

from scope_agent.domain.models import CommitData, Finding, FindingType, Severity
from scope_agent.domain.routing import (
    RepoRouting,
    RoutingConfig,
    push_recipients,
    reviewers_for,
)


def _routing(repo_key="payments-service"):
    return RoutingConfig(
        repos={
            repo_key: RepoRouting(
                leads=["Lead-A@corp"],
                paths={"src/main/java/**/flags/**": ["flags-owner@corp"]},
            )
        },
        categories={
            "connection_model": ["gateway-owner@corp", "platform-sre@corp"],
        },
        default_leads=["eng-managers@corp"],
    )


def _finding(repo="PAY/payments-service", category="connection_model",
             files=("src/main/java/x/flags/F.java",), commit="c1", author="Dev@corp"):
    return Finding(
        repo=repo, commit=commit, author_email=author, jira_keys=[],
        type=FindingType.risky_change, category=category, severity=Severity.high,
        title="t", explanation="e", evidence=[], files=list(files),
        suggested_reviewers=[],
    )


def _commit(sha, author):
    return CommitData(
        repo="PAY/payments-service", sha=sha, author_name="n", author_email=author,
        message="m", parents=[], files=[],
        committed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def test_repo_path_category_owners():
    assert reviewers_for(_finding(), _routing()) == [
        "lead-a@corp", "flags-owner@corp", "gateway-owner@corp", "platform-sre@corp",
    ]


def test_unknown_repo_default_leads():
    f = _finding(repo="PAY/unknown", category="other")
    assert reviewers_for(f, _routing()) == ["eng-managers@corp"]


def test_default_leads_not_suppressed_by_category_only():
    f = _finding(repo="PAY/unknown")
    assert reviewers_for(f, _routing()) == [
        "eng-managers@corp", "gateway-owner@corp", "platform-sre@corp",
    ]


def test_missing_author_still_leads():
    f = _finding(author=None)
    out = push_recipients([f], [_commit("c1", None)], _routing())
    assert out == [
        "lead-a@corp", "flags-owner@corp", "gateway-owner@corp", "platform-sre@corp",
    ]


def test_slug_or_project_slug_key():
    for key in ("payments-service", "PAY/payments-service"):
        assert reviewers_for(_finding(category="x"), _routing(key))[0] == "lead-a@corp"


def test_push_recipients_authors_first_only_flagged_commits():
    commits = [_commit("c1", "Dev@corp"), _commit("c2", "other@corp")]
    out = push_recipients([_finding()], commits, _routing())
    assert out[0] == "dev@corp"
    assert "other@corp" not in out
    assert len(out) == len(set(out)) == 5
