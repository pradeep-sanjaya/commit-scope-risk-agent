from datetime import datetime, timezone
from email.message import EmailMessage

from scope_agent.domain.models import Finding, Severity
from scope_agent.notify.rating_links import LinkBuilder
from scope_agent.notify.rendering import build_message

NO_TICKET = "No ticket"


def render_digest(
    findings: list[Finding], errors: int, deferred: int,
    start: datetime, end: datetime, links: LinkBuilder,
) -> EmailMessage:
    high = sum(1 for f in findings if f.severity is Severity.high)
    date = end.astimezone(timezone.utc).strftime("%Y-%m-%d")
    subject = f"[Scope Agent] Daily digest {date}: {high} high, {len(findings)} findings"

    grouped: dict[str, dict[str, list[dict]]] = {}
    for f in sorted(findings, key=lambda f: -f.severity.rank):
        entry = {"finding": f, "commit_url": links.commit(f.repo, f.commit)}
        grouped.setdefault(f.repo, {}).setdefault(f.jira_keys[0] if f.jira_keys else NO_TICKET, []).append(entry)
    repos = [
        {"repo": repo, "tickets": [
            {"key": key, "url": links.ticket(key) if key != NO_TICKET else None, "entries": items}
            for key, items in tickets.items()
        ]}
        for repo, tickets in grouped.items()
    ]

    def count(category: str) -> int:
        return sum(1 for f in findings if f.category == category)

    ctx = {
        "repos": repos, "total": len(findings), "high": high, "errors": errors, "deferred": deferred,
        "start": start.astimezone(timezone.utc).isoformat(), "end": end.astimezone(timezone.utc).isoformat(),
        "missing_key": count("missing_key"), "missing_ac": count("missing_ac"), "vague_ac": count("vague_ac"),
    }
    return build_message(subject, "digest", ctx)
