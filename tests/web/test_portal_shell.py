import base64
import hashlib
import re

from scope_agent.web.portal import TABS
from tests.web.test_portal import client

HTMX_SHA384 = "ujb1lZYygJmzgSwoxRggbCHcjc0rB2XoQrxeTUQyRjrOnlCoYta87iKBWq3EsdM2"
PAGES = ["/portal", "/portal/epics/PAY-100", "/portal/tickets", "/portal/tickets/PAY-101",
         *[f"/portal/epics/PAY-100?tab={t}" for t in TABS]]


def _current_section(html: str) -> list[str]:
    return re.findall(r'<a [^>]*aria-current="page"[^>]*>([^<]+)</a>', html.split("</nav>")[0])


def test_top_nav_marks_the_current_section():
    c = client()
    assert _current_section(c.get("/portal").text) == ["Epics"]
    assert _current_section(c.get("/portal/epics/PAY-100").text) == ["Epics"]
    assert _current_section(c.get("/portal/tickets").text) == ["Tickets"]
    assert _current_section(c.get("/portal/tickets/PAY-101").text) == ["Tickets"]


def test_tickets_page_lists_tickets_with_commits():
    c = client()
    html = c.get("/portal/tickets").text
    keys = re.findall(r'<a class="key" href="/portal/tickets/([A-Z0-9-]+)"', html)
    assert keys == ["PAY-7", "PAY-102", "PAY-103", "PAY-101"]
    assert "summary PAY-101" in html and 'href="/portal/epics/PAY-100"' in html
    assert 'class="badge b-high"' in html and 'class="chip">Gateway / Envoy<' in html
    assert '<select name="project"' in html and "High risk only" in html
    filtered = c.get("/portal/tickets", params={"high": "1"}).text
    assert re.findall(r'<a class="key" href="/portal/tickets/([A-Z0-9-]+)"', filtered) == ["PAY-101"]
    assert "No tickets match these filters." in c.get("/portal/tickets", params={"q": "zzz"}).text


def test_pages_load_nothing_external():
    c = client()
    for page in PAGES:
        html = c.get(page).text
        assert re.search(r'(src|href)="https?://', html) is None, page
        assert "unpkg" not in html, page


def test_vendored_htmx_matches_pinned_hash():
    c = client()
    js = c.get("/portal/static/htmx.min.js")
    assert js.status_code == 200
    assert base64.b64encode(hashlib.sha384(js.content).digest()).decode() == HTMX_SHA384
    assert 'src="/portal/static/htmx.min.js"' in c.get("/portal").text


def test_top_bar_has_brand_nav_and_search():
    html = client().get("/portal").text
    assert 'class="topbar"' in html and "QA Impact" in html
    assert re.search(r'<a [^>]*aria-current="page"[^>]*>Epics</a>', html)
    assert re.search(r'<form [^>]*action="/portal/search"', html)


def test_search_ticket_key_redirects():
    c = client()
    hit = c.get("/portal/search?q=pay-101", follow_redirects=False)
    assert hit.status_code == 303 and hit.headers["location"] == "/portal/tickets/PAY-101"
    for q in ("NOPE-1", "checkout", "PAY-104"):  # unknown key, a word, a ticket without commits
        miss = c.get(f"/portal/search?q={q}", follow_redirects=False)
        assert miss.status_code == 303 and miss.headers["location"] == f"/portal?q={q}", q
