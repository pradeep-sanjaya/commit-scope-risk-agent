from datetime import date

import pytest
from pydantic import BaseModel

from scope_agent.llm.base import BudgetExceeded, LLMResult
from scope_agent.llm.budget import BudgetGuard
from scope_agent.persistence.usage import TokenUsageMeter

DAY = date(2026, 1, 1)


class Out(BaseModel):
    x: int = 1


class Inner:
    def __init__(self):
        self.calls = 0

    def analyze(self, system, prompt, schema):
        self.calls += 1
        return LLMResult(data=Out(), input_tokens=100, output_tokens=50)


def test_budget_blocks_before_call(db):
    meter = TokenUsageMeter(db)
    meter.add(DAY, 1000)
    inner = Inner()
    with pytest.raises(BudgetExceeded):
        BudgetGuard(inner, meter, 1000, lambda: DAY).analyze("s", "p", Out)
    assert inner.calls == 0


def test_budget_records_usage(db):
    meter = TokenUsageMeter(db)
    g = BudgetGuard(Inner(), meter, 1000, lambda: DAY)
    g.analyze("s", "p", Out)
    assert meter.used(DAY) == 150


def test_budget_records_tokens_of_failed_analysis(db):
    from scope_agent.llm.base import LLMOutputError

    class Failing:
        def analyze(self, system, prompt, schema):
            raise LLMOutputError("bad", 70, 30)

    meter = TokenUsageMeter(db)
    with pytest.raises(LLMOutputError):
        BudgetGuard(Failing(), meter, 1000, lambda: DAY).analyze("s", "p", Out)
    assert meter.used(DAY) == 100
