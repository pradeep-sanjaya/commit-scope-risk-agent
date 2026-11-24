from fastapi.testclient import TestClient

from scope_agent.persistence.jobs import JobQueue
from scope_agent.persistence.ratings import RatingRepository
from scope_agent.web.app import create_app
from tests.portal_helpers import seed


def client(**kw):
    q, db, resolver = seed(**kw)
    app = create_app("w", ["development"], JobQueue(db), RatingRepository(db), "rsecret", portal_queries=q)
    return TestClient(app)


def _highlighted_tabs(html: str) -> list[str]:
    import re
    nav = re.search(r'<nav class="tabs">(.*?)</nav>', html, re.S)
    assert nav, "tab nav missing"
    return re.findall(r'<a [^>]*class="on"[^>]*>([^<]+)</a>', nav.group(1))


def test_tab_switch_moves_the_highlight():
    """htmx swaps the response in; it must carry the nav so the clicked tab is highlighted."""
    c = client()
    for tab, label in [("tickets", "Tickets"), ("test-scope", "Test scope"), ("risks", "Risks"),
                       ("history", "History"), ("areas-list", "Impacted areas"),
                       ("areas-matrix", "Impacted areas")]:
        partial = c.get(f"/portal/epics/PAY-100/tab/{tab}")
        assert partial.status_code == 200
        assert '<div id="tabs"' in partial.text and "<html" not in partial.text
        assert _highlighted_tabs(partial.text) == [label], tab
        assert _highlighted_tabs(c.get(f"/portal/epics/PAY-100?tab={tab}").text) == [label], tab


def test_tab_links_swap_the_whole_tab_region():
    page = client().get("/portal/epics/PAY-100").text
    assert page.count('hx-target="#tabs"') >= 7  # five tabs + the List/Matrix toggle
    assert 'hx-target="#tab-body"' not in page


def test_portal_routes_render():
    c = client()
    home = c.get("/portal")
    assert home.status_code == 200 and "PAY-100" in home.text and "Tickets without an Epic" in home.text
    assert "htmx.org@1.9.12" in home.text and "sha384-" in home.text
    epic = c.get("/portal/epics/PAY-100")
    assert epic.status_code == 200 and "Feature flags" in epic.text and 'class="ov"' in epic.text
    assert "Report v2" in epic.text and "+2 commits since v1" in epic.text
    assert "new-badge" in epic.text  # Gateway / Envoy is new in v2
    matrix = c.get("/portal/epics/PAY-100/tab/areas-matrix")
    assert matrix.status_code == 200 and "ticket=PAY-103" in matrix.text
    scope = c.get("/portal/epics/PAY-100/tab/test-scope")
    assert "non_functional" in scope.text and "New in v2" in scope.text
    ticket = c.get("/portal/tickets/PAY-101")
    assert ticket.status_code == 200 and "a1b2c3d" in ticket.text
    assert "&lt;b&gt;poller&lt;/b&gt;" in ticket.text and "<b>poller</b>" not in ticket.text
    assert c.get("/portal/static/portal.css").status_code == 200


def test_every_tab_works_without_js():
    c = client()
    for tab, needle in [("areas-matrix", "ticket=PAY-103"), ("tickets", "summary PAY-102"),
                        ("test-scope", "non_functional"), ("risks", "Library swap"), ("history", "v1")]:
        r = c.get("/portal/epics/PAY-100", params={"tab": tab})
        assert r.status_code == 200 and needle in r.text and "<html" in r.text, tab
    assert c.get("/portal/epics/PAY-100", params={"tab": "bogus"}).status_code == 404
    assert c.get("/portal/epics/PAY-100/tab/bogus").status_code == 404
    partial = c.get("/portal/epics/PAY-100/tab/risks")
    assert "<html" not in partial.text


def test_version_param_and_cell():
    c = client()
    assert "Report v1" in c.get("/portal/epics/PAY-100", params={"v": 1}).text
    assert c.get("/portal/epics/PAY-100", params={"v": 9}).status_code == 404
    cell = c.get("/portal/epics/PAY-100/cell", params={"area": "Feature flags", "ticket": "PAY-103"})
    assert cell.status_code == 200 and "b1" in cell.text and "check flags" in cell.text


def test_pending_scope_banner():
    c = client(status="pending")
    assert "Test scope pending (LLM budget)" in c.get("/portal/epics/PAY-100").text
    assert "Test scope pending (LLM budget)" not in client().get("/portal/epics/PAY-100").text


def test_unknown_subject_404():
    c = client()
    assert c.get("/portal/epics/PAY-999").status_code == 404
    assert c.get("/portal/epics/PAY-999/tab/risks").status_code == 404
    assert c.get("/portal/tickets/NOPE-1").status_code == 404


def test_search_and_filters_via_http():
    c = client()
    assert "PAY-100" in c.get("/portal", params={"q": "checkout"}).text
    assert "PAY-100" not in c.get("/portal", params={"q": "zzz"}).text
    assert "PAY-100" in c.get("/portal", params={"high": "1"}).text
