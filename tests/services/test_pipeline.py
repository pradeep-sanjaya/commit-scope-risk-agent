from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select

from scope_agent.domain.models import (
    CommitData, FileChange, Finding, FindingType, PushJob, Severity, TicketContext,
)
from scope_agent.llm.base import BudgetExceeded
from scope_agent.persistence.commits import CommitLedger
from scope_agent.persistence.database import Database, analysis_errors, findings as findings_t
from scope_agent.persistence.findings import FindingRepository
from scope_agent.services.code_analysis import CodeAnalysis
from scope_agent.services.dependency_analysis import DependencyAnalysis
from scope_agent.services.pipeline import PushPipeline

JOB = PushJob(repo="P/r", branch="development", from_hash=None, to_hash="c2", pushed_by="u")
POM_B = "<project><dependencies><dependency><groupId>g</groupId><artifactId>old</artifactId><version>1</version></dependency></dependencies></project>"
POM_A = "<project><dependencies><dependency><groupId>g</groupId><artifactId>new</artifactId><version>1</version></dependency></dependencies></project>"


def _commit(sha, msg="PAY-1 work", parents=("p",), files=()):
    return CommitData(repo="P/r", sha=sha, author_name="A", author_email=f"{sha}@x.test", message=msg,
                      parents=list(parents), files=list(files),
                      committed_at=datetime(2026, 1, 1, tzinfo=timezone.utc))


def _ticket(key="PAY-1"):
    return TicketContext(key=key, summary="s", description="d", issue_type="Task", status="x",
                         acceptance_criteria="ac", components=[], labels=[], epic_key="PAY-9",
                         epic_summary=None, epic_description=None, linked_titles=[])


def _finding(sha="c1", cat="x"):
    return Finding(repo="?", commit=sha, author_email=None, jira_keys=[], type=FindingType.risky_change,
                   category=cat, severity=Severity.high, title="t", explanation="e", evidence=[],
                   files=[], suggested_reviewers=[])


class FakeBitbucket:
    def __init__(self, commits):
        self.commits = {c.sha: c for c in commits}
        self.fetched = []

    def commits_in_push(self, job, limit):
        return list(self.commits)

    def get_commit(self, repo, sha):
        self.fetched.append(sha)
        return self.commits[sha]


class FakeTickets:
    def __init__(self, tickets):
        self.tickets = tickets
        self.calls = []

    def resolve(self, key):
        self.calls.append(key)
        return self.tickets.get(key)


class FakeDeps:
    def __init__(self, analysis=None):
        self.analysis = analysis or DependencyAnalysis()
        self.calls = []

    def analyze(self, commits, changes):
        self.calls.append((commits, changes))
        return self.analysis


class FakeCode:
    def __init__(self, per_sha=None, error=None):
        self.per_sha = per_sha or {}
        self.error = error
        self.calls = []

    def analyze(self, commit, tickets):
        self.calls.append((commit.sha, tickets))
        if self.error:
            raise self.error
        return self.per_sha.get(commit.sha, CodeAnalysis(ac_quality="ok"))


@pytest.fixture
def db():
    d = Database("sqlite://")
    d.create_all()
    return d


def _pipe(db, commits, tickets=None, deps=None, code=None):
    bb = FakeBitbucket(commits)
    t = FakeTickets(tickets if tickets is not None else {"PAY-1": _ticket()})
    deps, code = deps or FakeDeps(), code or FakeCode()
    p = PushPipeline(bb, t, deps, code, db, FindingRepository(db), CommitLedger(db), ["PAY"], 50)
    return p, bb, t, deps, code


def _count(db, table):
    with db.session() as s:
        return s.execute(select(func.count()).select_from(table)).scalar()


def test_pipeline_skips_analyzed_and_merges(db):
    ledger = CommitLedger(db)
    ledger.mark_analyzed("P/r", ["old"])
    commits = [_commit("old"), _commit("mrg", parents=("a", "b")), _commit("c1")]
    p, bb, _, _, code = _pipe(db, commits)
    r = p.run(1, JOB)
    assert [c.sha for c in r.commits] == ["c1"]
    assert "old" not in bb.fetched
    assert [c[0] for c in code.calls] == ["c1"]
    assert ledger.is_analyzed("P/r", "mrg") and ledger.is_analyzed("P/r", "c1")


def test_pipeline_only_merges_marks_without_analysis(db):
    p, _, _, deps, _ = _pipe(db, [_commit("mrg", parents=("a", "b"))])
    r = p.run(1, JOB)
    assert r.commits == [] and r.findings == [] and deps.calls == []
    assert CommitLedger(db).is_analyzed("P/r", "mrg")


