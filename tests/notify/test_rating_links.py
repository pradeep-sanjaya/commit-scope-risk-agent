import hashlib, hmac

from scope_agent.notify.rating_links import rating_token, rating_url
from tests.notify.helpers import links


def test_rating_token_stable():
    t = rating_token("s", "f1", "useful")
    assert t == hmac.new(b"s", b"f1:useful", hashlib.sha256).hexdigest()[:32]
    assert t == rating_token("s", "f1", "useful") and len(t) == 32
    assert t != rating_token("s", "f1", "noise")


def test_rating_url():
    u = rating_url("https://p/", "s", "f1", "useful")
    assert u == f"https://p/rate/f1?v=useful&t={rating_token('s', 'f1', 'useful')}"


def test_link_builder():
    lb = links()
    assert lb.commit("PAY/gateway", "abc") == "https://bb.example/projects/PAY/repos/gateway/commits/abc"
    assert lb.ticket("PAY-1") == "https://jira.example/browse/PAY-1"
    assert lb.portal_epic("PAY-10") == "https://portal.example/portal/epics/PAY-10"
    assert lb.portal_ticket("PAY-1") == "https://portal.example/portal/tickets/PAY-1"
    assert lb.rating("f1", "noise").startswith("https://portal.example/rate/f1?v=noise&t=")


def test_link_builder_github_commit_links():
    from scope_agent.notify.rating_links import LinkBuilder
    lb = LinkBuilder("https://github.com/", "https://jira.example", "https://portal.example", "s", "github")
    assert lb.commit("acme/payments", "abc") == "https://github.com/acme/payments/commit/abc"


def test_verify_rating_token():
    from scope_agent.notify.rating_links import rating_token, verify_rating_token
    t = rating_token("s", "f1", "useful")
    assert verify_rating_token("s", "f1", "useful", t) is True
    assert verify_rating_token("s", "f1", "noise", t) is False
    assert verify_rating_token("s", "f1", "useful", "é✓") is False
