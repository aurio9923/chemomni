---
name: chemomni-dev
description: Project conventions and integration know-how for the ChemOmni repository (Ketcher editor in React, FastAPI + RDKit backend, OpenAI-compatible LLM tool loop over SSE). Use whenever writing or reviewing code in this repo, especially Ketcher integration, chat/SSE streaming, the tool registry, or performance-sensitive paths.
---

# ChemOmni development guide

The design source of truth is `docs/PLAN.md` (decision log in §12). This skill holds the
practical knowledge needed to implement it. Update it when you learn something the hard way.

## Repository conventions

- Monorepo: `backend/` (Python 3.12, uv) and `frontend/` (Node 22, pnpm). Develop on WSL2's
  own filesystem, never under `/mnt/c`.
- Backend: `uv add <pkg>` to add deps (never hand-edit `[project.dependencies]`), `uv run` for
  everything. Lint `uv run ruff check . && uv run ruff format --check .`, test `uv run pytest`.
  Ruff uses a selected rule set (E, F, I, B, UP, SIM, RUF), not `ALL`. Type checking is optional.
- Frontend: `pnpm tsc --noEmit`, `pnpm vitest run`, `pnpm build`.
- Tests and E2E never call a real LLM: set `CHEMOMNI_PROVIDER=fake` and script the
  `FakeProvider`.
- Commits are small; after M0, work on feature branches and open PRs.

## Performance rules (PLAN §10)

- Ketcher is loaded with `React.lazy`; the app shell must render before it.
- Never compress `.wasm` on the fly: the build pre-compresses assets (br + gz) and the
  backend serves hashed files with `Cache-Control: public, max-age=31536000, immutable`.
- Cache depictions and property rows by canonical SMILES (LRU).
- Debounce: autosave 500 ms, live property bar 300 ms.
- Keep imports in the FastAPI startup path light; startup budget is 2 s.

## Ketcher (pinned 3.18.0)

Packages: `ketcher-core`, `ketcher-react`, `ketcher-standalone` — pin all three to the same
exact version. `import 'ketcher-react/dist/index.css'` is mandatory.

```tsx
import { Editor } from 'ketcher-react'
import { StandaloneStructServiceProvider } from 'ketcher-standalone' // use the binaryWasm entry; see below

<Editor
  staticResourcesUrl=""
  structServiceProvider={provider}          // create once, module scope
  disableMacromoleculesEditor               // macro mode makes getSmiles/getMolfile throw
  errorHandler={(m) => console.error(m)}
  onInit={(ketcher) => bridge.attach(ketcher)}
/>
```

- Entry point: import `StandaloneStructServiceProvider` from `ketcher-standalone/dist/binaryWasm`
  (separate `.wasm`, worker via `new URL(..., import.meta.url)`). The subpath has no `types`
  in the package `exports`, so `src/ketcher/ketcher-standalone.d.ts` re-declares it.
- Ketcher 3.18 does not set `window.ketcher`. The bridge keeps the instance from `onInit` and
  exposes `window.chemomniKetcher` plus `body[data-ketcher-ready]` for E2E tests only.
