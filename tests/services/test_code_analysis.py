from datetime import datetime, timezone

import pytest

from scope_agent.domain.models import CommitData, FileChange, FindingType, Severity, TicketContext
from scope_agent.llm.base import BudgetExceeded, LLMOutputError, LLMResult
from scope_agent.llm.schemas import CodeVerdict, LLMFinding
from scope_agent.services.code_analysis import CodeAnalyzer


class FakeLLM:
    def __init__(self, items):
        self.items = list(items)
        self.prompts = []

    def analyze(self, system, prompt, schema):
        self.prompts.append(prompt)
        item = self.items.pop(0)
        if isinstance(item, Exception):
            raise item
        return LLMResult(item, 10, 5)


def _v(findings=(), ac="ok", notes=("n1",)):
    return CodeVerdict(findings=list(findings), ac_quality=ac, ac_quality_reason="r", test_note=list(notes))


def _lf(type_=FindingType.risky_change, cat="connection_model"):
    return LLMFinding(type=type_, category=cat, severity=Severity.high, title="t", explanation="e",
                      evidence=["+x"], files=["A.java"])


def _ticket(key="P-1"):
    return TicketContext(key=key, summary="s", description="d", issue_type="Task", status="x",
                         acceptance_criteria=None, components=[], labels=[], epic_key=None,
                         epic_summary=None, epic_description=None, linked_titles=[])


def _commit(files):
    return CommitData(repo="P/r", sha="c1", author_name="A", author_email="a@x.test", message="msg",
                      parents=["p"], files=files, committed_at=datetime.now(timezone.utc))


def _big(n, size=8000):
    return [FileChange(path=f"F{i}.java", status="modified", before="a\n", after="b" * size + "\n") for i in range(n)]


def _analyzer(llm, max_tokens=60_000, max_chunks=3):
    return CodeAnalyzer(llm, exclude=[], priority=[], max_input_tokens=max_tokens, max_chunks=max_chunks)


def test_finding_conversion():
    llm = FakeLLM([_v([_lf()])])
    r = _analyzer(llm).analyze(_commit(_big(1, 10)), [_ticket("P-1"), _ticket("P-2")])
    f = r.findings[0]
    assert (f.repo, f.commit, f.author_email, f.jira_keys) == ("P/r", "c1", "a@x.test", ["P-1", "P-2"])
    assert f.type == FindingType.risky_change and f.category == "connection_model"
    assert "<ticket key=\"P-2\">" in llm.prompts[0]


def test_partial_analysis_finding():
    llm = FakeLLM([_v(), _v()])
    r = _analyzer(llm, max_tokens=4000, max_chunks=2).analyze(_commit(_big(5)), [_ticket()])
    partial = [f for f in r.findings if f.category == "partial_analysis"]
    assert len(partial) == 1
    p = partial[0]
    assert p.severity == Severity.info and p.type == FindingType.risky_change
    assert p.title == "Partial analysis: 3 file(s) not reviewed (size budget)"
    assert p.files == p.evidence == ["F2.java", "F3.java", "F4.java"]
    assert "manually" in p.explanation
    assert all("partial (truncated)" in pr for pr in llm.prompts)


def test_no_ticket_drops_out_of_scope():
    llm = FakeLLM([_v([_lf(FindingType.out_of_scope, "x"), _lf()], ac="missing")])
    r = _analyzer(llm).analyze(_commit(_big(1, 10)), [])
    assert [f.type for f in r.findings] == [FindingType.risky_change]
    assert r.ac_quality is None


def test_worst_ac_quality_and_test_notes_deduped():
    llm = FakeLLM([_v(ac="ok", notes=("a", "b")), _v(ac="missing", notes=("b", "c")), _v(ac="vague")])
    r = _analyzer(llm, max_tokens=4000, max_chunks=3).analyze(_commit(_big(3)), [_ticket()])
    assert r.ac_quality == "missing"
    assert r.test_note == ["a", "b", "c", "n1"]


def test_vague_beats_ok():
    llm = FakeLLM([_v(ac="ok"), _v(ac="vague")])
    r = _analyzer(llm, max_tokens=4000, max_chunks=3).analyze(_commit(_big(2)), [_ticket()])
    assert r.ac_quality == "vague"


def test_llm_output_error_isolated_to_chunk():
    llm = FakeLLM([LLMOutputError("bad"), _v([_lf()])])
    r = _analyzer(llm, max_tokens=4000, max_chunks=3).analyze(_commit(_big(2)), [_ticket()])
    assert len(r.errors) == 1 and len(r.findings) == 1


def test_budget_exceeded_propagates():
    with pytest.raises(BudgetExceeded):
        _analyzer(FakeLLM([BudgetExceeded()])).analyze(_commit(_big(1, 10)), [_ticket()])


def test_nothing_reviewable_no_llm_call():
    llm = FakeLLM([])
    r = CodeAnalyzer(llm, ["**/*.lock"], [], 1000, 3).analyze(
        _commit([FileChange(path="a.lock", status="modified", before="a", after="b")]), [_ticket()])
    assert r.findings == [] and r.ac_quality is None and r.test_note == [] and llm.prompts == []
