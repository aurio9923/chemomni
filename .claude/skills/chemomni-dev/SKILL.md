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
- Frontend: `pnpm tsc --noEmit`, `pnpm vitest run`, `pnpm build`. There is no prettier config: never run
  `npx prettier` (it reformats whole files); follow the surrounding style (no semicolons, single quotes).
- Tests and E2E never call a real LLM: set `CHEMOMNI_PROVIDER=fake` and script the
  `FakeProvider`. Its rule-based mode reacts to keywords (`pyridine`, `fluor`, `analog`, `replace`,
  `methyl`, `highlight`, and `compound N` + `substructure`); E2E relies on them.
- Run E2E with the preinstalled Playwright (`python3 e2e/smoke.py <url> <png> [grid.png] [scan.png] [search.png]`);
  `uv run --with playwright` pulls a newer version that cannot find the installed browser.
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

**Known Ketcher 3.18 bug (verified):** a read (`getSmiles`, `getKet`, ...) issued while a
document is being loaded with `setMolecule` can stay pending forever. `bridge.ts` therefore
serializes every operation through one queue and retries reads with a 1.5 s timeout. Never call
the Ketcher instance directly from components or tests; tests use `window.chemomniBridge`.

**Atom numbering (verified):** the LLM and backend use 1-based molfile atom numbers; Ketcher
selection uses struct atom ids. Molfiles come from Indigo and can be ordered per fragment, so
`bridge.ts` matches atoms by element and position (Ketcher's y axis is flipped) — see
`matchAtoms`, `selectionAsMolfileAtoms`, `selectMolfileAtoms`. Never map by index.

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

### Which OpenAI API

`CHEMOMNI_OPENAI_API=auto` uses the **Responses API** (`openai_responses_provider.py`) for
api.openai.com and **Chat Completions** (`openai_provider.py`) when `OPENAI_BASE_URL` is set.
Verified: gpt-6.1-sol rejects function tools on Chat Completions with any `reasoning_effort`
and rejects `reasoning_effort="none"`. Internal messages stay in Chat Completions shape; the
Responses adapter converts them (`system` → `developer`, tool calls → `function_call` /
`function_call_output` items) and maps `response.output_text.delta`,
`response.output_item.done` (function_call) and `response.completed` back to LLM events.

### Anthropic (Claude models)

`anthropic_provider.py`, chosen when the model name starts with `claude-`. All `system`
messages go to the `system` parameter (static prompt first with `cache_control`), tool calls
become `tool_use` blocks, tool results become `tool_result` blocks in one user turn. The
anthropic SDK (2.x) uses its own `httpx2` fork: test with `httpx2.MockTransport`, not `httpx`.

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
- Ketcher molfiles use bond length ~1.0, RDKit's depictor 1.5, CoordGen 1.0. `chem/edit.py` scales the
  template to 1.5, matches with RDKit's depictor (CoordGen forced off — depict.py turns it on
  globally), then scales back. Mixing scales gives long new bonds and bent rings (seen in real use).
- Coordinates aligned to a parent: `rdFMCS.FindMCS([parent, child], ringMatchesRingOnly=True,
  completeRingsOnly=True, timeout=2)` then `AllChem.GenerateDepictionMatching2DStructure`;
  fall back to `rdDepictor.Compute2DCoords`.
- AI tabs use Ketcher's scale (mean bond length 1.0): `chem/layout.py` (`to_ketcher_scale`, `grid`,
  `layout_smiles`). `chem/generate.py` aligns every proposal to the parent's template and shifts all of
  them by the template's centre, so the shared core sits at the same spot of each grid cell. `align()`
  already returns the template's scale; rescaling its output again moves the core slightly.
- One grid, several parents (PLAN Q58): `evaluate(parent, candidates, molfile)` takes `(smiles, rationale)`
  or `(smiles, rationale, parent_smiles)`; a candidate's own parent wins over the default parent. Without
  any named parent the layout follows the most similar structure on the tab (`_closest_on_tab`, Morgan
  Tanimoto >= 0.3, layout only, no similarity column); unrelated ideas get a plain layout. Each parent has
  its own template and each candidate is centred on its own template, so every result keeps the drawing
  of its own parent. The grid has a parent card and similarity only when all candidates share one parent;
  grid items always carry `parent_smiles`. Tests: `tests/test_series_layout.py` (drift of MCS atoms after
  removing a translation; the plain layout is the control and must drift > 0.5).
