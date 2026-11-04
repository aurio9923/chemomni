"""Tool registry (PLAN §6): Pydantic input model + async function, JSON Schema generated."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError

ToolResult = dict[str, Any]
Emit = Callable[[str, dict[str, Any]], Awaitable[None]]


@dataclass
class ToolContext:
    """What a tool may touch besides its arguments."""

    workspace_id: str
    message_id: str
    call_id: str
    emit_artifact: Callable[[str, dict[str, Any]], Awaitable[str]]
    snapshot: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_model: type[BaseModel]
    fn: Callable[[Any, ToolContext], Awaitable[ToolResult]]
    summary: Callable[[ToolResult], str] = lambda r: "done"

    def spec(self) -> dict[str, Any]:
        schema = self.input_model.model_json_schema()
        schema.pop("title", None)
        return {
            "type": "function",
            "function": {"name": self.name, "description": self.description, "parameters": schema},
        }

    async def run(self, arguments: dict[str, Any], ctx: ToolContext) -> ToolResult:
        try:
            args = self.input_model.model_validate(arguments)
        except ValidationError as exc:
            return {
                "ok": False,
                "errors": [e["msg"] + f" at {'.'.join(map(str, e['loc']))}" for e in exc.errors()],
            }
        return await self.fn(args, ctx)


REGISTRY: dict[str, Tool] = {}


def tool(
    *, name: str, description: str, summary: Callable[[ToolResult], str] | None = None
) -> Callable[[Callable[..., Awaitable[ToolResult]]], Callable[..., Awaitable[ToolResult]]]:
    def decorator(fn: Callable[..., Awaitable[ToolResult]]) -> Callable[..., Awaitable[ToolResult]]:
        input_model = fn.__annotations__["args"]
        if isinstance(input_model, str):  # postponed annotations
            input_model = fn.__globals__[input_model]
        REGISTRY[name] = Tool(
            name=name,
            description=description,
            input_model=input_model,
            fn=fn,
            summary=summary or (lambda r: "done"),
        )
        return fn

    return decorator


def specs() -> list[dict[str, Any]]:
    return [t.spec() for t in REGISTRY.values()]
