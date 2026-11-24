from collections.abc import Callable
from datetime import date

from scope_agent.llm.base import BudgetExceeded, LLMOutputError, LLMClient, LLMResult, T
from scope_agent.persistence.usage import TokenUsageMeter


class BudgetGuard:
    def __init__(self, inner: LLMClient, meter: TokenUsageMeter, daily_budget: int,
                 today: Callable[[], date]):
        self._inner = inner
        self._meter = meter
        self._budget = daily_budget
        self._today = today

    def analyze(self, system: str, prompt: str, schema: type[T]) -> LLMResult:
        day = self._today()
        if self._meter.used(day) >= self._budget:
            raise BudgetExceeded(f"daily token budget {self._budget} reached")
        try:
            result = self._inner.analyze(system, prompt, schema)
        except LLMOutputError as e:
            self._meter.add(day, e.input_tokens + e.output_tokens)
            raise
        self._meter.add(day, result.input_tokens + result.output_tokens)
        return result
