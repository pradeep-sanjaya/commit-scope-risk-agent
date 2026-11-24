from email.message import EmailMessage

from scope_agent.domain.models import Severity
from scope_agent.notify.rating_links import LinkBuilder
from scope_agent.notify.rendering import build_message
from scope_agent.services.pipeline import PushResult


def _flagged(result: PushResult):
    flagged = [f for f in result.findings if f.severity.rank >= Severity.low.rank]
    return sorted(flagged, key=lambda f: -f.severity.rank)


def should_send_push(result: PushResult) -> bool:
    return bool(_flagged(result)) or any(result.summaries.values())


def render_push(result: PushResult, links: LinkBuilder) -> EmailMessage:
    flagged = _flagged(result)
    summaries = [s for group in result.summaries.values() for s in group]
    job = result.job
    subject = (
        f"[Scope Agent] {len(flagged) + len(summaries)} item(s) for review: "
        f"{job.repo} {job.branch} ({(job.from_hash or '')[:7]}..{job.to_hash[:7]})"
    )
    items = [
        {
            "finding": f,
            "commit_url": links.commit(f.repo, f.commit),
            "ticket_links": [(k, links.ticket(k)) for k in f.jira_keys],
            "useful_url": links.rating(f.id, "useful"),
            "noise_url": links.rating(f.id, "noise"),
        }
        for f in flagged
    ]
    return build_message(subject, "push", {"items": items, "summaries": summaries})
