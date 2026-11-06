from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession
from sse_starlette import EventSourceResponse

from chemomni import db
from chemomni.config import Settings
from chemomni.db import ensure_default_workspace, get_session
from chemomni.llm.anthropic_provider import AnthropicProvider
from chemomni.llm.context import Snapshot
from chemomni.llm.fake_provider import FakeProvider
from chemomni.llm.loop import run_turn
from chemomni.llm.openai_provider import OpenAIChatProvider
from chemomni.llm.openai_responses_provider import OpenAIResponsesProvider
from chemomni.llm.provider import LLMProvider
from chemomni.models import Artifact, Conversation, Message

router = APIRouter(prefix="/api", tags=["chat"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]


def make_provider(settings: Settings, model: str | None = None) -> LLMProvider:
    if settings.provider == "fake":
        return FakeProvider()
    model = model or settings.model
    if settings.is_anthropic(model):
        return AnthropicProvider(api_key=settings.anthropic_api_key, model=model)
    cls = OpenAIResponsesProvider if settings.use_responses_api else OpenAIChatProvider
    return cls(
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url,
        model=model,
        reasoning_effort=settings.reasoning_effort,
    )


def provider_for(request: Request, model: str | None) -> LLMProvider:
    """The default provider lives in app.state.provider (tests replace it); others are cached per model."""
    settings: Settings = request.app.state.settings
    if not model or model == settings.model:
        return request.app.state.provider
    if model not in settings.model_list:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"model '{model}' is not in CHEMOMNI_MODELS")
    cache: dict[str, LLMProvider] = request.app.state.providers
    if model not in cache:
        cache[model] = make_provider(settings, model)
    return cache[model]


class ChatRequest(BaseModel):
    conversation_id: str
    content: str = Field(..., min_length=1, max_length=20_000)
    snapshot: Snapshot = Field(default_factory=Snapshot)
    model: str | None = Field(default=None, description="One of CHEMOMNI_MODELS; default CHEMOMNI_MODEL.")


@router.post("/chat")
async def chat(body: ChatRequest, request: Request) -> EventSourceResponse:
    provider = provider_for(request, body.model)
    settings: Settings = request.app.state.settings

    async with db.new_session() as check:
        if await check.get(Conversation, body.conversation_id) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "conversation not found")

    async def events() -> AsyncIterator[dict[str, Any]]:
        async with db.new_session() as session:
            conversation = await session.get(Conversation, body.conversation_id)
            assert conversation is not None
            async for name, data in run_turn(
                session=session,
                provider=provider,
                conversation=conversation,
                user_text=body.content,
                snapshot=body.snapshot,
                context_budget=settings.context_budget,
                services=request.app.state.services,
            ):
                yield {"event": name, "data": json.dumps(data, ensure_ascii=False)}

    return EventSourceResponse(events(), ping=15)


@router.post("/conversations", status_code=status.HTTP_201_CREATED)
async def new_conversation(session: SessionDep) -> dict[str, Any]:
    ws, _ = await ensure_default_workspace(session)
    conv = Conversation(workspace_id=ws.id)
    session.add(conv)
    await session.commit()
    return {"id": conv.id, "title": conv.title}


@router.get("/conversations/{conversation_id}/messages")
async def list_messages(conversation_id: str, session: SessionDep) -> dict[str, Any]:
    if await session.get(Conversation, conversation_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "conversation not found")
    rows = (
        await session.exec(
            select(Message).where(Message.conversation_id == conversation_id).order_by(Message.created_at)  # type: ignore[arg-type]
        )
    ).all()
    ids = [m.id for m in rows]
    arts = (
        await session.exec(
            select(Artifact).where(Artifact.message_id.in_(ids)).order_by(Artifact.created_at)  # type: ignore[attr-defined]
        )
    ).all()
    by_message: dict[str, list[dict[str, Any]]] = {}
    for a in arts:
        by_message.setdefault(a.message_id, []).append({"id": a.id, "type": a.type, "payload": a.payload})
    return {
        "messages": [
            {
                "id": m.id,
                "role": m.role,
                "blocks": m.content,
                "artifacts": by_message.get(m.id, []),
                "stop_reason": m.stop_reason,
                "model": m.model,
                "created_at": m.created_at.isoformat(),
            }
            for m in rows
        ]
    }