def test_pipeline_force_reanalyzes(db):
    CommitLedger(db).mark_analyzed("P/r", ["c1"])
    p, *_ = _pipe(db, [_commit("c1")])
    assert [c.sha for c in p.run(1, JOB, force=True).commits] == ["c1"]


def test_pipeline_missing_key_still_runs_code_analysis(db):
    p, _, tk, _, code = _pipe(db, [_commit("c1", msg="no key")], tickets={})
    r = p.run(1, JOB)
    assert [c[0] for c in code.calls] == ["c1"] and code.calls[0][1] == []
    assert [f.category for f in r.findings] == ["missing_key"]
    assert tk.calls == []


def test_pipeline_dependency_files_dispatched_and_cross_commit_swap(db):
    rm = FileChange(path="pom.xml", status="modified", before=POM_B, after="<project/>")
    add = FileChange(path="svc/pom.xml", status="modified", before="<project/>", after=POM_A)
    other = FileChange(path="a.txt", status="added", before=None, after="x")
    p, _, _, deps, _ = _pipe(db, [_commit("c1", files=[rm, other]), _commit("c2", files=[add])])
    r = p.run(1, JOB)
    assert len(deps.calls) == 1
    commits, changes = deps.calls[0]
    assert [c.sha for c in commits] == ["c1", "c2"]
    assert [(c.name, c.kind) for c in changes["c1"]] == [("g:old", "removed")]
    assert [(c.name, c.kind) for c in changes["c2"]] == [("g:new", "added")]
    assert r.dependency_changes == changes


def test_pipeline_enriches_and_persists_atomically(db):
    dep = DependencyAnalysis(findings=[_finding("c1", "dep")], errors=["dep boom"])
    code = FakeCode({"c1": CodeAnalysis(findings=[_finding("c1", "code")], ac_quality="vague",
                                       test_note=["tn"], errors=["code boom"])})
    p, _, tk, _, _ = _pipe(db, [_commit("c1"), _commit("c2", msg="PAY-1 again")], deps=FakeDeps(dep), code=code)
    r = p.run(7, JOB)
    assert tk.calls == ["PAY-1"]  # resolved once per push
    assert r.keys_by_commit == {"c1": ["PAY-1"], "c2": ["PAY-1"]}
    assert r.test_notes["c1"] == ["tn"]
    assert {f.category for f in r.findings} == {"dep", "code", "vague_ac"}
    for f in r.findings:
        assert f.repo == "P/r" and f.jira_keys == ["PAY-1"] and f.author_email == "c1@x.test"
    assert _count(db, findings_t) == 3
    with db.session() as s:
        rows = s.execute(select(analysis_errors.c.commit, analysis_errors.c.message)).all()
    assert sorted(rows, key=lambda x: x[1]) == [("c1", "code boom"), (None, "dep boom")]
    assert CommitLedger(db).is_analyzed("P/r", "c2")


def test_pipeline_budget_exceeded_persists_nothing(db):
    code = FakeCode(error=BudgetExceeded())
    p, *_ = _pipe(db, [_commit("c1")], code=code)
    with pytest.raises(BudgetExceeded):
        p.run(1, JOB)
    assert _count(db, findings_t) == 0
    assert not CommitLedger(db).is_analyzed("P/r", "c1")


def test_pipeline_save_failure_rolls_back_ledger(db):
    class Boom(FindingRepository):
        def save(self, *a, **k):
            super().save(*a, **k)
            raise RuntimeError("x")
    p, *_ = _pipe(db, [_commit("c1")])
    p._findings = Boom(db)
    with pytest.raises(RuntimeError):
        p.run(1, JOB)
    assert _count(db, findings_t) == 0 and not CommitLedger(db).is_analyzed("P/r", "c1")


def test_pipeline_on_commit_runs_in_same_transaction_and_failure_rolls_back(db):
    """If impact recording fails, nothing is marked analyzed, so the retry re-records the commits."""
    seen = []

    def boom(result, session):
        seen.append((result.commits[0].sha, session is not None))
        raise RuntimeError("impact down")

    p, *_ = _pipe(db, [_commit("c1")])
    with pytest.raises(RuntimeError, match="impact down"):
        p.run(1, JOB, on_commit=boom)
    assert seen == [("c1", True)]
    assert _count(db, findings_t) == 0 and not CommitLedger(db).is_analyzed("P/r", "c1")


def test_pipeline_lost_lease_persists_nothing(db):
    from scope_agent.persistence.jobs import LeaseLost

    def fence(session):
        raise LeaseLost("reclaimed")

    committed = []
    p, *_ = _pipe(db, [_commit("c1")])
    with pytest.raises(LeaseLost):
        p.run(1, JOB, fence=fence, on_commit=lambda r, s: committed.append(r))
    assert committed == []
    assert _count(db, findings_t) == 0 and not CommitLedger(db).is_analyzed("P/r", "c1")
