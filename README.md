# ChemOmni

A chemical structure workspace that puts a ChemDraw-like editor ([Ketcher](https://github.com/epam/ketcher)) first and an LLM chat beside it. The AI computes properties, explains structures, edits them from natural language and proposes analogs — and its results land in the editor as new tabs.

**Status:** M3 (generation). Polish (import/export, packaging, performance) comes next in M4.

## What it does

- Editor tabs you create, rename, duplicate, close and reopen; autosaved to SQLite; a live property bar.
- Chat about the active tab: properties (RDKit), validation, similarity, substructure search.
- Edits in natural language (“add a fluorine para to the ester”), laid out on your own drawing, in a new tab or in place with Undo. Select atoms in the editor to point at them.
- Analog series: the AI designs candidates, RDKit validates and deduplicates them, and they open together on one grid tab aligned to the parent, with a sortable table of properties and similarity. Split any multi-molecule tab into one tab per molecule.
- Names to structures: OPSIN offline when installed, then PubChem.
- Models: OpenAI (Responses API), Anthropic Claude, or any OpenAI-compatible server (Ollama, vLLM), switchable from the chat header.

## Principles

- Editor first, chat second. Tabs are the AI's output surface.
- Local-first, open source (MIT), LLM-provider agnostic.
- Light and fast.

## Stack

- Frontend: Vite, React 19, TypeScript, Tailwind, Zustand, Ketcher (standalone)
- Backend: Python 3.12, FastAPI, SQLModel + SQLite, RDKit, OpenAI and Anthropic SDKs

## Development

Requirements: Python 3.12+ with [uv](https://docs.astral.sh/uv/), Node 22+ with pnpm. On Windows, use WSL2.

```bash
# backend (http://127.0.0.1:8000)
cd backend
cp ../.env.example ../.env     # .env at the repo root or in backend/ both work
uv sync                        # add --extra opsin for offline name lookup (needs Java)
uv run uvicorn chemomni.main:app --reload

# frontend (http://localhost:5173, proxies /api to the backend)
cd frontend
pnpm install
pnpm dev
```

Production-like run: `pnpm build` in `frontend/`, then start the backend; it serves `frontend/dist` with pre-compressed assets.

Name lookups that OPSIN cannot parse go to PubChem. Set `CHEMOMNI_PUBCHEM=false` to keep compound names (for example internal codes) on your machine.

Tests: `uv run pytest` (backend), `pnpm lint && pnpm test && pnpm build` (frontend), `e2e/smoke.py` against a running server started with `CHEMOMNI_PROVIDER=fake`.
