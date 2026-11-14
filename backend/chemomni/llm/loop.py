"""Agent loop (PLAN §3, §8): stream the LLM, run tools, emit SSE events, persist the turn."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from typing import Any

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

import chemomni.tools.chem_tools
import chemomni.tools.edit_tools
import chemomni.tools.generate_tools  # noqa: F401
from chemomni.llm.context import Snapshot, build_messages
from chemomni.llm.provider import Done, LLMError, LLMProvider, TextDelta, ToolCall
from chemomni.models import Artifact, Conversation, Message, ToolCallRecord, utcnow
from chemomni.tools.registry import REGISTRY, ToolContext, specs

MAX_TOOL_CALLS = 8
LLM_TIMEOUT_S = 60
TOOL_TIMEOUT_S = 30

Event = tuple[str, dict[str, Any]]


async def run_turn(
    *,
    session: AsyncSession,
    provider: LLMProvider,
    conversation: Conversation,
    user_text: str,
    snapshot: Snapshot,
    context_budget: int,
    services: dict[str, Any] | None = None,
) -> AsyncIterator[Event]:
    user = Message(conversation_id=conversation.id, role="user", content=[{"type": "text", "text": user_text}])
    assistant = Message(conversation_id=conversation.id, role="assistant", content=[], model=provider.model)
    session.add(user)
    session.add(assistant)
    if conversation.title == "New chat":
        conversation.title = user_text.strip().splitlines()[0][:60] or "New chat"
    conversation.updated_at = utcnow()
    session.add(conversation)
    await session.commit()

    yield (
        "message.start",
        {
            "message_id": assistant.id,
            "user_message_id": user.id,
            "conversation_id": conversation.id,
            "model": provider.model,
        },
    )

    history = list(
        (
            await session.exec(
                select(Message)
                .where(Message.conversation_id == conversation.id, Message.id != assistant.id)
                .order_by(Message.created_at)  # type: ignore[arg-type]
            )
        ).all()
    )
    messages = build_messages(history, snapshot, context_budget)
    blocks: list[dict[str, Any]] = []
    pending: list[Event] = []  # artifacts emitted by tools, flushed after each tool
    usage_total: dict[str, int] = {}
    tool_calls_used = 0
    stop_reason = "end_turn"

    async def emit_artifact(call_id: str, kind: str, payload: dict[str, Any]) -> str:
        art = Artifact(message_id=assistant.id, type=kind, payload={**payload, "call_id": call_id})
        session.add(art)
        await session.flush()
        pending.append(("artifact", {"artifact": {"id": art.id, "type": kind, "payload": art.payload}}))
        return art.id

    try:
        while True:
            budget_left = MAX_TOOL_CALLS - tool_calls_used
            tools = specs() if budget_left > 0 else []
            text, calls = "", []
            async with asyncio.timeout(LLM_TIMEOUT_S):
                async for ev in provider.stream(messages, tools):
                    if isinstance(ev, TextDelta):
                        text += ev.text
                        yield "text.delta", {"delta": ev.text}
                    elif isinstance(ev, ToolCall):
                        calls.append(ev)
                    elif isinstance(ev, Done):
                        for k, v in ev.usage.items():
                            if isinstance(v, int):
                                usage_total[k] = usage_total.get(k, 0) + v
            if text:
                blocks.append({"type": "text", "text": text})
            if budget_left <= 0:
                stop_reason = "max_tools"
                break
            if not calls:
                break

            messages.append(
                {
                    "role": "assistant",
                    "content": text or None,
                    "tool_calls": [
                        {
                            "id": c.id,
                            "type": "function",
                            "function": {"name": c.name, "arguments": c.arguments},
                        }
                        for c in calls
                    ],
                }
            )
            for call in calls:
                blocks.append({"type": "tool_use", "id": call.id, "name": call.name, "arguments": call.arguments})
                yield (
                    "tool.start",
                    {"call_id": call.id, "name": call.name, "arguments": _safe_json(call.arguments)},
                )
                if tool_calls_used >= MAX_TOOL_CALLS:
                    result, ok, duration_ms = (
                        {
                            "ok": False,
                            "errors": [f"tool budget of {MAX_TOOL_CALLS} calls per turn is used up"],
                        },
                        False,
                        0,
                    )
                else:
                    result, ok, duration_ms = await _run_tool(
                        call,
                        assistant.id,
                        snapshot,
                        emit_artifact,
                        workspace_id=conversation.workspace_id,
                        session=session,
                        emit_event=lambda name, data: pending.append((name, data)),
                        services=services or {},
                    )
                tool_calls_used += 1
                session.add(
                    ToolCallRecord(
                        message_id=assistant.id,
                        call_id=call.id,
                        name=call.name,
                        arguments=_safe_json(call.arguments),
                        result=result,
                        ok=ok,
                        duration_ms=duration_ms,
                    )
                )
                for ev in pending:
                    yield ev
                pending.clear()
                tool = REGISTRY.get(call.name)
                if tool and "errors" not in result:
                    summary = tool.summary(result)
                else:
                    summary = (result.get("errors") or ["failed"])[0]
                yield (
                    "tool.result",
                    {"call_id": call.id, "ok": ok, "summary": summary, "duration_ms": duration_ms},
                )
                content = json.dumps(result, ensure_ascii=False)
                blocks.append({"type": "tool_result", "tool_call_id": call.id, "content": content})
                messages.append({"role": "tool", "tool_call_id": call.id, "content": content})
    except LLMError as exc:
        stop_reason = "error"
        yield "error", {"code": "llm", "message": str(exc)}
    except TimeoutError:
        stop_reason = "error"
        yield "error", {"code": "timeout", "message": f"The model did not answer within {LLM_TIMEOUT_S} s."}
    finally:
        assistant.content = _close_dangling_calls(blocks)
        assistant.usage = usage_total or None
        assistant.stop_reason = stop_reason
        session.add(assistant)
        await session.commit()

    yield "message.done", {"message_id": assistant.id, "usage": usage_total, "stop_reason": stop_reason}


async def _run_tool(
    call: ToolCall,
    message_id: str,
    snapshot: Snapshot,
    emit_artifact: Any,
    *,
    workspace_id: str = "",
    session: Any = None,
    emit_event: Any = None,
    services: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], bool, int]:
    t0 = time.perf_counter()
    tool = REGISTRY.get(call.name)
    if tool is None:
        result: dict[str, Any] = {"ok": False, "errors": [f"unknown tool '{call.name}'"]}
    else:
        args = _safe_json(call.arguments)
        if not isinstance(args, dict):
            result = {"ok": False, "errors": ["arguments must be a JSON object"]}
        else:
            ctx = ToolContext(
                workspace_id=workspace_id,
                message_id=message_id,
                call_id=call.id,
                emit_artifact=lambda kind, payload: emit_artifact(call.id, kind, payload),
                snapshot=snapshot.model_dump(),
                emit_event=emit_event or (lambda name, data: None),
                session=session,
                services=services or {},
            )
            try:
                async with asyncio.timeout(TOOL_TIMEOUT_S):
                    result = await tool.run(args, ctx)
            except TimeoutError:
                result = {"ok": False, "errors": [f"tool timed out after {TOOL_TIMEOUT_S} s"]}
            except Exception as exc:  # a tool bug must not kill the turn
                result = {"ok": False, "errors": [f"tool failed: {type(exc).__name__}: {exc}"]}
    return result, bool(result.get("ok")), int((time.perf_counter() - t0) * 1000)


def _safe_json(text: str) -> Any:
    try:
        return json.loads(text or "{}")
    except json.JSONDecodeError:
        return {"_raw": text}


def _close_dangling_calls(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every tool_use needs a tool_result, or the history cannot be replayed to the provider."""
    answered = {b["tool_call_id"] for b in blocks if b["type"] == "tool_result"}
    out = list(blocks)
    for b in blocks:
        if b["type"] == "tool_use" and b["id"] not in answered:
            out.append(
                {
                    "type": "tool_result",
                    "tool_call_id": b["id"],
                    "content": '{"ok": false, "errors": ["interrupted"]}',
                }
            )
    return out
