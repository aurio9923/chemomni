"""Serve the built frontend with pre-compressed assets (PLAN §9).

Hashed files under ``/assets`` are immutable; ``.br`` / ``.gz`` siblings produced at build
time are served when the client accepts them, so the 11 MB Indigo WASM is never compressed
on the fly.
"""

from __future__ import annotations

import mimetypes
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response

IMMUTABLE = "public, max-age=31536000, immutable"
NO_CACHE = "no-cache"

mimetypes.add_type("application/wasm", ".wasm")
mimetypes.add_type("text/javascript", ".js")


def _pick_encoded(path: Path, accept_encoding: str) -> tuple[Path, str | None]:
    for enc, suffix in (("br", ".br"), ("gzip", ".gz")):
        if enc in accept_encoding:
            candidate = path.with_name(path.name + suffix)
            if candidate.is_file():
                return candidate, enc
    return path, None


def mount_frontend(app: FastAPI, root: Path) -> None:
    root = root.resolve()
    index = root / "index.html"

    @app.get("/{full_path:path}", include_in_schema=False)
    async def frontend(full_path: str, request: Request) -> Response:
        if full_path.startswith("api/"):
            raise HTTPException(404)
        target = (root / full_path).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            target = index  # SPA fallback
        served, encoding = _pick_encoded(target, request.headers.get("accept-encoding", ""))
        media_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        headers = {"Cache-Control": IMMUTABLE if "/assets/" in target.as_posix() else NO_CACHE}
        if encoding:
            headers["Content-Encoding"] = encoding
            headers["Vary"] = "Accept-Encoding"
        return FileResponse(served, media_type=media_type, headers=headers)
