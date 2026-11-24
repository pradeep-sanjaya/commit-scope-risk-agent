"""Ticket-quality gap rules for one commit. Pure: no I/O."""

from scope_agent.domain.models import CommitData, Finding, FindingType, Severity, TicketContext

_MISSING_KEY_EXPLANATION = (
    "Without a ticket key the change can't be checked against planned scope or linked for QA."
)


def _gap(commit: CommitData, keys: list[str], category: str, title: str, explanation: str) -> Finding:
    return Finding(
        repo=commit.repo, commit=commit.sha, author_email=commit.author_email, jira_keys=list(keys),
        type=FindingType.ticket_gap, category=category, severity=Severity.low if category == "missing_key" else Severity.info,
        title=title, explanation=explanation, evidence=[], files=[], suggested_reviewers=[],
    )


def ticket_gaps(
    commit: CommitData,
    keys: list[str],
    tickets: dict[str, TicketContext | None],
    ac_quality: str | None,
    already_reported: set[tuple[str, str]],
) -> list[Finding]:
    """Gap findings for one commit; `already_reported` (ticket, category) is updated in place."""
    if not keys:
        return [_gap(commit, keys, "missing_key", "Commit has no JIRA key", _MISSING_KEY_EXPLANATION)]
    found: list[Finding] = []

    def report(key: str, category: str, title: str, explanation: str) -> None:
        if (key, category) in already_reported:
            return
        already_reported.add((key, category))
        found.append(_gap(commit, keys, category, title, explanation))

    for key in keys:
        ticket = tickets.get(key)
        if ticket is None:
            report(key, "ticket_not_found", f"JIRA ticket {key} not found or not accessible",
                   "The ticket could not be read, so the change can't be compared with its planned scope.")
            continue
        if ticket.acceptance_criteria is None:
            report(key, "missing_ac", f"{key} has no acceptance criteria",
                   "Without acceptance criteria the change can't be checked against what was agreed.")
        if ac_quality == "vague":
            report(key, "vague_ac", f"{key} acceptance criteria are vague",
                   "Vague acceptance criteria make it hard to confirm the change does what was intended.")
        if ticket.epic_key is None:
            report(key, "no_epic", f"{key} is not linked to an Epic",
                   "Without an Epic link the change can't be rolled up into Epic-level reporting.")
    return found
