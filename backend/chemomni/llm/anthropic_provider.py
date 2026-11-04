"""Anthropic Messages API provider (Claude models).

The loop's messages are in Chat Completions shape. This adapter:
- joins every ``system`` message into the ``system`` parameter (static prompt first, cached;
  the per-turn workspace snapshot second),
- turns assistant ``tool_calls`` into ``tool_use`` blocks and ``tool`` messages into
  ``tool_result`` blocks inside a user turn (consecutive results share one user message),
- maps the streamed events back to LLMEvents.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import anthropic
from anthropic import AsyncAnthropic

from chemomni.llm.provider import Done, LLMError, LLMEvent, Message, TextDelta, ToolCall, ToolSpecJSON

MAX_TOKENS = 4096


def _loads(arguments: str) -> dict[str, Any]:
    try:
        value = json.loads(arguments or "{}")
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def to_anthropic(messages: list[Message]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (system blocks, messages)."""
    system: list[dict[str, Any]] = []
    out: list[dict[str, Any]] = []

    def push(role: str, blocks: list[dict[str, Any]]) -> None:
        if out and out[-1]["role"] == role:
            out[-1]["content"].extend(blocks)
        else:
            out.append({"role": role, "content": list(blocks)})

    for m in messages:
        role = m["role"]
        if role == "system":
            system.append({"type": "text", "text": m["content"]})
        elif role == "user":
            push("user", [{"type": "text", "text": m["content"]}])
        elif role == "assistant":
            blocks: list[dict[str, Any]] = []
            if m.get("content"):
                blocks.append({"type": "text", "text": m["content"]})
            for tc in m.get("tool_calls") or []:
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": tc["id"],
                        "name": tc["function"]["name"],
                        "input": _loads(tc["function"]["arguments"]),
                    }
                )
            if blocks:
                push("assistant", blocks)
        elif role == "tool":
            push("user", [{"type": "tool_result", "tool_use_id": m["tool_call_id"], "content": m["content"]}])
    if system:
        system[0]["cache_control"] = {"type": "ephemeral"}  # the static prompt is identical every turn
    return system, out


def to_tools(specs: list[ToolSpecJSON]) -> list[dict[str, Any]]:
    tools = [
        {
            "name": s["function"]["name"],
            "description": s["function"].get("description", ""),
            "input_schema": s["function"].get("parameters") or {"type": "object"},
        }
        for s in specs
    ]
    if tools:
        tools[-1]["cache_control"] = {"type": "ephemeral"}
    return tools


class AnthropicProvider:
    def __init__(self, *, api_key: str | None, model: str, http_client: Any = None) -> None:
        self.model = model
        self._missing_key = not api_key
        self._client = AsyncAnthropic(api_key=api_key or "missing", http_client=http_client)

    async def stream(self, messages: list[Message], tools: list[ToolSpecJSON]) -> AsyncIterator[LLMEvent]:
        if self._missing_key:
            raise LLMError("ANTHROPIC_API_KEY is not set. Put it in .env at the repository root.")
        system, msgs = to_anthropic(messages)
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": MAX_TOKENS,
            "messages": msgs,
            "stream": True,
        }
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = to_tools(tools)
        try:
            stream = await self._client.messages.create(**kwargs)
            blocks: dict[int, dict[str, str]] = {}  # index -> tool_use being assembled
            usage: dict[str, int] = {}
            stop_reason: str | None = None
            async for ev in stream:
                kind = ev.type
                if kind == "message_start":
                    usage.update(_ints(ev.message.usage.model_dump()))
                elif kind == "content_block_start":
                    if ev.content_block.type == "tool_use":
                        blocks[ev.index] = {"id": ev.content_block.id, "name": ev.content_block.name, "json": ""}
                elif kind == "content_block_delta":
                    if ev.delta.type == "text_delta":
                        yield TextDelta(ev.delta.text)
                    elif ev.delta.type == "input_json_delta" and ev.index in blocks:
                        blocks[ev.index]["json"] += ev.delta.partial_json
                elif kind == "content_block_stop":
                    block = blocks.pop(ev.index, None)
                    if block is not None:
                        yield ToolCall(block["id"], block["name"], block["json"] or "{}")
                elif kind == "message_delta":
                    stop_reason = ev.delta.stop_reason
                    if ev.usage is not None:
                        usage.update(_ints(ev.usage.model_dump()))
            yield Done(finish_reason="tool_calls" if stop_reason == "tool_use" else stop_reason, usage=usage)
        except anthropic.AuthenticationError as exc:
            raise LLMError("The Anthropic API key was rejected.") from exc
        except anthropic.NotFoundError as exc:
            raise LLMError(f"Model '{self.model}' was not found at Anthropic.") from exc
        except anthropic.APIConnectionError as exc:
            raise LLMError("Could not reach the Anthropic API.") from exc
        except anthropic.APIStatusError as exc:
            raise LLMError(f"Provider error {exc.status_code}: {exc.message}") from exc


def _ints(d: dict[str, Any]) -> dict[str, int]:
    return {k: v for k, v in d.items() if isinstance(v, int)}
