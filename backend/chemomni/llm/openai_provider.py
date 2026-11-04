from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator
from typing import Any

import openai
from openai import AsyncOpenAI

from chemomni.llm.provider import Done, LLMError, LLMEvent, Message, TextDelta, ToolCall, ToolSpecJSON

log = logging.getLogger(__name__)


def _rejects_reasoning_with_tools(exc: openai.APIStatusError) -> bool:
    """e.g. 'Function tools with reasoning_effort are not supported for <model> in /v1/chat/completions'."""
    return exc.status_code == 400 and "reasoning_effort" in str(exc.message)


class OpenAIChatProvider:
    """Chat Completions with streamed tool calls. Works with any OpenAI-compatible base_url."""

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
        self._missing_key = not api_key and not base_url
        # Local OpenAI-compatible servers usually accept any key.
        self._client = AsyncOpenAI(api_key=api_key or "not-needed", base_url=base_url or None, http_client=http_client)

    async def _create(self, kwargs: dict[str, Any]) -> Any:
        try:
            return await self._client.chat.completions.create(**kwargs)
        except openai.APIStatusError as exc:
            # Some models (e.g. gpt-6.1-sol) refuse tools + reasoning_effort on Chat Completions.
            # Fall back to 'none' once and remember it for the rest of the process.
            if (
                "reasoning_effort" in kwargs
                and kwargs["reasoning_effort"] != "none"
                and _rejects_reasoning_with_tools(exc)
            ):
                log.warning(
                    "model %s rejected reasoning_effort=%s with tools; using 'none'",
                    self.model,
                    kwargs["reasoning_effort"],
                )
                self.reasoning_effort = "none"
                return await self._client.chat.completions.create(**{**kwargs, "reasoning_effort": "none"})
            raise

    async def stream(self, messages: list[Message], tools: list[ToolSpecJSON]) -> AsyncIterator[LLMEvent]:
        if self._missing_key:
            raise LLMError("OPENAI_API_KEY is not set. Put it in .env at the repository root.")
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tools:
            kwargs["tools"] = tools
        if self.reasoning_effort:
            kwargs["reasoning_effort"] = self.reasoning_effort
        try:
            stream = await self._create(kwargs)
            calls: dict[int, dict[str, str]] = {}
            finish_reason: str | None = None
            usage: dict[str, Any] = {}
            async for chunk in stream:
                if chunk.usage is not None:
                    usage = chunk.usage.model_dump(exclude_none=True)
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                delta = choice.delta
                if delta and delta.content:
                    yield TextDelta(delta.content)
                for tc in (delta.tool_calls if delta else None) or []:
                    c = calls.setdefault(tc.index, {"id": "", "name": "", "args": ""})
                    if tc.id:
                        c["id"] = tc.id
                    if tc.function and tc.function.name:
                        c["name"] += tc.function.name
                    if tc.function and tc.function.arguments:
                        c["args"] += tc.function.arguments
                if choice.finish_reason:
                    finish_reason = choice.finish_reason
            for c in calls.values():
                yield ToolCall(c["id"] or f"call_{uuid.uuid4().hex[:12]}", c["name"], c["args"] or "{}")
            yield Done(finish_reason=finish_reason, usage=usage)
        except openai.AuthenticationError as exc:
            raise LLMError("The API key was rejected by the provider.") from exc
        except openai.NotFoundError as exc:
            raise LLMError(f"Model '{self.model}' was not found at the provider.") from exc
        except openai.APIConnectionError as exc:
            raise LLMError("Could not reach the LLM provider.") from exc
        except openai.APIStatusError as exc:
            raise LLMError(f"Provider error {exc.status_code}: {exc.message}") from exc
