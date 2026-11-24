from datetime import datetime, timezone

from scope_agent.domain.epic_report import (
    Aggregate, MatrixCell, ReportContent, aggregate, diff_reports,
)
from scope_agent.domain.impact import Endpoint, ImpactRecord
from scope_agent.domain.models import DependencyChange, EpicTicket, Severity
from scope_agent.llm.schemas import TestScopeItem


def t(key):
    return EpicTicket(key=key, summary=f"s {key}", status="Open", assignee=None)


def rec(commit, ticket, dev, areas, sev=None, swap=False, migrations=(), repo="PAY/r"):
    deps = []
    if swap:
        deps = [DependencyChange(ecosystem="maven", name="a", old_version="1", new_version=None, kind="removed", file="pom.xml"),
                DependencyChange(ecosystem="maven", name="b", old_version=None, new_version="1", kind="added", file="pom.xml")]
    return ImpactRecord(
        repo=repo, commit=commit, author_name=dev, author_email=None,
        committed_at=datetime(2026, 1, 1, tzinfo=timezone.utc), jira_keys=[ticket], epic_key="PAY-100",
        subject="PAY-100", areas=list(areas), endpoints=[], migrations=list(migrations), config_files=[],
        dependency_changes=deps, finding_ids=[], max_severity=Severity(sev) if sev else None, test_note=[],
    )


def _recs():
    return [rec("a1", "PAY-101", "asha", ["Feature flags", "Gateway / Envoy"], sev="high", swap=True),
            rec("b1", "PAY-103", "ravi", ["Feature flags"], sev="medium"),
            rec("c1", "PAY-102", "nimal", ["Checkout API"], migrations=["V42__x.sql"])]


def test_aggregate_overlap_matrix_and_counts():
    agg = aggregate("PAY-100", _recs(), [t("PAY-101"), t("PAY-102"), t("PAY-103"), t("PAY-104")])
    assert agg.overlaps == ["Feature flags"]
    assert agg.area_rows[0].area in {"Feature flags", "Gateway / Envoy"} and agg.area_rows[0].max_severity == "high"
    assert MatrixCell(area="Feature flags", ticket="PAY-103", commits=1, max_severity="medium") in agg.matrix
    assert agg.counts == {"tickets": 4, "developers": 3, "repos": 1, "commits": 3}
    assert "DB migration" in next(r for r in agg.area_rows if r.area == "Checkout API").change_kinds
    assert "library swap" in next(r for r in agg.area_rows if r.area == "Gateway / Envoy").change_kinds
    assert agg.severity_counts["high"] == 1


def test_same_developer_two_tickets_overlaps_and_email_identity():
    a = rec("a", "PAY-1", "x", ["A"]); b = rec("b", "PAY-2", "y", ["B"]); c = rec("c", "PAY-1", "z", ["B"])
    a.author_email = "Dev@X.com"; c.author_email = "dev@x.com"
    agg = aggregate("S", [a, b, c], [])
    assert agg.counts["developers"] == 2
    assert agg.overlaps == ["B"]


def _content(agg, items=None):
    return ReportContent(aggregate=agg, test_scope=items, test_scope_status="ok")


def test_diff_reports_new_items():
    full = aggregate("PAY-100", _recs(), [])
    prev = aggregate("PAY-100", [_recs()[0], _recs()[2]], [])
    item = TestScopeItem(type="functional", what="w", why="y", tickets=["PAY-1"])
    d = diff_reports(_content(prev, []), _content(full, [item]))
    assert d.new_overlaps == ["Feature flags"] and d.new_commits == 1 and d.new_areas == []
    assert d.new_test_items == [item]
    first = diff_reports(None, _content(full, [item]))
    assert first.new_commits == 3 and "Checkout API" in first.new_areas
