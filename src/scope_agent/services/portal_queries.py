"""Read models for the QA portal. Read-only; composes repositories, owns no storage."""

from datetime import datetime

import structlog
from pydantic import BaseModel

from scope_agent.domain.epic_report import developer
from scope_agent.domain.impact import ImpactRecord
from scope_agent.domain.models import Finding, Severity, TicketContext
from scope_agent.persistence.findings import FindingRepository
from scope_agent.persistence.impact import ImpactRepository
from scope_agent.persistence.reports import ReportRepository, StoredReport, StoredReportMeta
from scope_agent.services.ticket_resolver import TicketResolver

log = structlog.get_logger()

NO_EPIC = "_no_epic"
UNASSIGNED = "_unassigned"
NO_EPIC_TITLE = "Tickets without an Epic"
UNASSIGNED_TITLE = "Commits without a ticket"
UNASSIGNED_PREFIX = "unassigned/"


class EpicListRow(BaseModel):
    subject: str
    title: str
    tickets: int
    developers: int
    areas: int
    overlaps: int
    severity_counts: dict[str, int]
    last_change: datetime | None
    new_commits_since_prev: int


class EpicPage(BaseModel):
    subject: str
    title: str
    report: StoredReport
    versions: list[StoredReportMeta]


class TicketPage(BaseModel):
    ticket: TicketContext
    areas: list[str]
    findings: list[Finding]
    records: list[ImpactRecord]


def _severity_counts(records: list[ImpactRecord]) -> dict[str, int]:
    counts = {s.value: 0 for s in Severity}
    for r in records:
        if r.max_severity:
            counts[r.max_severity.value] += 1
    return counts


def _newest_first(records: list[ImpactRecord]) -> list[ImpactRecord]:
    return sorted(records, key=lambda r: (r.committed_at, r.repo, r.commit), reverse=True)


def _matches(query: str, *haystack: str) -> bool:
    q = query.lower()
    return any(q in h.lower() for h in haystack)


class PortalQueries:
    def __init__(self, reports: ReportRepository, records: ImpactRepository,
                 findings: FindingRepository, tickets: TicketResolver):
        self._reports = reports
        self._records = records
        self._findings = findings
        self._tickets = tickets

    def _title(self, subject: str) -> str:
        try:
            ctx = self._tickets.resolve(subject)
        except Exception as exc:  # JIRA being down must not take the portal down
            log.warning("portal_title_unavailable", subject=subject, error=str(exc))
            return subject
        return ctx.summary if ctx and ctx.summary else subject

    def _synthetic(self, subject: str, title: str, tickets: int, records: list[ImpactRecord]) -> EpicListRow:
        return EpicListRow(
            subject=subject, title=title, tickets=tickets,
            developers=len({developer(r) for r in records}),
            areas=len({a for r in records for a in r.areas}), overlaps=0,
            severity_counts=_severity_counts(records),
            last_change=max((r.committed_at for r in records), default=None), new_commits_since_prev=0,
        )

    def epic_list(self, query: str | None, project: str | None, high_only: bool) -> list[EpicListRow]:
        epics: list[tuple[EpicListRow, list[str]]] = []
        plain: list[ImpactRecord] = []
        plain_subjects: list[str] = []
        unassigned: list[ImpactRecord] = []
        for subject in self._records.subjects():
            recs = self._records.for_subject(subject)
            if subject.startswith(UNASSIGNED_PREFIX):
                unassigned.extend(recs)
            elif not any(r.epic_key for r in recs):
                plain.extend(recs)
                plain_subjects.append(subject)
            else:
                report = self._reports.latest(subject)
                if report is None:
                    continue
                agg = report.content.aggregate
                row = EpicListRow(
                    subject=subject, title=self._title(subject), tickets=agg.counts.get("tickets", 0),
                    developers=agg.counts.get("developers", 0), areas=len(agg.area_rows),
                    overlaps=len(agg.overlaps), severity_counts=agg.severity_counts,
                    last_change=self._records.latest_change(subject),
                    new_commits_since_prev=report.diff.new_commits,
                )
                keys = [t.key for t in agg.tickets] + [c.ticket for c in agg.matrix]
                epics.append((row, [subject, row.title, *keys, *(r.area for r in agg.area_rows),
                                    *{r.repo for r in recs}]))
        epics.sort(key=lambda e: (e[0].last_change is not None, e[0].last_change), reverse=True)

        rows = [(row, hay) for row, hay in epics if not project or row.subject.split("-")[0] == project]
        if not project:
            if plain:
                rows.append((self._synthetic(NO_EPIC, NO_EPIC_TITLE, len(plain_subjects), plain),
                             [NO_EPIC_TITLE, *plain_subjects, *(a for r in plain for a in r.areas),
                              *{r.repo for r in plain}]))
            if unassigned:
                rows.append((self._synthetic(UNASSIGNED, UNASSIGNED_TITLE, 0, unassigned),
                             [UNASSIGNED_TITLE, *(a for r in unassigned for a in r.areas),
                              *{r.repo for r in unassigned}]))
        return [
            row for row, hay in rows
            if (not high_only or row.severity_counts.get("high", 0) > 0) and (not query or _matches(query, *hay))
        ]

    def epic_page(self, subject: str, version: int | None) -> EpicPage | None:
        report = self._reports.latest(subject) if version is None else self._reports.get(subject, version)
        if report is None:
            return None
        return EpicPage(subject=subject, title=self._title(subject), report=report,
                        versions=self._reports.versions(subject))

    def matrix_cell(self, subject: str, area: str, ticket: str) -> list[ImpactRecord]:
        return _newest_first([
            r for r in self._records.for_subject(subject) if area in r.areas and ticket in r.jira_keys
        ])

    def epic_risks(self, subject: str) -> list[Finding]:
        """Findings behind the Epic's records, highest severity first."""
        ids = dict.fromkeys(f for r in self._records.for_subject(subject) for f in r.finding_ids)
        found = [f for fid in ids if (f := self._findings.get(fid))]
        return sorted(found, key=lambda f: -f.severity.rank)

    def ticket_page(self, key: str) -> TicketPage | None:
        records = _newest_first(self._records.for_ticket(key))
        try:
            ticket = self._tickets.resolve(key)
        except Exception as exc:
            log.warning("portal_ticket_unavailable", key=key, error=str(exc))
            ticket = None
        if ticket is None and not records:
            return None
        if ticket is None:  # JIRA has no such issue (or is unreachable) but we hold commits for it
            ticket = TicketContext(key=key, summary="", description="", issue_type="", status="",
                                   acceptance_criteria=None, components=[], labels=[], epic_key=None,
                                   epic_summary=None, epic_description=None, linked_titles=[])
        ids = dict.fromkeys(f for r in records for f in r.finding_ids)
        found = [f for fid in ids if (f := self._findings.get(fid))]
        return TicketPage(
            ticket=ticket, areas=list(dict.fromkeys(a for r in reversed(records) for a in r.areas)),
            findings=sorted(found, key=lambda f: -f.severity.rank), records=records,
        )
