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
    def __init__(self, epic, children=None, unknown=()):
        self._epic = epic
        self._children = children or {}
        self._unknown = set(unknown)

    def epic_tickets(self, key):
        return self._epic if key == "PAY-100" else self._children.get(key, [])

    def resolve(self, key):
        if key in self._unknown:
            return None
        issue_type = "Epic" if key == "PAY-100" else "Task"
        return TicketContext(key=key, summary="s", description="", issue_type=issue_type, status="Open",
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


def _full_prompt_tokens(b, subject="PAY-100"):
    from scope_agent.domain.epic_report import aggregate
    records = b._records.for_subject(subject)
    tickets = b._epic_tickets(subject)
    contexts = {t.key: b._tickets.resolve(t.key) for t in tickets}
    prompt = b._prompt(subject, aggregate(subject, records, tickets), records, contexts, b._load_findings(records))
    return len(prompt) // 4


def _long_note_commit(commit="c9", ticket="PAY-101"):
    """Raw commit notes make the full prompt much larger than the consolidation prompt."""
    return rec(commit, ticket).model_copy(update={"test_note": ["long " + "x" * 4000]})


def test_falls_back_to_per_ticket_when_over_budget(env):
    env[0].save([_long_note_commit()])
    llm = FakeLLM()
    b, reports = build(env, llm)
    b._max_tokens = _full_prompt_tokens(b) - 1
    b.rebuild("PAY-100")
    assert len(llm.prompts) == 3  # two tickets + final
    assert "integration: call 1 — y [PAY-101]" in llm.prompts[2]
    assert reports.latest("PAY-100").content.test_scope[0].what == "call 3"
    assert all(len(p) // 4 <= b._max_tokens for p in llm.prompts)


def test_split_scope_keeps_commits_not_tagged_with_a_listed_ticket(env):
    """Commits tagged with the Epic key itself or a sub-task are not under any listed ticket."""
    env[0].save([rec("c3", "PAY-100"), rec("c4", "PAY-150"), _long_note_commit()])
    llm = FakeLLM()
    b, _ = build(env, llm)
    b._max_tokens = _full_prompt_tokens(b) - 1
    b.rebuild("PAY-100")
    partials = llm.prompts[:-1]
    assert any("note c3" in p and "note c4" in p for p in partials)
    assert all(len(p) // 4 <= b._max_tokens for p in llm.prompts)


def test_split_scope_chunks_one_large_ticket_to_fit(db):
    impact = ImpactRepository(db)
    long = [rec(f"c{i}", "PAY-101").model_copy(update={"test_note": [f"note c{i} " + "x" * 2000]})
            for i in range(6)]
    impact.save(long)
    llm = FakeLLM()
    b, reports = build((impact, FindingRepository(db), ReportRepository(db)), llm)
    b._max_tokens = _full_prompt_tokens(b) // 3
    b.rebuild("PAY-100")
    assert all(len(p) // 4 <= b._max_tokens for p in llm.prompts)
    partials = llm.prompts[:-1] if len(llm.prompts) > 1 else llm.prompts
    for i in range(6):
        assert any(f"note c{i} " in p for p in partials)
    assert reports.latest("PAY-100").content.test_scope_status == "ok"


def test_split_scope_returns_partial_items_when_final_prompt_cannot_fit(env):
    llm = FakeLLM()
    b, reports = build(env, llm, max_tokens=1)  # nothing fits: no consolidation call
    b.rebuild("PAY-100")
    assert len(llm.prompts) == 2  # one per ticket, no final
    assert [i.what for i in reports.latest("PAY-100").content.test_scope] == ["call 1", "call 2"]


def test_story_subject_lists_the_story_itself(db):
    impact = ImpactRepository(db)
    impact.save([rec("c1", "PAY-10").model_copy(update={"subject": "PAY-10", "epic_key": None})])
    subtasks = [EpicTicket(key=k, summary="s", status="Open", assignee=None) for k in ("PAY-11", "PAY-12")]
    impact_env = (impact, FindingRepository(db), ReportRepository(db))
    b = EpicReportBuilder(impact, FakeTickets(TICKETS, children={"PAY-10": subtasks}), impact_env[1],
                          impact_env[2], FakeLLM(), 60_000)
    b.rebuild("PAY-10")
    keys = [t.key for t in impact_env[2].latest("PAY-10").content.aggregate.tickets]
    assert keys == ["PAY-10", "PAY-11", "PAY-12"]


def test_unresolvable_subject_is_skipped_without_llm(db):
    impact = ImpactRepository(db)
    impact.save([rec("c1", "PAY-99999").model_copy(update={"subject": "PAY-99999", "epic_key": None})])
    llm = FakeLLM()
    reports = ReportRepository(db)
    b = EpicReportBuilder(impact, FakeTickets(TICKETS, unknown={"PAY-99999"}), FindingRepository(db),
                          reports, llm, 60_000)
    assert b.rebuild("PAY-99999") is None
    assert llm.prompts == [] and reports.latest("PAY-99999") is None


def test_pending_rebuild_without_changes_appends_no_version(env):
    llm = FakeLLM(_raise(BudgetExceeded()))
    b, reports = build(env, llm)
    with pytest.raises(BudgetExceeded):
        b.rebuild("PAY-100")
    with pytest.raises(BudgetExceeded):
        b.rebuild("PAY-100")  # next day: still no budget, nothing new
    assert [v.version for v in reports.versions("PAY-100")] == [1]
    env[0].save([rec("c3", "PAY-101")])
    with pytest.raises(BudgetExceeded):
        b.rebuild("PAY-100")  # new commits are still shown while pending
    assert [v.version for v in reports.versions("PAY-100")] == [1, 2]


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
