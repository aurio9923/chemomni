from __future__ import annotations

import json

import httpx2  # the anthropic SDK ships its own httpx fork
import pytest

from chemomni.api.chat import make_provider
from chemomni.config import Settings
from chemomni.llm.anthropic_provider import AnthropicProvider, to_anthropic, to_tools
from chemomni.llm.provider import Done, LLMError, TextDelta, ToolCall


def _sse(events: list[dict]) -> bytes:
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events).encode()


MSG = {
    "id": "msg_1",
    "type": "message",
    "role": "assistant",
    "model": "claude-haiku-5-5",
    "content": [],
    "stop_reason": None,
    "stop_sequence": None,
    "usage": {"input_tokens": 200, "output_tokens": 1},
}

STREAM = _sse(
    [
        {"type": "message_start", "message": MSG},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Checking"}},
        {"type": "content_block_stop", "index": 0},
        {
            "type": "content_block_start",
            "index": 1,
            "content_block": {"type": "tool_use", "id": "toolu_1", "name": "compute_properties", "input": {}},
        },
        {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": '{"smiles"'}},
        {
            "type": "content_block_delta",
            "index": 1,
            "delta": {"type": "input_json_delta", "partial_json": ': ["CCO"]}'},
        },
        {"type": "content_block_stop", "index": 1},
        {
            "type": "message_delta",
            "delta": {"stop_reason": "tool_use", "stop_sequence": None},
            "usage": {"output_tokens": 42},
        },
        {"type": "message_stop"},
    ]
)


def test_conversion_merges_roles_and_moves_system() -> None:
    system, msgs = to_anthropic(
        [
            {"role": "system", "content": "prompt"},
            {"role": "user", "content": "hi"},
            {
                "role": "assistant",
                "content": "Let me check.",
                "tool_calls": [
                    {"id": "t1", "type": "function", "function": {"name": "a", "arguments": '{"x": 1}'}},
                    {"id": "t2", "type": "function", "function": {"name": "b", "arguments": "{}"}},
                ],
            },
            {"role": "tool", "tool_call_id": "t1", "content": "r1"},
            {"role": "tool", "tool_call_id": "t2", "content": "r2"},
            {"role": "assistant", "content": "done"},
            {"role": "system", "content": "<workspace>"},
            {"role": "user", "content": "next"},
        ]
    )
    assert [b["text"] for b in system] == ["prompt", "<workspace>"]
    assert system[0]["cache_control"] == {"type": "ephemeral"}
    assert [m["role"] for m in msgs] == ["user", "assistant", "user", "assistant", "user"]
    assert msgs[1]["content"][1] == {"type": "tool_use", "id": "t1", "name": "a", "input": {"x": 1}}
    assert [b["tool_use_id"] for b in msgs[2]["content"]] == ["t1", "t2"]


def test_tools_have_input_schema() -> None:
    tools = to_tools(
        [{"type": "function", "function": {"name": "f", "description": "d", "parameters": {"type": "object"}}}]
    )
    assert tools == [
        {"name": "f", "description": "d", "input_schema": {"type": "object"}, "cache_control": {"type": "ephemeral"}}
    ]


async def test_streams_text_and_tool_use() -> None:
    sent: list[dict] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.url.path.endswith("/v1/messages")
        sent.append(json.loads(request.content))
        return httpx2.Response(200, content=STREAM, headers={"content-type": "text/event-stream"})

    provider = AnthropicProvider(
        api_key="sk-ant-test",
        model="claude-haiku-5-5",
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )
    tools = [{"type": "function", "function": {"name": "compute_properties", "parameters": {"type": "object"}}}]
    events = [
        e async for e in provider.stream([{"role": "system", "content": "p"}, {"role": "user", "content": "x"}], tools)
    ]
    assert sent[0]["model"] == "claude-haiku-5-5" and sent[0]["max_tokens"] > 0
    assert events[0] == TextDelta("Checking")
    assert events[1] == ToolCall("toolu_1", "compute_properties", '{"smiles": ["CCO"]}')
    assert isinstance(events[2], Done)
    assert events[2].finish_reason == "tool_calls"
    assert events[2].usage["input_tokens"] == 200 and events[2].usage["output_tokens"] == 42


async def test_missing_key() -> None:
    with pytest.raises(LLMError, match="ANTHROPIC_API_KEY"):
        _ = [e async for e in AnthropicProvider(api_key=None, model="claude-haiku-5-5").stream([], [])]


def test_routing_by_model_name() -> None:
    s = Settings(_env_file=None, OPENAI_API_KEY="sk", ANTHROPIC_API_KEY="ak", CHEMOMNI_PROVIDER="openai")
    assert isinstance(make_provider(s, "claude-haiku-5-5"), AnthropicProvider)
    assert not isinstance(make_provider(s, "gpt-6-luna"), AnthropicProvider)
    assert "claude-haiku-5-5" in s.model_list
