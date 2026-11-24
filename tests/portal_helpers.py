"""Seed the PAY-100 example (Task 18) into in-memory repositories for portal tests."""

from datetime import datetime, timedelta, timezone

from scope_agent.domain.epic_report import ReportContent, aggregate, diff_reports
from scope_agent.domain.impact import ImpactRecord
from scope_agent.domain.models import EpicTicket, Finding, FindingType, Severity, TicketContext
from scope_agent.llm.schemas import TestScopeItem
from scope_agent.persistence.database import Database
from scope_agent.persistence.findings import FindingRepository
from scope_agent.persistence.impact import ImpactRepository
from scope_agent.persistence.reports import ReportRepository
from scope_agent.services.portal_queries import PortalQueries

T0 = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


def tk(key):
    return EpicTicket(key=key, summary=f"summary {key}", status="In QA", assignee="Asha")


def rec(commit, ticket, dev, areas, sev=None, subject="PAY-100", epic="PAY-100", repo="PAY/r", hours=0,
        note=("check flags",), fids=(), keys=None):
    return ImpactRecord(
        repo=repo, commit=commit, author_name=dev, author_email=None, committed_at=T0 + timedelta(hours=hours),
        jira_keys=list(keys if keys is not None else ([ticket] if ticket else [])), epic_key=epic,
        subject=subject, areas=list(areas), endpoints=[], migrations=[], config_files=[],
        dependency_changes=[], finding_ids=list(fids), max_severity=Severity(sev) if sev else None,
        test_note=list(note),
    )


class FakeResolver:
    def __init__(self):
        self.calls = []

    def resolve(self, key):
        self.calls.append(key)
        if key == "NOPE-1":
            return None
        return TicketContext(
            key=key, summary={"PAY-100": "Feature flag platform migration"}.get(key, f"summary {key}"),
            description="d", issue_type="Story", status="In QA", acceptance_criteria="- flags load on startup",
            components=[], labels=[], epic_key=None if key == "PAY-100" else "PAY-100", epic_summary=None,
            epic_description=None, linked_titles=[],
        )


def seed(status="ok", extra_version=True):
    db = Database("sqlite://")
    db.create_all()
    records, reports, findings = ImpactRepository(db), ReportRepository(db), FindingRepository(db)
    f = Finding(id="f1", repo="PAY/r", commit="a1b2c3d", author_email=None, jira_keys=["PAY-101"],
                type=FindingType.risky_change, category="swap", severity=Severity.high,
                title="Library swap <b>poller</b>", explanation="why", evidence=[], files=[], suggested_reviewers=[])
    findings.save(1, [f], {}, [])
    recs = [
        rec("a1b2c3d", "PAY-101", "asha", ["Feature flags", "Gateway / Envoy"], "high", hours=2, fids=("f1",)),
        rec("9f8e7d6", "PAY-101", "asha", ["Feature flags"], "medium", hours=1),
        rec("b1", "PAY-103", "ravi", ["Feature flags"], "medium", hours=3),
        rec("c1", "PAY-102", "nimal", ["Checkout API"], hours=4),
        rec("p1", "PAY-7", "zed", ["Misc"], "low", subject="PAY-7", epic=None, hours=5),
        rec("u1", None, "zed", ["Misc"], "low", subject="unassigned/PAY/repo", epic=None, hours=6),
    ]
    records.save(recs)
    epic_recs = [r for r in recs if r.subject == "PAY-100"]
    tickets = [tk(k) for k in ("PAY-101", "PAY-102", "PAY-103", "PAY-104")]
    item = TestScopeItem(type="non_functional", what="Load test gateway", why="streaming", tickets=["PAY-101"])
    c1 = ReportContent(aggregate=aggregate("PAY-100", epic_recs[:2], tickets), test_scope=[item], test_scope_status="ok")
    reports.append("PAY-100", c1, diff_reports(None, c1), 2)
    if extra_version:
        scope = None if status == "pending" else [
            item, TestScopeItem(type="integration", what="Flag defaults together", why="2 tickets", tickets=["PAY-101", "PAY-103"])]
        c2 = ReportContent(aggregate=aggregate("PAY-100", epic_recs, tickets), test_scope=scope, test_scope_status=status)
        reports.append("PAY-100", c2, diff_reports(c1, c2), 4)
    resolver = FakeResolver()
    return PortalQueries(reports, records, findings, resolver), db, resolver
