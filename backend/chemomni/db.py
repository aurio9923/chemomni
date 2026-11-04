from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

from chemomni.config import Settings
from chemomni.models import LOCAL_USER, Conversation, Workspace

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def _set_pragmas(dbapi_conn, _record) -> None:  # type: ignore[no-untyped-def]
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.close()


async def init_db(settings: Settings) -> None:
    global _engine, _sessionmaker
    kwargs: dict = {}
    if str(settings.db_path) == ":memory:":
        kwargs = {"poolclass": StaticPool, "connect_args": {"check_same_thread": False}}
    else:
        settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    _engine = create_async_engine(settings.db_url, **kwargs)
    event.listen(_engine.sync_engine, "connect", _set_pragmas)
    _sessionmaker = async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)
    async with _engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)


async def close_db() -> None:
    global _engine
    if _engine is not None:
        await _engine.dispose()
        _engine = None


async def get_session() -> AsyncIterator[AsyncSession]:
    assert _sessionmaker is not None, "init_db() was not called"
    async with _sessionmaker() as session:
        yield session


def new_session() -> AsyncSession:
    """A session owned by the caller (used by the SSE generator, which outlives the request scope)."""
    assert _sessionmaker is not None, "init_db() was not called"
    return _sessionmaker()


async def ensure_default_workspace(session: AsyncSession) -> tuple[Workspace, Conversation]:
    """Single-workspace MVP: create the default workspace and conversation on first use."""
    ws = (await session.exec(select(Workspace).where(Workspace.user_id == LOCAL_USER))).first()
    if ws is None:
        ws = Workspace()
        session.add(ws)
        await session.flush()
    conv = (
        await session.exec(
            select(Conversation).where(Conversation.workspace_id == ws.id).order_by(Conversation.updated_at.desc())  # type: ignore[attr-defined]
        )
    ).first()
    if conv is None:
        conv = Conversation(workspace_id=ws.id)
        session.add(conv)
    await session.commit()
    return ws, conv
