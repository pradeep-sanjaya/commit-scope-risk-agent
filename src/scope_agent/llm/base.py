from dataclasses import dataclass
from typing import Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class LLMOutputError(Exception):
    """The model did not return a valid structured result, even after one retry."""

    def __init__(self, message: str, input_tokens: int = 0, output_tokens: int = 0):
        super().__init__(message)
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class BudgetExceeded(Exception):
    """The daily token budget is used up; the job should be deferred."""


@dataclass(frozen=True)
class LLMResult:
    data: BaseModel
    input_tokens: int
    output_tokens: int


class LLMClient(Protocol):
    def analyze(self, system: str, prompt: str, schema: type[T]) -> LLMResult: ...
