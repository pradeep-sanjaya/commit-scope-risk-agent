import pytest
from jinja2 import UndefinedError

from scope_agent.llm.prompts import render_prompt
from scope_agent.llm.schemas import RISK_CATEGORIES

CTX = {
    "dependency_upgrade": dict(library="l", old_version="1", new_version="2", notes=[("2", "n")], usage="u"),
    "dependency_new": dict(library="l", version="1", docs="d", usage="u"),
    "dependency_swap": dict(removed="a", removed_version="1", added="b", added_version="2",
                            old_profile="p", new_docs="d"),
    "code_review": dict(commit_message="m", tickets=[], diffs=[("a.py", "+x")], partial=False),
    "test_scope": dict(subject="s", aggregate="agg", tickets=[], test_notes=["t"], findings=[]),
}


@pytest.mark.parametrize("name", CTX)
def test_prompt_lists_categories_and_review(name):
    out = render_prompt(name, **CTX[name])
    for c in RISK_CATEGORIES:
        assert c in out
    assert "human review" in out and "verbatim" in out


def test_swap_prompt_lists_categories():
    out = render_prompt("dependency_swap", **CTX["dependency_swap"])
    assert "connection_model" in out and "keep-alive" in out


def test_missing_variable_raises():
    with pytest.raises(UndefinedError):
        render_prompt("dependency_new", library="l")
