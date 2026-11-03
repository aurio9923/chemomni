# ChemOmni

A chemical structure workspace that puts a ChemDraw-like editor ([Ketcher](https://github.com/epam/ketcher)) first and an LLM chat beside it. The AI computes properties, explains structures, edits them from natural language and proposes analogs — and its results land in the editor as new tabs.

**Status:** design complete, implementation starting. See [`docs/PLAN.md`](docs/PLAN.md) (Japanese) for the full plan, architecture and decision log.

## Principles

- Editor first, chat second. Tabs are the AI's output surface.
- Local-first, open source (MIT), LLM-provider agnostic (OpenAI first; any OpenAI-compatible server works).
- Light and fast.

## Stack

- Frontend: Vite, React 19, TypeScript, Tailwind + shadcn/ui, Zustand, Ketcher (standalone)
- Backend: Python 3.12, FastAPI, SQLModel + SQLite, RDKit, OpenAI SDK