- Position scans (`chem/scan.py`, tool `scan_positions`): the LLM only picks the changes (`substitute` a
  group for H, or `replace_atom` to an element; `changes` takes up to three and they are combined: every
  way of giving each change its own atom, identical changes not counted in both orders, results merged by
  canonical SMILES). Combinations must never be written by the model: "F and Me on a phenyl" came out as
  8 of 10 when it did (see `test_fluoro_plus_methyl_on_a_phenyl_misses_nothing`, whose expected set is
  built by plain loops, not by `scan`). RDKit enumerates positions on the selected atoms
  (molfile numbers, mapped through an `_orig` atom property so removed explicit H cannot shift them),
  merges symmetric results and names ortho/meta/para for a ring with one attachment point. Substituted
  aromatic atoms (3 connections) are not positions for an element swap. For a swap the grid layout uses
  `align(..., any_element=True)` (topology-only MCS) so the ring keeps the parent's drawing and only the
  changed atom is highlighted. All generating tools end in `present_candidates()` (validate, grid tab, card).
- Tautomers (PLAN Q63, `chem/tautomers.py`, tool `tautomers`): `rdMolStandardize.TautomerEnumerator` with
  `SetRemoveSp3Stereo(False)` and `SetRemoveBondStereo(False)`. With RDKit's defaults the canonical tautomer of
  (S)-ibuprofen, L-alanine or (S)-thalidomide has lost its stereocentre (`tests/test_tautomers.py` keeps a control that
  shows it). The charge state is left alone (tautomers, not protomers); a salt is refused. Enumeration is bounded: the
  transform limit (1000) and a 5 s deadline through a `TautomerEnumeratorCallback` subclass (keep a reference to the
  callback object); `result.status` other than `Completed` means a partial list. `PickCanonical(result)` saves a second
  enumeration. The canonical tautomer is a scoring-based label, not the predominant form: barbituric acid's is an aromatic
  enol. The grid highlight for tautomers cannot use an element comparison (same heavy-atom skeleton): `align(...,
  bond_changes=True)` matches by topology and marks atoms whose H count, charge or bond types differ, choosing among
  equal mappings the one that changes the fewest atoms. In a multi-turn FakeProvider test, `provider.calls[i]["messages"]`
  is one shared list, so read all tool results from `provider.calls[-1]["messages"]`, not `[-1]` of an earlier call.
- Unfavoured tautomers (PLAN Q64): RDKit's enumerator returns every form its H-shift rules reach, including ones no
  chemist draws. `unfavoured_reasons(drawn, tautomer)` marks them: a ring aromatic in the drawing that is not aromatic
  in the tautomer (checked ring by ring on the ring's bonds; the enumerator keeps the atom order, so indices can be
  compared), and groups in `UNFAVOURED_GROUPS` that the tautomer has more of than the drawing (imidic acid C(OH)=N,
  enol of an acid derivative, C(OH)-N=O of a hydroxamic acid; the carbon is `!a`, so 2-hydroxypyridine stays). Do not
  compare the number of aromatic rings with the canonical form: that hides the enols of cyclic 1,3-diketones and
  tetramic acids. The canonical tautomer can itself be unfavoured (hydroxamic acids: RDKit scores C(OH)=N-OH highest).
  To add a class, add one SMARTS and one sentence, plus a hand-written case and a neighbour that must stay in
  `tests/test_tautomers.py`.
- `split_molfile` does not sanitize (explicit H and odd valences survive). KET tabs cannot be read by
  RDKit: the client sends the editor's molfile with `POST /api/tabs/{id}/split`.
- Names: `chem/names.py` tries OPSIN (`uv sync --extra opsin`, needs Java; `py2opsin` returns `""`
  and warns on failure) and then PubChem PUG REST. Since PubChem's 2025 rename the isomeric property is
  `SMILES` (`ConnectivitySMILES` has no stereo); read `IsomericSMILES` as a fallback. Keep requests at
  ≤ 5/s (one `NameResolver` per app in `app.state.services["names"]`). Tests mock it with
  `httpx.MockTransport`; the sandbox cannot reach PubChem.
- Numbered compounds (`chem/compounds.py`, `tools/refs.py`, PLAN Q57). A number is a Ketcher text object
  written next to a structure. The client sends the KET with every chat turn; `build_compounds(ket, molfile)`
  reads molecule boxes and texts from the KET, gives each structure the nearest label (nearest pairs first,
  one label per structure, ambiguity and duplicates become notes) and matches KET molecules to molfile
  fragments by atom coordinates (never by order). The LLM sees `[1] SMILES` in `<compounds>` and passes
  `ref` / `refs` / `query_ref` / `target_refs` / `ref_a` / `ref_b` / `parent_ref`; the backend resolves them.
  `substructure_search(query_ref=...)` uses the drawn molecule as the query (`GetSubstructMatches`): elements and
  bond orders, no H counts or charges, no stereo. Ketcher facts (verified in 3.18, fixtures in
  `backend/tests/fixtures/`): KET text nodes are `{type:'text', boundingBox, paragraphs[].parts[].text}`; KET
  and V3000 coordinates are identical, y points up; `setMolecule` accepts text nodes in that shape (and in the
  older `data.content` Lexical shape), so "Number compounds" is `POST /api/chem/number-compounds` (KET in, KET out)
  followed by `bridge.setKet` (an ordinary undoable edit, autosaved). `getSmarts()` keeps aromatic atoms of an
  imported SMARTS where the molfile loses them (the molfile path then matches aliphatic rings too).
