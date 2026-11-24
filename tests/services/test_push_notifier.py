from scope_agent.domain.models import Severity
from scope_agent.domain.routing import RepoRouting, RoutingConfig
from scope_agent.services.push_notifier import PushNotifier
from tests.notify.helpers import commit, finding, links, result, summary


class Rec:
    def __init__(self):
        self.sent = []

    def send(self, msg, to):
        self.sent.append((msg, to))


ROUTING = RoutingConfig(
    repos={"PAY/gateway": RepoRouting(leads=["gw@x.io"])}, categories={}, default_leads=["d@x.io"])


def test_notifier_routes_to_gateway_owner_for_connection_model():
    f = finding(reviewers=(), category="connection_model")
    rec = Rec()
    PushNotifier(ROUTING, rec, links()).notify(result([f]))
    assert f.suggested_reviewers == ["gw@x.io"]
    (msg, to), = rec.sent
    assert to == ["dev@x.io", "gw@x.io"]


def test_notifier_skips_info_only():
    rec = Rec()
    PushNotifier(ROUTING, rec, links()).notify(result([finding(severity=Severity.info)]))
    assert rec.sent == []


def test_notifier_summary_only_sends_without_info_authors():
    rec = Rec()
    PushNotifier(ROUTING, rec, links()).notify(result([finding(severity=Severity.info)], {"c": [summary()]}))
    (msg, to), = rec.sent
    assert "dev@x.io" not in to
