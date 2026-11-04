from __future__ import annotations

import json

import httpx

from chemomni.llm.openai_provider import OpenAIChatProvider
from chemomni.llm.provider import Done, TextDelta, ToolCall

REJECTION = (
    "Function tools with reasoning_effort are not supported for gpt-6.1-sol in /v1/chat/completions. "
    "To use function tools, use /v1/responses or set reasoning_effort to 'none'."
)


def _sse(chunks: list[dict]) -> bytes:
    return "".join(f"data: {json.dumps(c)}\n\n" for c in chunks).encode() + b"data: [DONE]\n\n"


def _chunk(delta: dict, finish: str | None = None) -> dict:
    return {
        "id": "x",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": "gpt-6.1-sol",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }


STREAM = _sse(
    [
        _chunk({"role": "assistant", "content": "Hi"}),
        _chunk(
            {
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "similarity", "arguments": '{"smi'},
                    }
                ]
            }
        ),
        _chunk({"tool_calls": [{"index": 0, "function": {"arguments": 'les_a": "C", "smiles_b": "CC"}'}}]}),
        _chunk({}, "tool_calls"),
    ]
)


async def test_falls_back_to_reasoning_none_when_tools_are_rejected() -> None:
    sent: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        sent.append(body)
        if body.get("reasoning_effort") != "none":
            return httpx.Response(400, json={"error": {"message": REJECTION, "type": "invalid_request_error"}})
        return httpx.Response(200, content=STREAM, headers={"content-type": "text/event-stream"})

    provider = OpenAIChatProvider(
        api_key="sk-test",
        base_url=None,
        model="gpt-6.1-sol",
        reasoning_effort="low",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    tools = [{"type": "function", "function": {"name": "similarity", "parameters": {"type": "object"}}}]
    events = [e async for e in provider.stream([{"role": "user", "content": "hi"}], tools)]

    assert [b["reasoning_effort"] for b in sent] == ["low", "none"]
    assert provider.reasoning_effort == "none"  # remembered for later turns
    assert events[0] == TextDelta("Hi")
    assert events[1] == ToolCall("call_1", "similarity", '{"smiles_a": "C", "smiles_b": "CC"}')
    assert isinstance(events[2], Done) and events[2].finish_reason == "tool_calls"

    # next turn goes straight to 'none'
    sent.clear()
    _ = [e async for e in provider.stream([{"role": "user", "content": "again"}], tools)]
    assert [b["reasoning_effort"] for b in sent] == ["none"]


async def test_other_400s_are_reported() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {"message": "bad request", "type": "invalid_request_error"}})

    provider = OpenAIChatProvider(
        api_key="sk-test",
        base_url=None,
        model="m",
        reasoning_effort="none",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    try:
        _ = [e async for e in provider.stream([{"role": "user", "content": "hi"}], [])]
    except Exception as exc:
        assert "Provider error 400" in str(exc)
    else:
        raise AssertionError("expected LLMError")