- Import and export (`chem/io.py`, PLAN Q59). What Ketcher 3.18 does on its own (checked in a real browser): the Open
  dialog takes a file or pasted text and offers "Open as New Project" / "Add to Canvas"; a single-structure file
  (mol, one-record SDF, one-line SMILES, KET) is fine, but **a multi-record SDF puts every record at its own
  coordinates, i.e. stacked on top of each other**, and **a multi-line `.smi` leaves the dialog open forever** (the
  structures do load). Save as offers KET, Molfile V2000/V3000, SDF V2000/V3000, RDF, SMARTS, SMILES, Extended SMILES,
  CML, InChI, InChIKey, SVG, PNG, CDXML, CDX; none of them carries properties or our compound numbers. So the app
  adds: `POST /api/tabs/import` (`{filename, text}` -> new tab; `.sdf/.sd/.mol` records are split on `$$$$` and read
  unsanitized, `.smi/.txt` lines are `SMILES [name]` with an optional header and `#` comments, `.csv/.tsv` need a
  `smiles`-like header column; one structure keeps its drawing, several go on a `layout.grid` with every structure
  centred at Ketcher's bond length; 3D records are depicted flat without hydrogens; at most 200 structures, the rest
  is reported) and `POST /api/chem/export` (`{format: sdf|csv|smi, molfile, ket, name}`; the client sends the
  editor's V3000 molfile and KET, `build_compounds` gives the numbers, queries are reported and skipped, SDF fields
  are `Number` plus the property names of `compute_properties`). KET / CDXML / RXN files are not parsed by the
  backend (it cannot read them): the client creates a tab and calls `bridge.setStructure` (= `setMolecule`, which
  detects the format), and closes the tab again when Ketcher throws. After a multi-structure import the client
  numbers the tab (`numberActive(expected)` waits until the KET shows that many molecules: a read right after a
  load can still be empty). Record names (`_Name`, `ID`) are not used as labels; numbers are 1..N in file order.
  The file name arrives as the browser gives it, but a Windows path must work too (`basename` splits on both
  separators: `Path("C:\\x\\a.sdf").stem` on Linux is the whole string). In `chem/io.py` the stacked-record check
  uses fragments' bounding boxes, not `split_molfile` (that one centres every fragment).
- Bioisosteres (PLAN Q60, Q61): `data/bioisosteres/*.yaml` is vendored as delivered; the only edits are the three
  user-decided ones of PLAN Q62 (`para_phenylene_to_bcp` / `_bco` take no halogen as an attachment atom,
  `pyrazole_to_isoxazole` gives both regioisomers; each has a dated comment in the YAML). Any other wrong rule goes
  back to its author, and a rule change needs its example products and a regression test updated with it. `chem/bioisostere_rules.py` loads it with Pydantic when the tool module is
  imported, so a broken table stops the server; `RuleTable.get` treats `status: excluded` as unknown. In a rule,
  atoms with a map number are attachments that stay; unmapped reactant atoms are what is replaced. A *position* is a set
  of replaced atoms (`react_atom_idx` on the product tells which reactant atoms survived; the rest were replaced), not a
  raw `RunReactants` match: a ring template matches one ring 6-12 times. A selection keeps only positions whose replaced
  atoms all lie inside it. Rules are written for neutral molecules: `Uncharger(canonicalOrder=True)` keeps the atom order,
  so the user's atom numbers still hold; the grid parent stays the drawing as drawn, products are neutral. Duplicates
  are found by canonical tautomer (`TautomerEnumerator.Canonicalize`), displayed as the product's own canonical SMILES.
  Keep a `ChemicalReaction` alive while you use its templates: `AllChem.ReactionFromSmarts(s).GetReactantTemplate(0)`
  on a temporary returns a dangling molecule (map numbers read as 0). Gitignore: `data/` is ignored for the runtime
  database, so the package data folder is re-included with `!backend/chemomni/data/`. `tests/test_rule_table.py` runs
  every rule's own examples (plain RDKit and through the engine); `tests/test_bioisostere.py` holds expectations
  written by hand (never produced by running a rule). The model passes rule ids only; do not add a SMILES field to
  `apply_bioisosteres`.
- SA score lives in `rdkit.Contrib.SA_Score.sascorer` (add `RDConfig.RDContribDir` to the path
  once at import time).
- Molfile V3000 out: `Chem.MolToV3KMolBlock(mol)`.
