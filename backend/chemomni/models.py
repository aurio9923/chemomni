"""SQLModel tables (PLAN §4)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import JSON, Column, Text
from sqlmodel import Field, SQLModel

LOCAL_USER = "local"


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def utcnow() -> datetime:
    return datetime.now(UTC)


class Workspace(SQLModel, table=True):
    id: str = Field(default_factory=lambda: new_id("ws"), primary_key=True)
    user_id: str = Field(default=LOCAL_USER, index=True)
    name: str = "Workspace"
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class Tab(SQLModel, table=True):
    id: str = Field(default_factory=lambda: new_id("tab"), primary_key=True)
    workspace_id: str = Field(foreign_key="workspace.id", index=True)
    name: str
    kind: str = "molecules"
    source: str = "user"  # user | ai
    origin_message_id: str | None = None
    content_format: str = "ket"  # ket | mol
    content: str = Field(default="", sa_column=Column(Text, nullable=False, default=""))
    smiles_cache: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    revision: int = 0
    position: int = 0
    is_deleted: bool = Field(default=False, index=True)
    deleted_at: datetime | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class Conversation(SQLModel, table=True):
    id: str = Field(default_factory=lambda: new_id("conv"), primary_key=True)
    workspace_id: str = Field(foreign_key="workspace.id", index=True)
    title: str = "New chat"
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class Message(SQLModel, table=True):
    id: str = Field(default_factory=lambda: new_id("msg"), primary_key=True)
    conversation_id: str = Field(foreign_key="conversation.id", index=True)
    role: str  # user | assistant | tool
    content: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    model: str | None = None
    usage: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    stop_reason: str | None = None
    created_at: datetime = Field(default_factory=utcnow)


class ToolCallRecord(SQLModel, table=True):
    __tablename__ = "tool_call"
    id: str = Field(default_factory=lambda: new_id("tc"), primary_key=True)
    message_id: str = Field(foreign_key="message.id", index=True)
    call_id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    result: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    ok: bool = True
    duration_ms: int = 0
    created_at: datetime = Field(default_factory=utcnow)


class Artifact(SQLModel, table=True):
    id: str = Field(default_factory=lambda: new_id("art"), primary_key=True)
    message_id: str = Field(foreign_key="message.id", index=True)
    type: str  # structure | structure_set | diff | table
    payload: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    tab_id: str | None = None
    created_at: datetime = Field(default_factory=utcnow)


# ---- API schemas -----------------------------------------------------------

ContentFormat = Literal["ket", "mol"]


class TabRead(SQLModel):
    id: str
    name: str
    kind: str
    source: str
    origin_message_id: str | None
    content_format: str
    content: str
    smiles_cache: list[str]
    revision: int
    position: int
    is_deleted: bool
    created_at: datetime
    updated_at: datetime


class TabCreate(SQLModel):
    name: str | None = None
    content_format: ContentFormat = "ket"
    content: str = ""
    smiles: list[str] = []


class TabUpdate(SQLModel):
    revision: int
    name: str | None = None
    content_format: ContentFormat | None = None
    content: str | None = None
    smiles: list[str] | None = None


class WorkspaceRead(SQLModel):
    id: str
    name: str
    conversation_id: str
    tabs: list[TabRead]
