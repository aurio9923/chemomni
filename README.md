# ChemOmni

A chemical structure workspace that puts a ChemDraw-like editor ([Ketcher](https://github.com/epam/ketcher)) first and an LLM chat beside it. The AI computes properties, explains structures, edits them from natural language and proposes analogs — and its results land in the editor as new tabs.

**Status:** M0 (skeleton) — editor, tabs, autosave and live properties work; chat arrives in M1. See [`docs/PLAN.md`](docs/PLAN.md) (Japanese) for the full plan, architecture and decision log.

## Principles

- Editor first, chat second. Tabs are the AI's output surface.
- Local-first, open source (MIT), LLM-provider agnostic (OpenAI first; any OpenAI-compatible server works).
- Light and fast.

## Stack

- Frontend: Vite, React 19, TypeScript, Tailwind + shadcn/ui, Zustand, Ketcher (standalone)
- Backend: Python 3.12, FastAPI, SQLModel + SQLite, RDKit, OpenAI SDK

## Development

Requirements: Python 3.12+ with [uv](https://docs.astral.sh/uv/), Node 22+ with pnpm. On Windows, use WSL2 and keep the repo on the Linux filesystem.

```bash
# backend (http://127.0.0.1:8000)
cd backend
cp ../.env.example ../.env     # .env at the repo root or in backend/ both work
uv sync
uv run uvicorn chemomni.main:app --reload

# frontend (http://localhost:5173, proxies /api to the backend)
cd frontend
pnpm install
pnpm dev
```

Production-like run: `pnpm build` in `frontend/`, then start the backend; it serves `frontend/dist` with pre-compressed assets.

Tests: `uv run pytest` (backend), `pnpm lint && pnpm build` (frontend), `e2e/smoke_m0.py` against a running server.
