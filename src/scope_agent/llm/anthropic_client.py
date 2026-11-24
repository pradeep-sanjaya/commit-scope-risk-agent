from typing import Any

import anthropic
import structlog
from pydantic import ValidationError

from scope_agent.llm.base import LLMOutputError, LLMResult, T

log = structlog.get_logger()

TOOL_NAME = "report"


class AnthropicClient:
    def __init__(self, api_key: str, model: str, max_tokens: int, base_url: str | None,
                 client: Any | None = None):
        self._model = model
        self._max_tokens = max_tokens
        self._client = client or anthropic.Anthropic(api_key=api_key, base_url=base_url, max_retries=2)

    def analyze(self, system: str, prompt: str, schema: type[T]) -> LLMResult:
        messages: list[dict] = [{"role": "user", "content": prompt}]
        tools = [{"name": TOOL_NAME, "description": "Report the analysis result.",
                  "input_schema": schema.model_json_schema()}]
        tin = tout = 0
        error = ""
        for attempt in range(2):
            resp = self._client.messages.create(
                model=self._model, max_tokens=self._max_tokens, system=system, messages=messages,
                tools=tools, tool_choice={"type": "tool", "name": TOOL_NAME},
            )
            tin += resp.usage.input_tokens
            tout += resp.usage.output_tokens
            log.info("llm_call", model=self._model, schema=schema.__name__,
                     input_tokens=resp.usage.input_tokens, output_tokens=resp.usage.output_tokens)
            block = next((b for b in resp.content
                          if getattr(b, "type", None) == "tool_use" and b.name == TOOL_NAME), None)
            if block is not None:
                try:
                    return LLMResult(schema.model_validate(block.input), tin, tout)
                except ValidationError as e:
                    error = str(e)
                    retry = {"role": "user", "content": [{
                        "type": "tool_result", "tool_use_id": block.id, "is_error": True, "content": error}]}
            else:
                error = "no report tool call"
                retry = {"role": "user", "content": f"Please call the `{TOOL_NAME}` tool with your result."}
            messages = messages + [{"role": "assistant", "content": _dump(resp.content)}, retry]
        raise LLMOutputError(f"invalid structured output after retry: {error}")


def _dump(content: list) -> list:
    return [b.model_dump(exclude_none=True) if hasattr(b, "model_dump") else b for b in content]
