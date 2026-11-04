"""Provider-neutral streaming interface (PLAN §8).

Messages use the OpenAI Chat Completions shape as the internal format because it is the
common denominator of OpenAI-compatible servers (Ollama, vLLM, LM Studio). Other providers
get an adapter that converts to and from this shape.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Protocol

Message = dict[str, Any]
ToolSpecJSON = dict[str, Any]


@dataclass(frozen=True)
class TextDelta:
    text: str


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: str  # raw JSON text as produced by the model


@dataclass(frozen=True)
class Done:
    finish_reason: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)


LLMEvent = TextDelta | ToolCall | Done


class LLMError(RuntimeError):
    """A provider failure that should be shown to the user."""


class LLMProvider(Protocol):
    model: str

    def stream(self, messages: list[Message], tools: list[ToolSpecJSON]) -> AsyncIterator[LLMEvent]: ...
