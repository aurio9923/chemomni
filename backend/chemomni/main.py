from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from chemomni import db
from chemomni.api import chat, chem, tabs
from chemomni.api.chat import make_provider
from chemomni.chem.names import NameResolver
from chemomni.config import Settings, get_settings
from chemomni.static import mount_frontend


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        await db.init_db(settings)
        yield
        await db.close_db()

    app = FastAPI(title="ChemOmni", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.provider = make_provider(settings)
    app.state.providers = {}
    # shared by every chat turn: one cache and one PubChem rate limit for the whole app
    app.state.services = {"names": NameResolver(pubchem=settings.pubchem)}
    app.include_router(tabs.router)
    app.include_router(chem.router)
    app.include_router(chat.router)

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/config")
    async def config() -> dict[str, str | bool | list[str] | None]:
        return {
            "provider": settings.provider,
            "model": settings.model,
            "models": settings.model_list,
            "api_key_set": bool(settings.openai_api_key) or bool(settings.openai_base_url),
            "anthropic_key_set": bool(settings.anthropic_api_key),
        }

    static_dir = settings.static_dir or Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if static_dir.is_dir():
        mount_frontend(app, static_dir)
    return app


app = create_app()
