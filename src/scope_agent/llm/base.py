from dataclasses import dataclass
from typing import Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class LLMOutputError(Exception):
    """The model did not return a valid structured result, even after one retry."""


class BudgetExceeded(Exception):
    """The daily token budget is used up; the job should be deferred."""


@dataclass(frozen=True)
class LLMResult:
    data: BaseModel
    input_tokens: int
    output_tokens: int


class LLMClient(Protocol):
    def analyze(self, system: str, prompt: str, schema: type[T]) -> LLMResult: ...
