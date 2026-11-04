"""OpenAI Responses API provider.

Needed because newer OpenAI reasoning models (e.g. gpt-6.1-sol) refuse function tools on
/v1/chat/completions. The loop's internal messages stay in Chat Completions shape; this adapter
converts them to Responses ``input`` items and maps the streamed events back to LLMEvents.
Requests are stateless (``store=False``): history is always sent by the loop.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import openai
from openai import AsyncOpenAI

from chemomni.llm.provider import Done, LLMError, LLMEvent, Message, TextDelta, ToolCall, ToolSpecJSON


def to_input_items(messages: list[Message]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for m in messages:
        role = m["role"]
        if role == "system":
            items.append({"role": "developer", "content": m["content"]})
        elif role == "user":
            items.append({"role": "user", "content": m["content"]})
        elif role == "assistant":
            if m.get("content"):
                items.append({"role": "assistant", "content": m["content"]})
            for tc in m.get("tool_calls") or []:
                items.append(
                    {
                        "type": "function_call",
                        "call_id": tc["id"],
                        "name": tc["function"]["name"],
                        "arguments": tc["function"]["arguments"],
                    }
                )
        elif role == "tool":
            items.append({"type": "function_call_output", "call_id": m["tool_call_id"], "output": m["content"]})
    return items


def to_tools(specs: list[ToolSpecJSON]) -> list[dict[str, Any]]:
    out = []
    for s in specs:
        fn = s["function"]
        out.append(
            {
                "type": "function",
                "name": fn["name"],
                "description": fn.get("description"),
                "parameters": fn.get("parameters"),
                "strict": False,
            }
        )
    return out


class OpenAIResponsesProvider:
    def __init__(
        self,
        *,
        api_key: str | None,
        base_url: str | None,
        model: str,
        reasoning_effort: str | None = None,
        http_client: Any = None,
    ) -> None:
        self.model = model
        self.reasoning_effort = reasoning_effort or None
        self._missing_key = not api_key
        self._client = AsyncOpenAI(api_key=api_key or "missing", base_url=base_url or None, http_client=http_client)

    async def stream(self, messages: list[Message], tools: list[ToolSpecJSON]) -> AsyncIterator[LLMEvent]:
        if self._missing_key:
            raise LLMError("OPENAI_API_KEY is not set. Put it in .env at the repository root.")
        kwargs: dict[str, Any] = {
            "model": self.model,
            "input": to_input_items(messages),
            "stream": True,
            "store": False,
        }
        if tools:
            kwargs["tools"] = to_tools(tools)
        if self.reasoning_effort:
            kwargs["reasoning"] = {"effort": self.reasoning_effort}
        try:
            stream = await self._client.responses.create(**kwargs)
            saw_call = False
            async for ev in stream:
                kind = getattr(ev, "type", "")
                if kind == "response.output_text.delta":
                    if ev.delta:
                        yield TextDelta(ev.delta)
                elif kind == "response.output_item.done":
                    item = ev.item
                    if getattr(item, "type", "") == "function_call":
                        saw_call = True
                        yield ToolCall(item.call_id, item.name, item.arguments or "{}")
                elif kind == "response.completed":
                    usage = ev.response.usage.model_dump(exclude_none=True) if ev.response.usage else {}
                    yield Done(finish_reason="tool_calls" if saw_call else "stop", usage=_flat_usage(usage))
                    return
                elif kind == "response.incomplete":
                    details = ev.response.incomplete_details
                    reason = getattr(details, "reason", None) or "incomplete"
                    yield Done(finish_reason=reason, usage={})
                    return
                elif kind == "response.failed":
                    err = ev.response.error
                    raise LLMError(f"Provider error: {getattr(err, 'message', 'response failed')}")
                elif kind == "error":
                    raise LLMError(f"Provider error: {getattr(ev, 'message', 'unknown error')}")
            yield Done(finish_reason="stop", usage={})
        except openai.AuthenticationError as exc:
            raise LLMError("The API key was rejected by the provider.") from exc
        except openai.NotFoundError as exc:
            raise LLMError(f"Model '{self.model}' was not found at the provider.") from exc
        except openai.APIConnectionError as exc:
            raise LLMError("Could not reach the LLM provider.") from exc
        except openai.APIStatusError as exc:
            raise LLMError(f"Provider error {exc.status_code}: {exc.message}") from exc


def _flat_usage(usage: dict[str, Any]) -> dict[str, int]:
    return {k: v for k, v in usage.items() if isinstance(v, int)}
