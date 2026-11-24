from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from scope_agent.llm.anthropic_client import AnthropicClient
from scope_agent.llm.base import LLMOutputError


class Out(BaseModel):
    answer: int


def tool_block(input_, id_="tu_1"):
    return SimpleNamespace(type="tool_use", name="report", id=id_, input=input_)


def response(blocks, inp=10, out=5):
    return SimpleNamespace(content=blocks, usage=SimpleNamespace(input_tokens=inp, output_tokens=out))


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


def make(responses):
    fake = FakeClient(responses)
    return AnthropicClient("k", "m", 100, None, client=fake), fake


def test_parses_tool_call():
    c, fake = make([response([tool_block({"answer": 3})])])
    r = c.analyze("sys", "prompt", Out)
    assert r.data == Out(answer=3)
    assert (r.input_tokens, r.output_tokens) == (10, 5)
    call = fake.calls[0]
    assert call["tool_choice"] == {"type": "tool", "name": "report"}
    assert call["tools"][0]["name"] == "report"
    assert call["tools"][0]["input_schema"] == Out.model_json_schema()
    assert call["system"] == "sys" and call["max_tokens"] == 100


def test_retries_once_then_raises():
    c, fake = make([response([tool_block({"answer": "x"})]), response([tool_block({"answer": "y"})])])
    with pytest.raises(LLMOutputError):
        c.analyze("s", "p", Out)
    assert len(fake.calls) == 2
    last = fake.calls[1]["messages"][-1]
    assert last["role"] == "user"
    part = last["content"][0]
    assert part["type"] == "tool_result" and part["tool_use_id"] == "tu_1" and part["is_error"] is True


def test_retry_succeeds_and_sums_tokens():
    c, _ = make([response([tool_block({"answer": "x"})], 10, 5), response([tool_block({"answer": 1})], 20, 7)])
    r = c.analyze("s", "p", Out)
    assert r.data.answer == 1
    assert (r.input_tokens, r.output_tokens) == (30, 12)


def test_missing_tool_block_retries_with_plain_message():
    c, fake = make([response([SimpleNamespace(type="text", text="hi")]), response([tool_block({"answer": 2})])])
    assert c.analyze("s", "p", Out).data.answer == 2
    last = fake.calls[1]["messages"][-1]
    assert last["role"] == "user" and isinstance(last["content"], str) and "report" in last["content"]