- No `<StrictMode>`: Ketcher's editor does not tolerate double mounting.
- Vite 8 (verified): `define: { global: 'globalThis' }`, `optimizeDeps.exclude: ['ketcher-standalone']`,
  `worker.format: 'es'`, and the `events` npm package (ketcher-core imports Node's `events`).
  The manual `@mui`/`@emotion` chunk was not needed.
- Build output (M0): app shell 77 KB br; Ketcher JS ~1 MB br (lazy); Indigo WASM 2.5 MB br
  (immutable cache). The 3D viewer (miew + three, 264 KB br) is a separate lazy chunk.

API surface we rely on (all async unless noted):

| Need | Call |
|---|---|
| Replace document | `ketcher.setMolecule(str)` — SMILES / molfile / KET auto-detected; pushes onto undo history and fires `change` |
| Merge into document | `ketcher.addFragment(str)` |
| Read | `getKet()`, `getMolfile('v3000')`, `getSmiles()`, `getInchi()` |
| Image | `ketcher.generateImage(data, { outputFormat: 'png' \| 'svg' })` |
| Changes | `ketcher.editor.subscribe('change', handler)` (sync subscribe; returns a subscriber to `unsubscribe`) |
| Selection | `ketcher.editor.subscribe('selectionChange', h)`, `ketcher.editor.selection()` → `{ atoms?: number[], bonds?: number[], ... }` |
| History | `ketcher.editor.clearHistory()`; history is capped at 32 entries |

Tab switching with one shared instance:

```ts
loading = true                     // change handler must ignore events while true
await saveCurrent(await k.getKet())
await k.setMolecule(next.content)  // KET or molfile
k.editor.clearHistory()            // only on tab switch, NOT on AI replace (keeps undo)
loading = false
```

Atom indices in `selection()` are Ketcher struct ids. Before sending them to the backend,
map them to molfile order (export the molfile and match by position) — never assume they
are equal.

## Backend shape

```
chemomni/api/       routers (thin; validation + calls into services)
chemomni/chem/      pure RDKit functions, no FastAPI imports, fully unit-tested
chemomni/llm/       provider protocol, OpenAI + Fake providers, loop, context builder, prompts
chemomni/tools/     @tool registry; each tool = Pydantic input model + async function
```

### Tool registry

```python
class ComputePropertiesIn(BaseModel):
    smiles: list[str] = Field(..., max_length=50, description="SMILES strings")

@tool(name="compute_properties", description="Compute standard properties for molecules.")
async def compute_properties(args: ComputePropertiesIn, ctx: ToolContext) -> ToolResult: ...
```

- JSON Schema comes from `Model.model_json_schema()`; descriptions on fields matter, the LLM
  reads them.
- Tools return a compact JSON payload for the LLM plus optional UI side effects through
  `ctx` (`ctx.emit_artifact(...)`, `ctx.create_tab(...)`). SVGs never go into the LLM payload.
- Invalid input returns `{"ok": false, "errors": [...]}`; do not raise for chemistry errors.

### OpenAI-compatible streaming with tool calls (Chat Completions)

```python
stream = await client.chat.completions.create(
    model=settings.model, messages=msgs, tools=specs, stream=True,
    stream_options={"include_usage": True},
)
calls: dict[int, dict] = {}
async for chunk in stream:
    if not chunk.choices:              # final usage-only chunk
        usage = chunk.usage; continue
    delta = chunk.choices[0].delta
    if delta.content:
        yield TextDelta(delta.content)
    for tc in delta.tool_calls or []:  # arguments arrive in fragments, keyed by index
        c = calls.setdefault(tc.index, {"id": "", "name": "", "args": ""})
        if tc.id: c["id"] = tc.id
        if tc.function and tc.function.name: c["name"] += tc.function.name
        if tc.function and tc.function.arguments: c["args"] += tc.function.arguments
    if chunk.choices[0].finish_reason == "tool_calls":
        for c in calls.values(): yield ToolCall(c["id"], c["name"], c["args"])
```

- Append the assistant message with `tool_calls` before the `role: "tool"` results, each with
  the matching `tool_call_id`.
- Some OpenAI-compatible servers (Ollama, vLLM) omit `id` or usage; generate an id if missing.
- `reasoning_effort` is optional; only send it when configured.

### SSE

- Server: `sse_starlette.EventSourceResponse` with an async generator yielding
  `{"event": name, "data": json.dumps(payload)}`; set `ping=15` for keepalive and stop work
  when `await request.is_disconnected()`.
- Client: `fetch` + `response.body.getReader()` and a small line parser (`EventSource` cannot
  POST). Event names and payloads are typed in `frontend/src/lib/types.ts` and must match
  PLAN §5.

## RDKit notes for this project

- Use the vendored `rdkit` and `cheminformatics` skills for API details.
- Fingerprints: `rdFingerprintGenerator.GetMorganGenerator(radius=2, fpSize=2048)`.
- Coordinates aligned to a parent: `rdFMCS.FindMCS([parent, child], ringMatchesRingOnly=True,
  completeRingsOnly=True, timeout=2)` then `AllChem.GenerateDepictionMatching2DStructure`;
  fall back to `rdDepictor.Compute2DCoords`.
- SA score lives in `rdkit.Contrib.SA_Score.sascorer` (add `RDConfig.RDContribDir` to the path
  once at import time).
- Molfile V3000 out: `Chem.MolToV3KMolBlock(mol)`.
