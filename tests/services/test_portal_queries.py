from scope_agent.services.portal_queries import NO_EPIC, UNASSIGNED
from tests.portal_helpers import seed


def test_epic_list_rows_and_synthetic_rows():
    q, _, _ = seed()
    rows = q.epic_list(None, None, False)
    assert [r.subject for r in rows] == ["PAY-100", NO_EPIC, UNASSIGNED]
    epic = rows[0]
    assert epic.title == "Feature flag platform migration"
    assert (epic.tickets, epic.developers, epic.areas, epic.overlaps) == (4, 3, 3, 1)
    assert epic.severity_counts["high"] == 1 and epic.new_commits_since_prev == 2
    assert epic.last_change is not None
    assert rows[1].title == "Tickets without an Epic" and rows[1].tickets == 1
    assert rows[2].title == "Commits without a ticket" and rows[2].areas == 1


def test_epic_list_filters():
    q, _, _ = seed()
    assert [r.subject for r in q.epic_list(None, "PAY", True)] == ["PAY-100"]
    assert q.epic_list(None, "OTHER", False) == []


def test_epic_list_search_matches_area_name():
    q, _, _ = seed()
    assert [r.subject for r in q.epic_list("checkout api", None, False)] == ["PAY-100"]
    assert [r.subject for r in q.epic_list("feature flag platform", None, False)] == ["PAY-100"]
    assert [r.subject for r in q.epic_list("pay-103", None, False)] == ["PAY-100"]
    assert [r.subject for r in q.epic_list("PAY/r", None, False)][0] == "PAY-100"
    assert q.epic_list("zzz-nothing", None, False) == []


def test_epic_page_specific_version():
    q, _, _ = seed()
    assert q.epic_page("PAY-100", None).report.version == 2
    page = q.epic_page("PAY-100", 1)
    assert page.report.version == 1 and [v.version for v in page.versions] == [1, 2]
    assert page.title == "Feature flag platform migration"
    assert q.epic_page("PAY-100", 9) is None and q.epic_page("PAY-999", None) is None


def test_ticket_page_records_newest_first():
    q, _, _ = seed()
    page = q.ticket_page("PAY-101")
    assert [r.commit for r in page.records] == ["a1b2c3d", "9f8e7d6"]
    assert page.areas == ["Feature flags", "Gateway / Envoy"]
    assert [f.id for f in page.findings] == ["f1"] and page.ticket.key == "PAY-101"
    assert q.ticket_page("NOPE-1") is None


def test_matrix_cell():
    q, _, _ = seed()
    assert [r.commit for r in q.matrix_cell("PAY-100", "Feature flags", "PAY-103")] == ["b1"]
    assert q.matrix_cell("PAY-100", "Checkout API", "PAY-103") == []
