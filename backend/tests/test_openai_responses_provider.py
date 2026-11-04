from __future__ import annotations

import json

import httpx
import pytest

from chemomni.api.chat import make_provider
from chemomni.config import Settings
from chemomni.llm.openai_provider import OpenAIChatProvider
from chemomni.llm.openai_responses_provider import OpenAIResponsesProvider, to_input_items
from chemomni.llm.provider import Done, LLMError, TextDelta, ToolCall


def _sse(events: list[dict]) -> bytes:
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events).encode()


def _response(status: str, **extra: object) -> dict:
    return {"id": "resp_1", "object": "response", "created_at": 0, "model": "gpt-6.1-sol", "status": status,
            "output": [], **extra}  # fmt: skip


STREAM = _sse(
    [
        {"type": "response.created", "sequence_number": 0, "response": _response("in_progress")},
        {
            "type": "response.output_text.delta",
            "sequence_number": 1,
            "item_id": "msg_1",
            "output_index": 0,
            "content_index": 0,
            "delta": "Let me ",
            "logprobs": [],
        },  # fmt: skip
        {
            "type": "response.output_text.delta",
            "sequence_number": 2,
            "item_id": "msg_1",
            "output_index": 0,
            "content_index": 0,
            "delta": "check.",
            "logprobs": [],
        },  # fmt: skip
        {
            "type": "response.output_item.done",
            "sequence_number": 3,
            "output_index": 1,
            "item": {
                "type": "function_call",
                "id": "fc_1",
                "call_id": "call_abc",
                "name": "compute_properties",
                "arguments": '{"smiles": ["CCO"]}',
                "status": "completed",
            },
        },  # fmt: skip
        {
            "type": "response.completed",
            "sequence_number": 4,
            "response": _response(
                "completed",
                usage={
                    "input_tokens": 120,
                    "output_tokens": 30,
                    "total_tokens": 150,
                    "input_tokens_details": {"cached_tokens": 0},
                    "output_tokens_details": {"reasoning_tokens": 12},
                },
            ),
        },  # fmt: skip
    ]
)


def _provider(handler, **kw) -> OpenAIResponsesProvider:  # type: ignore[no-untyped-def]
    return OpenAIResponsesProvider(
        api_key="sk-test",
        base_url=None,
        model="gpt-6.1-sol",
        reasoning_effort=kw.get("effort", "low"),
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


def test_message_conversion() -> None:
    items = to_input_items(
        [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hi"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "f", "arguments": "{}"}}],
            },
            {"role": "tool", "tool_call_id": "c1", "content": '{"ok": true}'},
            {"role": "assistant", "content": "done"},
        ]
    )
    assert items == [
        {"role": "developer", "content": "sys"},
        {"role": "user", "content": "hi"},
        {"type": "function_call", "call_id": "c1", "name": "f", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "c1", "output": '{"ok": true}'},
        {"role": "assistant", "content": "done"},
    ]


async def test_streams_text_and_function_calls() -> None:
    sent: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/responses")
        sent.append(json.loads(request.content))
        return httpx.Response(200, content=STREAM, headers={"content-type": "text/event-stream"})

    tools = [{"type": "function", "function": {"name": "compute_properties", "description": "d",
                                               "parameters": {"type": "object"}}}]  # fmt: skip
    events = [e async for e in _provider(handler).stream([{"role": "user", "content": "props"}], tools)]

    body = sent[0]
    assert body["reasoning"] == {"effort": "low"}
    assert body["store"] is False and body["stream"] is True
    assert body["tools"][0]["name"] == "compute_properties" and body["tools"][0]["type"] == "function"
    assert events[:2] == [TextDelta("Let me "), TextDelta("check.")]
    assert events[2] == ToolCall("call_abc", "compute_properties", '{"smiles": ["CCO"]}')
    assert isinstance(events[3], Done) and events[3].finish_reason == "tool_calls"
    assert events[3].usage["total_tokens"] == 150


async def test_http_error_becomes_llm_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {"message": "bad effort", "type": "invalid_request_error"}})

    with pytest.raises(LLMError, match="Provider error 400"):
        _ = [e async for e in _provider(handler).stream([{"role": "user", "content": "x"}], [])]


def test_provider_selection() -> None:
    official = make_provider(Settings(OPENAI_API_KEY="sk", CHEMOMNI_PROVIDER="openai"))
    local = make_provider(
        Settings(OPENAI_API_KEY="", OPENAI_BASE_URL="http://localhost:11434/v1", CHEMOMNI_PROVIDER="openai")
    )
    forced = make_provider(Settings(OPENAI_API_KEY="sk", CHEMOMNI_PROVIDER="openai", CHEMOMNI_OPENAI_API="chat"))
    assert isinstance(official, OpenAIResponsesProvider)
    assert isinstance(local, OpenAIChatProvider)
    assert isinstance(forced, OpenAIChatProvider)
