from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from scope_agent.domain.dependency_hints import DependencyHints
from scope_agent.domain.models import CommitData, DependencyChange, FindingType, Severity
from scope_agent.llm.base import BudgetExceeded, LLMOutputError, LLMResult
from scope_agent.llm.schemas import DependencyVerdict, LLMFinding
from scope_agent.services.dependency_analysis import DependencyAnalyzer
from scope_agent.services.library_docs import LibraryDocs
from scope_agent.services.release_notes import ReleaseNotes


class FakeLLM:
    def __init__(self, verdicts=None):
        self.verdicts = list(verdicts or [])
        self.prompts = []

    def analyze(self, system, prompt, schema):
        self.prompts.append(prompt)
        v = self.verdicts.pop(0) if self.verdicts else _verdict()
        if isinstance(v, Exception):
            raise v
        return LLMResult(data=v, input_tokens=1, output_tokens=1)


def _verdict(findings=(), summary="sum"):
    return DependencyVerdict(findings=list(findings), reviewer_summary=summary)


def _lf(sev=Severity.high, cat="connection_model"):
    return LLMFinding(type=FindingType.risky_change, category=cat, severity=sev, title="t",
                      explanation="e", evidence=["quote"], files=[])


def _commit(sha="c1", email="a@x.test"):
    return CommitData(repo="P/r", sha=sha, author_name="A", author_email=email, message="m",
                      parents=["p"], files=[], committed_at=datetime.now(timezone.utc))


def _dc(kind, name="com.x:y", old="1.4.0", new="2.0.0", eco="maven"):
    return DependencyChange(ecosystem=eco, name=name, old_version=old, new_version=new, kind=kind, file="pom.xml")


def _analyzer(llm, notes=None, docs=None, profile=None, hints=None):
    n = MagicMock()
    n.between.return_value = notes or ReleaseNotes(library="x", entries=[("2.0.0", "breaking")], links=["http://rn"], available=True)
    d = MagicMock()
    d.docs_for.return_value = docs or LibraryDocs(library="x", version="1", text="docs text", links=["http://docs"])
    u = MagicMock()
    u.build.return_value = "usage text"
    p = MagicMock()
    p.profile.return_value = profile
    a = DependencyAnalyzer(llm, n, d, u, p, hints or DependencyHints())
    a.mocks = (n, d, u, p)
    return a


def test_swap_high_connection_model_on_adding_commit_with_summary():
    removed = _dc("removed", name="com.acme:flags", old="1.0", new=None)
    added = _dc("added", name="dev.openfeature.contrib:flagd", old=None, new="0.8")
    hints = DependencyHints(known_swaps=[("com.acme:flags", "dev.openfeature.contrib:flagd")])
    llm = FakeLLM([_verdict([_lf()], "polling to streaming")])
    a = _analyzer(llm, profile="old profile", hints=hints)
    res = a.analyze([_commit("c1"), _commit("c2")], {"c1": [removed], "c2": [added]})
    assert len(llm.prompts) == 1 and "old profile" in llm.prompts[0] and "docs text" in llm.prompts[0]
    assert len(res.findings) == 1
    f = res.findings[0]
    assert f.commit == "c2" and f.severity == Severity.high and f.category == "connection_model"
    assert f.type == FindingType.dependency_behavior_change
    assert f.repo == "P/r" and f.author_email == "a@x.test" and f.jira_keys == [] and f.files == ["pom.xml"]
    assert "http://docs" in f.evidence
    assert [s.kind for s in res.summaries["c2"]] == ["swap"] and res.summaries["c2"][0].summary == "polling to streaming"
    assert "c1" not in res.summaries


def test_swap_uses_usage_context_when_no_profile():
    removed = _dc("removed", name="com.acme:flags", old="1.0", new=None)
    added = _dc("added", name="dev.openfeature.contrib:flagd", old=None, new="0.8")
    llm = FakeLLM()
    a = _analyzer(llm, profile=None, hints=DependencyHints(known_swaps=[("com.acme:flags", "dev.openfeature.contrib:flagd")]))
    a.analyze([_commit()], {"c1": [removed, added]})
    assert "usage text" in llm.prompts[0]
    assert a.mocks[2].build.call_args.args[1] == removed


