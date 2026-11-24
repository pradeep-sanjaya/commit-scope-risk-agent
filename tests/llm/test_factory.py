from types import SimpleNamespace

import pytest

from scope_agent.llm.anthropic_client import AnthropicClient
from scope_agent.llm.budget import BudgetGuard
from scope_agent.llm.factory import build_llm
from scope_agent.persistence.usage import TokenUsageMeter


def settings(provider="anthropic"):
    from pydantic import SecretStr
    return SimpleNamespace(llm_provider=provider, llm_model="m", llm_base_url=None,
                           llm_api_key=SecretStr("k"), llm_daily_token_budget=10,
                           llm_max_output_tokens=100, timezone="UTC")


def test_factory_wraps_in_budget_guard(db):
    llm = build_llm(settings(), TokenUsageMeter(db))
    assert isinstance(llm, BudgetGuard) and isinstance(llm._inner, AnthropicClient)


def test_unsupported_provider(db):
    with pytest.raises(ValueError):
        build_llm(settings("nope"), TokenUsageMeter(db))
