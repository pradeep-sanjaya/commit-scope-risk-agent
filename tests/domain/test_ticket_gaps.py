from datetime import datetime, timezone

import pytest

from scope_agent.domain.models import CommitData, FindingType, Severity, TicketContext
from scope_agent.domain.ticket_gaps import ticket_gaps


def _commit():
    return CommitData(repo="P/r", sha="c1", author_name="A", author_email="a@x.test", message="m",
                      parents=["p"], files=[], committed_at=datetime(2026, 1, 1, tzinfo=timezone.utc))


def _ticket(key="P-1", ac="given/when/then", epic="P-9"):
    return TicketContext(key=key, summary="s", description="d", issue_type="Task", status="x",
                         acceptance_criteria=ac, components=[], labels=[], epic_key=epic,
                         epic_summary=None, epic_description=None, linked_titles=[])


@pytest.mark.parametrize("keys,tickets,quality,category,severity,title", [
    ([], {}, None, "missing_key", Severity.low, "Commit has no JIRA key"),
    (["P-1"], {"P-1": None}, None, "ticket_not_found", Severity.info, "JIRA ticket P-1 not found or not accessible"),
    (["P-1"], {"P-1": _ticket(ac=None)}, None, "missing_ac", Severity.info, "P-1 has no acceptance criteria"),
    (["P-1"], {"P-1": _ticket()}, "vague", "vague_ac", Severity.info, "P-1 acceptance criteria are vague"),
    (["P-1"], {"P-1": _ticket(epic=None)}, None, "no_epic", Severity.info, "P-1 is not linked to an Epic"),
])
def test_gap_rules_table(keys, tickets, quality, category, severity, title):
    found = ticket_gaps(_commit(), keys, tickets, quality, set())
    assert len(found) == 1
    f = found[0]
    assert (f.category, f.severity, f.title) == (category, severity, title)
    assert f.type == FindingType.ticket_gap
    assert f.repo == "P/r" and f.commit == "c1" and f.author_email == "a@x.test" and f.jira_keys == keys


def test_no_gaps_for_complete_ticket():
    assert ticket_gaps(_commit(), ["P-1"], {"P-1": _ticket()}, "ok", set()) == []


def test_gap_reported_once_per_ticket_per_push():
    seen: set = set()
    tickets = {"P-1": _ticket(epic=None)}
    assert len(ticket_gaps(_commit(), ["P-1"], tickets, None, seen)) == 1
    assert ("P-1", "no_epic") in seen
    assert ticket_gaps(_commit(), ["P-1"], tickets, None, seen) == []
    assert len(ticket_gaps(_commit(), [], {}, None, seen)) == 1
    assert len(ticket_gaps(_commit(), [], {}, None, seen)) == 1  # missing_key is per commit