@pytest.mark.parametrize("old,new,sev", [("1.4.0", "2.0.0", Severity.medium), ("1.4.0", "1.5.0", Severity.low)])
def test_upgrade_without_notes_behavior_unknown_no_llm(old, new, sev):
    llm = FakeLLM()
    a = _analyzer(llm, notes=ReleaseNotes(library="x", entries=[], links=[], available=False))
    res = a.analyze([_commit()], {"c1": [_dc("upgraded", old=old, new=new)]})
    assert llm.prompts == []
    f = res.findings[0]
    assert f.category == "behavior_unknown" and f.severity == sev
    assert f.title == f"Behavior changes unknown for com.x:y {old}→{new}: release notes unavailable"
    assert f"com.x:y {old} → {new}" in f.evidence and "pom.xml" in f.evidence
    assert res.summaries == {}


def test_notes_fetch_failure_is_behavior_unknown():
    llm = FakeLLM()
    a = _analyzer(llm)
    a.mocks[0].between.side_effect = RuntimeError("503")
    res = a.analyze([_commit()], {"c1": [_dc("upgraded")]})
    assert llm.prompts == [] and res.findings[0].category == "behavior_unknown"


def test_parent_bump_platform_upgrade():
    llm = FakeLLM([_verdict([], "parent summary")])
    res = _analyzer(llm).analyze([_commit()], {"c1": [_dc("parent", name="org.spring:parent")]})
    cats = {f.category: f for f in res.findings}
    assert cats["platform_upgrade"].severity == Severity.medium
    assert cats["platform_upgrade"].title == "Platform upgrade org.spring:parent 1.4.0→2.0.0: many managed versions changed"
    assert len(llm.prompts) == 1
    assert res.summaries["c1"][0].kind == "upgrade"


def test_transitive_low_no_llm():
    llm = FakeLLM()
    res = _analyzer(llm).analyze([_commit()], {"c1": [_dc("transitive")]})
    assert llm.prompts == []
    f = res.findings[0]
    assert f.category == "transitive_major" and f.severity == Severity.low
    assert f.title == "Transitive major upgrade com.x:y 1.4.0→2.0.0"


def test_added_library_summary_even_without_findings():
    llm = FakeLLM([_verdict([], "a client lib")])
    res = _analyzer(llm).analyze([_commit()], {"c1": [_dc("added", old=None, new="1.0")]})
    assert res.findings == []
    s = res.summaries["c1"][0]
    assert s.kind == "new" and s.summary == "a client lib" and s.links == ["http://docs"]


def test_added_with_docs_failure_proceeds_with_empty_docs():
    llm = FakeLLM()
    a = _analyzer(llm)
    a.mocks[1].docs_for.side_effect = RuntimeError("403")
    res = a.analyze([_commit()], {"c1": [_dc("added", old=None, new="1.0")]})
    assert len(llm.prompts) == 1 and res.errors == []


def test_removed_not_swap_nothing():
    llm = FakeLLM()
    res = _analyzer(llm).analyze([_commit()], {"c1": [_dc("removed", new=None)]})
    assert res.findings == [] and llm.prompts == []


def test_llm_error_isolated_to_one_change():
    llm = FakeLLM([LLMOutputError("bad json"), _verdict([_lf()], "ok")])
    res = _analyzer(llm).analyze(
        [_commit()], {"c1": [_dc("upgraded", name="a:a"), _dc("upgraded", name="b:b")]}
    )
    assert len(res.errors) == 1 and "a:a" in res.errors[0]
    assert len(res.findings) == 1 and "b:b" in res.summaries["c1"][0].library


def test_budget_exceeded_propagates():
    a = _analyzer(FakeLLM([BudgetExceeded()]))
    with pytest.raises(BudgetExceeded):
        a.analyze([_commit()], {"c1": [_dc("upgraded")]})


def test_incident_swap_detected_by_import_change_single_llm_call():
    from scope_agent.domain.models import FileChange
    removed = _dc("removed", name="com.corp:flag-util", old="1.0", new=None)
    added = _dc("added", name="dev.openfeature.contrib.providers:flagd", old=None, new="0.8")
    c1 = _commit("c1")
    c1.files = [FileChange(path="Flags.java", status="modified",
                           before="import com.corp.flagutil.Flags;\n", after="import dev.openfeature.contrib.providers.flagd.FlagdProvider;\n")]
    llm = FakeLLM([_verdict([_lf()], "polling to streaming")])
    res = _analyzer(llm).analyze([c1], {"c1": [removed, added]})
    assert len(llm.prompts) == 1 and "Removed: com.corp:flag-util" in llm.prompts[0]
    assert [(f.category, f.severity, f.commit) for f in res.findings] == [("connection_model", Severity.high, "c1")]
    assert [s.kind for s in res.summaries["c1"]] == ["swap"]
