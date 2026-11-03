from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import func
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from chemomni.db import ensure_default_workspace, get_session
from chemomni.models import Tab, TabCreate, TabRead, TabUpdate, Workspace, WorkspaceRead, utcnow

router = APIRouter(prefix="/api", tags=["tabs"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


async def _workspace(session: AsyncSession) -> Workspace:
    ws, _ = await ensure_default_workspace(session)
    return ws


async def _next_position(session: AsyncSession, workspace_id: str) -> int:
    current = (
        await session.exec(select(func.max(Tab.position)).where(Tab.workspace_id == workspace_id))
    ).one()
    return (current or 0) + 1


async def _untitled_name(session: AsyncSession, workspace_id: str) -> str:
    count = (
        await session.exec(select(func.count()).select_from(Tab).where(Tab.workspace_id == workspace_id))
    ).one()
    return f"Untitled {count + 1}"


async def create_tab(
    session: AsyncSession,
    workspace_id: str,
    *,
    name: str | None,
    content_format: str = "ket",
    content: str = "",
    smiles: list[str] | None = None,
    source: str = "user",
    origin_message_id: str | None = None,
) -> Tab:
    """Shared by the REST API and (later) the AI tools."""
    tab = Tab(
        workspace_id=workspace_id,
        name=(name or "").strip() or await _untitled_name(session, workspace_id),
        content_format=content_format,
        content=content,
        smiles_cache=smiles or [],
        source=source,
        origin_message_id=origin_message_id,
        position=await _next_position(session, workspace_id),
    )
    session.add(tab)
    await session.commit()
    await session.refresh(tab)
    return tab


async def _get_tab(session: AsyncSession, tab_id: str) -> Tab:
    tab = await session.get(Tab, tab_id)
    if tab is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "tab not found")
    return tab


@router.get("/workspace", response_model=WorkspaceRead)
async def get_workspace(session: SessionDep) -> WorkspaceRead:
    ws, conv = await ensure_default_workspace(session)
    tabs = (
        await session.exec(
            select(Tab)
            .where(Tab.workspace_id == ws.id, Tab.is_deleted == False)  # noqa: E712
            .order_by(Tab.position)  # type: ignore[arg-type]
        )
    ).all()
    if not tabs:
        tabs = [await create_tab(session, ws.id, name=None)]
    return WorkspaceRead(
        id=ws.id,
        name=ws.name,
        conversation_id=conv.id,
        tabs=[TabRead.model_validate(t) for t in tabs],
    )


@router.post("/tabs", response_model=TabRead, status_code=status.HTTP_201_CREATED)
async def post_tab(body: TabCreate, session: SessionDep) -> Tab:
    ws = await _workspace(session)
    return await create_tab(
        session,
        ws.id,
        name=body.name,
        content_format=body.content_format,
        content=body.content,
        smiles=body.smiles,
    )


@router.get("/tabs/closed", response_model=list[TabRead])
async def closed_tabs(session: SessionDep, limit: int = 20) -> list[Tab]:
    ws = await _workspace(session)
    rows = await session.exec(
        select(Tab)
        .where(Tab.workspace_id == ws.id, Tab.is_deleted == True)  # noqa: E712
        .order_by(Tab.deleted_at.desc())  # type: ignore[union-attr]
        .limit(min(limit, 100))
    )
    return list(rows.all())


@router.get("/tabs/{tab_id}", response_model=TabRead)
async def get_tab(tab_id: str, session: SessionDep) -> Tab:
    return await _get_tab(session, tab_id)


@router.patch("/tabs/{tab_id}", response_model=TabRead)
async def patch_tab(tab_id: str, body: TabUpdate, session: SessionDep) -> Tab:
    tab = await _get_tab(session, tab_id)
    if body.revision != tab.revision:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={"message": "revision mismatch", "current_revision": tab.revision},
        )
    if body.name is not None:
        tab.name = body.name.strip() or tab.name
    if body.content is not None:
        tab.content = body.content
        tab.content_format = body.content_format or tab.content_format
    if body.smiles is not None:
        tab.smiles_cache = body.smiles
    tab.revision += 1
    tab.updated_at = utcnow()
    session.add(tab)
    await session.commit()
    await session.refresh(tab)
    return tab


@router.delete("/tabs/{tab_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_tab(tab_id: str, session: SessionDep) -> Response:
    tab = await _get_tab(session, tab_id)
    tab.is_deleted = True
    tab.deleted_at = utcnow()
    session.add(tab)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/tabs/{tab_id}/restore", response_model=TabRead)
async def restore_tab(tab_id: str, session: SessionDep) -> Tab:
    tab = await _get_tab(session, tab_id)
    if tab.is_deleted:
        tab.is_deleted = False
        tab.deleted_at = None
        tab.position = await _next_position(session, tab.workspace_id)
        session.add(tab)
        await session.commit()
        await session.refresh(tab)
    return tab


@router.post("/tabs/{tab_id}/duplicate", response_model=TabRead, status_code=status.HTTP_201_CREATED)
async def duplicate_tab(tab_id: str, session: SessionDep) -> Tab:
    src = await _get_tab(session, tab_id)
    return await create_tab(
        session,
        src.workspace_id,
        name=f"{src.name} (copy)",
        content_format=src.content_format,
        content=src.content,
        smiles=list(src.smiles_cache),
    )
