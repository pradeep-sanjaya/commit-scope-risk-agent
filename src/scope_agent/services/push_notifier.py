from scope_agent.adapters.smtp import EmailSender
from scope_agent.domain.models import Severity
from scope_agent.domain.routing import RoutingConfig, push_recipients, reviewers_for
from scope_agent.notify.push_email import render_push, should_send_push
from scope_agent.notify.rating_links import LinkBuilder
from scope_agent.services.pipeline import PushResult


class PushNotifier:
    def __init__(self, routing: RoutingConfig, sender: EmailSender, links: LinkBuilder):
        self._routing = routing
        self._sender = sender
        self._links = links

    def notify(self, result: PushResult) -> None:
        for f in result.findings:
            f.suggested_reviewers = reviewers_for(f, self._routing)
        if not should_send_push(result):
            return
        flagged = [f for f in result.findings if f.severity.rank >= Severity.low.rank]
        to = push_recipients(flagged, result.commits, self._routing)
        self._sender.send(render_push(result, self._links), to)
