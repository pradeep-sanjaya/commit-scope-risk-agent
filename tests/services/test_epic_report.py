from datetime import datetime, timezone

import pytest

from scope_agent.domain.impact import ImpactRecord
from scope_agent.domain.models import EpicTicket, Finding, FindingType, Severity, TicketContext
from scope_agent.llm.base import BudgetExceeded, LLMOutputError, LLMResult
from scope_agent.llm.schemas import TestScopeItem, TestScopeVerdict
from scope_agent.persistence.database import Database
from scope_agent.persistence.findings import FindingRepository
from scope_agent.persistence.impact import ImpactRepository
from scope_agent.persistence.reports import ReportRepository
from scope_agent.services.epic_report import EpicReportBuilder
from scope_agent.worker.handlers import EpicReportJobHandler


def rec(commit, ticket, areas=("Feature flags",), fids=()):
    return ImpactRecord(
        repo="PAY/r", commit=commit, author_name="a", author_email=None,
        committed_at=datetime(2026, 1, 1, tzinfo=timezone.utc), jira_keys=[ticket], epic_key="PAY-100",
        subject="PAY-100", areas=list(areas), endpoints=[], migrations=[], config_files=[],
        dependency_changes=[], finding_ids=list(fids), max_severity=None, test_note=["note " + commit],
    )


class FakeTickets:
    def __init__(self, epic):
        self._epic = epic

    def epic_tickets(self, key):
        return self._epic if key == "PAY-100" else []

    def resolve(self, key):
        return TicketContext(key=key, summary="s", description="", issue_type="Task", status="Open",
                             acceptance_criteria="AC " + key, components=[], labels=[], epic_key=None,
                             epic_summary=None, epic_description=None, linked_titles=[])


class FakeLLM:
    def __init__(self, behavior=None):
        self.prompts = []
        self.behavior = behavior

    def analyze(self, system, prompt, schema):
        self.prompts.append(prompt)
        if self.behavior:
            self.behavior()
        item = TestScopeItem(type="integration", what=f"call {len(self.prompts)}", why="y", tickets=["PAY-101"])
        return LLMResult(data=TestScopeVerdict(items=[item]), input_tokens=1, output_tokens=1)


TICKETS = [EpicTicket(key=k, summary="s", status="Open", assignee=None) for k in ("PAY-101", "PAY-102")]


@pytest.fixture
def db():
    d = Database("sqlite://")
    d.create_all()
    return d


@pytest.fixture
def env(db):
    impact = ImpactRepository(db)
    impact.save([rec("c1", "PAY-101", fids=["f1"]), rec("c2", "PAY-102")])
    findings = FindingRepository(db)
    f = Finding(id="f1", repo="PAY/r", commit="c1", author_email=None, jira_keys=["PAY-101"],
                type=FindingType.risky_change, category="caching", severity=Severity.high, title="TTL",
                explanation="e", evidence=[], files=[], suggested_reviewers=[])
    findings.save(1, [f], {}, [])
    return impact, findings, ReportRepository(db)


def build(env, llm, max_tokens=60_000, tickets=TICKETS):
    impact, findings, reports = env
    return EpicReportBuilder(impact, FakeTickets(tickets), findings, reports, llm, max_tokens), reports


def test_rebuild_appends_version_and_skips_when_unchanged(env):
    llm = FakeLLM()
    b, reports = build(env, llm)
    assert b.rebuild("PAY-100") == 1
    assert b.rebuild("PAY-100") is None
    env[0].save([rec("c3", "PAY-101")])
    assert b.rebuild("PAY-100") == 2
    latest = reports.latest("PAY-100")
    assert latest.content.test_scope_status == "ok" and latest.diff.new_commits == 1
    assert "[high] caching: TTL" in llm.prompts[0] and "AC PAY-101" in llm.prompts[0]


def _raise(exc):
    def f():
        raise exc
    return f


def test_budget_exceeded_appends_pending_then_raises_and_recovers(env):
    llm = FakeLLM()
    b, reports = build(env, llm)
    b.rebuild("PAY-100")
    prev = reports.latest("PAY-100").content.test_scope
    llm.behavior = _raise(BudgetExceeded())
    env[0].save([rec("c3", "PAY-101")])
    with pytest.raises(BudgetExceeded):
        b.rebuild("PAY-100")
    latest = reports.latest("PAY-100")
    assert latest.version == 2 and latest.content.test_scope_status == "pending"
    assert latest.content.test_scope == prev and latest.content.aggregate.counts["commits"] == 3
    llm.behavior = None  # budget restored, no new records
    assert b.rebuild("PAY-100") == 3
    assert reports.latest("PAY-100").content.test_scope_status == "ok"
    assert b.rebuild("PAY-100") is None


def test_llm_output_error_marks_error_and_is_retried(env):
    llm = FakeLLM(_raise(LLMOutputError("bad")))
    b, reports = build(env, llm)
    assert b.rebuild("PAY-100") == 1
    c = reports.latest("PAY-100").content
    assert c.test_scope_status == "error" and c.test_scope is None
    llm.behavior = None
    assert b.rebuild("PAY-100") == 2
    assert reports.latest("PAY-100").content.test_scope_status == "ok"


def test_epic_report_job_hitting_budget_is_deferred(env, db):
    from sqlalchemy import select
    from scope_agent.persistence.database import jobs as jobs_t
    from scope_agent.persistence.jobs import JobQueue
    from scope_agent.worker.retry_policy import RetryPolicy
    from scope_agent.worker.worker import Worker
    from tests.persistence.conftest import FakeClock
    b, reports = build(env, FakeLLM(_raise(BudgetExceeded())))
    clock = FakeClock()
    q = JobQueue(db, clock)
    jid = q.enqueue("epic_report", {"subject": "PAY-100"}, "k")
    Worker(q, {"epic_report": EpicReportJobHandler(b)}, env[1], RetryPolicy([30]), 60, "UTC", clock).run_once()
    with db.session() as s:
        assert s.execute(select(jobs_t.c.status).where(jobs_t.c.id == jid)).scalar() == "deferred"
    assert reports.latest("PAY-100").content.test_scope_status == "pending"


def test_falls_back_to_per_ticket_when_over_budget(env):
    llm = FakeLLM()
    b, reports = build(env, llm, max_tokens=1)
    b.rebuild("PAY-100")
    assert len(llm.prompts) == 3  # two tickets + final
    assert "integration: call 1 — y [PAY-101]" in llm.prompts[2]
    assert reports.latest("PAY-100").content.test_scope[0].what == "call 3"


def test_plain_ticket_subject(db):
    impact = ImpactRepository(db)
    r = rec("c1", "PAY-5").model_copy(update={"subject": "PAY-5", "epic_key": None})
    impact.save([r])
    b, reports = build((impact, FindingRepository(db), ReportRepository(db)), FakeLLM())
    assert b.rebuild("PAY-5") == 1
    assert [t.key for t in reports.latest("PAY-5").content.aggregate.tickets] == ["PAY-5"]


def test_handler_rebuilds_payload_subject(env):
    b, reports = build(env, FakeLLM())
    EpicReportJobHandler(b).handle(1, {"subject": "PAY-100"}, 1)
    assert reports.latest("PAY-100").version == 1
