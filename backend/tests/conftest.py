from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest

from chemomni.config import Settings
from chemomni.main import create_app


@pytest.fixture
async def client(tmp_path: Path) -> AsyncIterator[httpx.AsyncClient]:
    settings = Settings(
        _env_file=None,  # never read the developer's .env in tests
        CHEMOMNI_MODEL="gpt-6.1-sol",
        CHEMOMNI_DB=tmp_path / "test.sqlite",
        CHEMOMNI_PROVIDER="fake",
        CHEMOMNI_STATIC_DIR=tmp_path / "no-frontend",
    )
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c
