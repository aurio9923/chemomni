# ChemOmni

A chemical structure workspace that puts a ChemDraw-like editor ([Ketcher](https://github.com/epam/ketcher)) first and an LLM chat beside it. The AI computes properties, explains structures, edits them from natural language and proposes analogs — and its results land in the editor as new tabs.

**Status:** M5 is done: bioisostere transformations, on top of compound references (number your structures, then say "compound 1") and file import/export. Docker packaging and performance tuning are deferred.

## What it does

- Editor tabs you create, rename, duplicate, close and reopen; autosaved to SQLite; a live property bar.
- Chat about the active tab: properties (RDKit), validation, similarity, substructure search.
- Edits in natural language (“add a fluorine para to the ester”), laid out on your own drawing, in a new tab or in place with Undo. Select atoms in the editor to point at them.
- Part-of-molecule changes: select a substructure (say a phenyl) and ask for a pyridine or a mono-fluorination. Several changes can be combined (one F and one methyl, difluoro). Every position and combination is enumerated with RDKit (not guessed by the model), symmetric ones are merged, ortho/meta/para is named, and all variants open on one grid tab.
- Numbered compounds: write 1, 2, 3 … next to your structures with Ketcher's text tool (or press *Number compounds*), then say "use compound 1 as the query and search the others". The query is the drawing itself, including any-atom and atom-list atoms, so the model never writes SMARTS; numbers also work as arguments for properties, similarity and edits instead of copied SMILES, and an edit of several numbered compounds can be collected on one tab, each result drawn like its own source.
- Bioisosteres: ask for "bioisosteres of the carboxylic acid of compound 3" (or select a part and ask for replacements). The model only picks rules by id from a vetted table of 80 transformations (acid isosteres, amide and ester replacements, ring and heteroaryl swaps, linkers, substituents); RDKit finds every place each rule fits, merges symmetric and tautomeric duplicates and opens the products on a grid tab, each row labelled with its rule. The model never writes the replacement's SMILES. Rules live in `backend/chemomni/data/bioisosteres/` (one YAML per family) and are not edited by the app.
- Tautomers: ask for the tautomers of a molecule or its canonical tautomer. RDKit enumerates every tautomer (at most 20 are drawn, the canonical one first, the atoms where a proton moved highlighted), keeping stereochemistry and the charge state. Forms no chemist would draw (a ring that loses its aromaticity, an amide written as C(OH)=N or as an enol) are left out and counted; ask for "every form" to see them. "Canonical" is a consistent label for comparing structures, not a prediction of the form that predominates in water.
- Import and export: the import button opens SDF, molfile, SMILES list, CSV (with a `smiles` column), KET and CDXML files as new tabs. A file with several structures lands on one grid tab and is numbered 1, 2, 3 …, so "compound 3" works at once (Ketcher's own Open dialog stacks the records of an SDF on top of each other). The export button writes the active tab as SDF, CSV or a SMILES list with properties and the compound numbers; Ketcher's *Save as* still covers the structure formats (Molfile, KET, CDXML, InChI, SVG, PNG …).
- Proposals: the AI designs candidates, RDKit validates and deduplicates them, and they open together on one grid tab aligned to the parent, with a sortable table of properties and similarity. Split any multi-molecule tab into one tab per molecule.
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
