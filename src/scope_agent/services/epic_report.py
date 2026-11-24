import json

import structlog

from scope_agent.domain.epic_report import Aggregate, ReportContent, aggregate, diff_reports
from scope_agent.domain.impact import ImpactRecord
from scope_agent.domain.models import EpicTicket, Finding, TicketContext
from scope_agent.llm.base import BudgetExceeded, LLMClient, LLMOutputError
from scope_agent.llm.prompts import render_prompt
from scope_agent.llm.schemas import TestScopeItem, TestScopeVerdict
from scope_agent.persistence.findings import FindingRepository
from scope_agent.persistence.impact import ImpactRepository
from scope_agent.persistence.reports import ReportRepository
from scope_agent.services.ticket_resolver import TicketResolver

log = structlog.get_logger()

SYSTEM = "You are a QA lead proposing a consolidated test scope. Output only via the report tool."


def _summary(agg: Aggregate) -> str:
    return json.dumps({
        "counts": agg.counts, "severity_counts": agg.severity_counts, "overlaps": agg.overlaps,
        "areas": [r.model_dump(mode="json") for r in agg.area_rows],
    }, indent=1)


def _format_item(i: TestScopeItem) -> str:
    return f"{i.type}: {i.what} — {i.why} [{', '.join(i.tickets)}]"


class EpicReportBuilder:
    def __init__(self, records: ImpactRepository, tickets: TicketResolver, findings: FindingRepository,
                 reports: ReportRepository, llm: LLMClient, max_input_tokens: int):
        self._records = records
        self._tickets = tickets
        self._findings = findings
        self._reports = reports
        self._llm = llm
        self._max_tokens = max_input_tokens

    def rebuild(self, subject: str) -> int | None:
        records = self._records.for_subject(subject)
        prev = self._reports.latest(subject)
        if prev and prev.record_count == len(records) and prev.content.test_scope_status == "ok":
            return None
        tickets = self._epic_tickets(subject)
        contexts = {t.key: self._tickets.resolve(t.key) for t in tickets}
        findings = self._load_findings(records)
        agg = aggregate(subject, records, tickets)
        items, status = self._test_scope(subject, agg, records, contexts, findings,
                                         prev.content.test_scope if prev else None)
        content = ReportContent(aggregate=agg, test_scope=items, test_scope_status=status)
        diff = diff_reports(prev.content if prev else None, content)
        version = self._reports.append(subject, content, diff, len(records))
        if status == "pending":
            raise BudgetExceeded(f"epic report {subject} v{version} awaiting test scope")  # worker defers the job
        return version

    def _epic_tickets(self, subject: str) -> list[EpicTicket]:
        epic = self._tickets.epic_tickets(subject)
        if epic:
            return epic
        ctx = self._tickets.resolve(subject)
        return [EpicTicket(key=ctx.key, summary=ctx.summary, status=ctx.status, assignee=None)] if ctx else []

    def _load_findings(self, records: list[ImpactRecord]) -> dict[str, Finding]:
        found = {}
        for fid in dict.fromkeys(f for r in records for f in r.finding_ids):
            f = self._findings.get(fid)
            if f:
                found[fid] = f
        return found

    def _prompt(self, subject: str, agg: Aggregate, records: list[ImpactRecord],
                contexts: dict[str, TicketContext | None], findings: dict[str, Finding],
                extra_notes: list[str] | None = None) -> str:
        fids = dict.fromkeys(f for r in records for f in r.finding_ids)
        return render_prompt(
            "test_scope", subject=subject, aggregate=_summary(agg),
            tickets=[{"key": t.key, "summary": t.summary,
                      "acceptance_criteria": c.acceptance_criteria if (c := contexts.get(t.key)) else None}
                     for t in agg.tickets],
            test_notes=[n for r in records for n in r.test_note] + (extra_notes or []),
            findings=[{"severity": findings[f].severity.value, "category": findings[f].category,
                       "title": findings[f].title} for f in fids if f in findings],
        )

    def _ask(self, prompt: str) -> list[TestScopeItem]:
        verdict = self._llm.analyze(SYSTEM, prompt, TestScopeVerdict).data
        assert isinstance(verdict, TestScopeVerdict)
        return verdict.items

    def _test_scope(self, subject, agg, records, contexts, findings, previous):
        try:
            prompt = self._prompt(subject, agg, records, contexts, findings)
            if len(prompt) // 4 <= self._max_tokens:
                return self._ask(prompt), "ok"
            collected: list[TestScopeItem] = []
            for t in agg.tickets:
                mine = [r for r in records if t.key in r.jira_keys]
                sub = aggregate(subject, mine, [t])
                collected += self._ask(self._prompt(subject, sub, mine, contexts, findings))
            notes = [_format_item(i) for i in collected]
            return self._ask(self._prompt(subject, agg, [], contexts, findings, notes)), "ok"
        except BudgetExceeded:
            log.info("epic_report_budget_exceeded", subject=subject)
            return previous, "pending"
        except LLMOutputError as e:
            log.warning("epic_report_llm_failed", subject=subject, error=str(e))
            return None, "error"
