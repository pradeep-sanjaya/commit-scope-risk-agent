from datetime import datetime, timezone

from scope_agent.domain.models import (
    CommitData, Finding, FindingType, LibrarySummary, PushJob, Severity,
)
from scope_agent.notify.rating_links import LinkBuilder
from scope_agent.services.pipeline import PushResult


def links() -> LinkBuilder:
    return LinkBuilder("https://bb.example/", "https://jira.example/", "https://portal.example/", "sekret")


def finding(fid="f1", severity=Severity.high, title="T", category="risky", keys=("PAY-12",),
            repo="PAY/gateway", commit="a" * 40, evidence=("ev",), files=("a.java",), reviewers=("lead@x.io",)):
    return Finding(
        id=fid, repo=repo, commit=commit, author_email="dev@x.io", jira_keys=list(keys),
        type=FindingType.risky_change, category=category, severity=severity, title=title,
        explanation="because reasons", evidence=list(evidence), files=list(files),
        suggested_reviewers=list(reviewers),
    )


def commit(sha="a" * 40, repo="PAY/gateway", email="dev@x.io"):
    return CommitData(repo=repo, sha=sha, author_name="Dev", author_email=email, message="m",
                      parents=["p"], files=[], committed_at=datetime(2026, 1, 1, tzinfo=timezone.utc))


def result(findings, summaries=None, commits=None) -> PushResult:
    job = PushJob(repo="PAY/gateway", branch="development", from_hash="1" * 40, to_hash="9" * 40, pushed_by=None)
    return PushResult(job_id=1, job=job, commits=commits if commits is not None else [commit()],
                      findings=findings, summaries=summaries or {})


def summary():
    return LibrarySummary(library="lib", kind="upgrade", summary="bumped", links=["https://docs.example/x"])
