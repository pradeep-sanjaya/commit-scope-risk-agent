from datetime import datetime, timezone

from scope_agent.domain.models import Severity
from scope_agent.notify.digest_email import render_digest
from tests.notify.helpers import finding, links

END = datetime(2026, 3, 5, 8, tzinfo=timezone.utc)
START = datetime(2026, 3, 4, 8, tzinfo=timezone.utc)


def test_digest_subject_and_counts():
    fs = [finding(f"h{i}", Severity.high) for i in range(2)]
    fs += [finding("g1", Severity.low, category="missing_key", keys=()),
           finding("g2", Severity.info, category="missing_ac"),
           finding("g3", Severity.info, category="vague_ac", repo="PAY/other")]
    msg = render_digest(fs, 1, 2, START, END, links())
    assert msg["Subject"] == "[Scope Agent] Daily digest 2026-03-05: 2 high, 5 findings"
    text = msg.get_body(("plain",)).get_content()
    assert "Analysis errors: 1" in text and "Deferred (budget): 2" in text
    assert "No ticket" in text and "PAY-12" in text and "PAY/other" in text
    assert "Commits without a key: 1" in text
    assert "Tickets with missing ACs: 1" in text and "Tickets with vague ACs: 1" in text
    assert "Deferred (budget): 2" in msg.get_body(("html",)).get_content()


def test_digest_zero_findings():
    msg = render_digest([], 0, 0, START, END, links())
    assert msg["Subject"].endswith("0 high, 0 findings")


def test_digest_links_ticket_and_epic_pages():
    msg = render_digest([finding("h", Severity.high)], 0, 0, START, END, links(), epics={"PAY-12": "PAY-10"})
    text = msg.get_body(("plain",)).get_content()
    assert "https://portal.example/portal/epics/PAY-10" in text
    assert "https://portal.example/portal/tickets/PAY-12" in text
    plain = render_digest([finding("h", Severity.high)], 0, 0, START, END, links())
    assert "/portal/epics/" not in plain.get_body(("plain",)).get_content()
