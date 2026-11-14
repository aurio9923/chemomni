"""Smoke test: editor loads, tabs persist, property bar updates, chat (fake provider) works.

Run against a running server (backend serving frontend/dist):
    uv run --project backend --with playwright python e2e/smoke_m0.py [base_url]
Prints load metrics as JSON on success.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from playwright.sync_api import Page, expect, sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
ASPIRIN = "CC(=O)Oc1ccccc1C(=O)O"
ETHANOL = "CCO"
# Three records, as exported by RDKit. Ketcher stacks the records of such a file on top of each other.
SERIES_SDF = """ethanol
     RDKit          2D

  3  2  0  0  0  0  0  0  0  0999 V2000
   -1.2990   -0.2500    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0
    0.0000    0.5000    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0
    1.2990   -0.2500    0.0000 O   0  0  0  0  0  0  0  0  0  0  0  0
  1  2  1  0
  2  3  1  0
M  END
> <ID>
ETHANOL

$$$$
acetic acid
     RDKit          2D

  4  3  0  0  0  0  0  0  0  0999 V2000
   -1.2990   -0.7500    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0
    0.0000    0.0000    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0
    0.0000    1.5000    0.0000 O   0  0  0  0  0  0  0  0  0  0  0  0
    1.2990   -0.7500    0.0000 O   0  0  0  0  0  0  0  0  0  0  0  0
  1  2  1  0
  2  3  2  0
  2  4  1  0
M  END
> <ID>
ACETIC ACID

$$$$
aniline
     RDKit          2D

  7  7  0  0  0  0  0  0  0  0999 V2000
    2.5714    0.0000    0.0000 N   0  0  0  0  0  0  0  0  0  0  0  0
    1.0714    0.0000    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0
    0.3214    1.2990    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0
   -1.1786    1.2990    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0
   -1.9286    0.0000    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0
   -1.1786   -1.2990    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0
    0.3214   -1.2990    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0
  1  2  1  0
  2  3  2  0
  3  4  1  0
  4  5  2  0
  5  6  1  0
  6  7  2  0
  7  2  1  0
M  END
> <ID>
ANILINE

$$$$
"""


def wait_ready(page: Page) -> float:
    t0 = time.perf_counter()
    page.wait_for_selector("body[data-ketcher-ready='true']", timeout=30_000)
    return time.perf_counter() - t0


def set_structure(page: Page, smiles: str) -> None:
    """Simulate a user edit through Ketcher's own API (drawing with the mouse is slow and brittle)."""
    page.evaluate(
        """async (smi) => {
            const k = window.chemomniKetcher
            await k.setMolecule(smi)
        }""",
        smiles,
    )


class TransferMeter:
    """Counts encoded response bytes for the page and its workers (the WASM is fetched by a worker)."""

    def __init__(self) -> None:
        self.total = 0

    def __call__(self, request) -> None:  # type: ignore[no-untyped-def]
        sizes = request.sizes()
        self.total += sizes["responseBodySize"] + sizes["responseHeadersSize"]


def main() -> None:
    metrics: dict[str, float | int] = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        meter = TransferMeter()
        ctx.on("requestfinished", meter)
        page = ctx.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        console: list[str] = []
        page.on("console", lambda m: console.append(f"{m.type}: {m.text}"))

        t0 = time.perf_counter()
        page.goto(BASE)
        wait_ready(page)
        metrics["cold_ready_s"] = round(time.perf_counter() - t0, 2)
        page.wait_for_load_state("networkidle")
        metrics["cold_transfer_kb"] = meter.total // 1024

        tabs = page.get_by_test_id("tab")
        n = tabs.count()  # 1 on a fresh database
        assert n >= 1
        tabs.nth(n - 1).click()

        # draw aspirin in tab 1 and check the live property bar
        set_structure(page, ASPIRIN)
        expect(page.get_by_test_id("property-bar")).to_contain_text("C9H8O4", timeout=5_000)

        # new tab with ethanol
        page.get_by_role("button", name="New tab", exact=True).click()
        expect(tabs).to_have_count(n + 1)
        page.wait_for_timeout(300)
        set_structure(page, ETHANOL)
        expect(page.get_by_test_id("property-bar")).to_contain_text("C2H6O", timeout=5_000)

        # switch back: measure switch time and confirm content
        page.wait_for_timeout(800)  # let autosave (500 ms debounce) run
        t1 = time.perf_counter()
        tabs.nth(n - 1).click()
        page.wait_for_function(
            "async () => (await window.chemomniBridge.getSmiles()).includes('c1ccccc1')", timeout=5_000
        )
        metrics["tab_switch_ms"] = round((time.perf_counter() - t1) * 1000)
        expect(page.get_by_test_id("property-bar")).to_contain_text("C9H8O4", timeout=5_000)

        # warm reload: everything restored from SQLite, assets from cache
        page.wait_for_timeout(800)
        before_reload = meter.total
        t2 = time.perf_counter()
        page.reload()
        wait_ready(page)
        metrics["warm_ready_s"] = round(time.perf_counter() - t2, 2)
        page.wait_for_load_state("networkidle")
        metrics["warm_transfer_kb"] = (meter.total - before_reload) // 1024
        expect(tabs).to_have_count(n + 1)
        page.wait_for_function("async () => (await window.chemomniBridge.getSmiles()).length > 0", timeout=5_000)
        restored = page.evaluate("async () => window.chemomniBridge.getSmiles()")
        assert restored in ("CCO", "OCC"), f"active tab after reload should be the last tab, got {restored}"

        # close + reopen
        tabs.nth(n).get_by_role("button", name="Close").click()
        expect(tabs).to_have_count(n)
        page.get_by_role("button", name="Reopen closed tab").click()
        expect(tabs).to_have_count(n + 1)
        expect(page.get_by_test_id("property-bar")).to_contain_text("C2H6O", timeout=5_000)

        # M1: chat with the fake provider computes properties of the active tab (ethanol).
        # Slow down history loading so a late response would race with the message being sent.
        def slow_history(route):  # type: ignore[no-untyped-def]
            time.sleep(0.8)
            route.continue_()

        page.route("**/api/conversations/*/messages", slow_history)
        page.get_by_role("button", name="New chat").click()
        expect(page.get_by_test_id("user-message")).to_have_count(0)
        box = page.get_by_role("textbox", name="Message")
        box.fill("Compute the properties")
        box.press("Enter")
        expect(page.get_by_test_id("user-message")).to_have_count(1)
        page.unroute("**/api/conversations/*/messages")
        table = page.get_by_test_id("artifact-table")
        try:
            expect(table).to_contain_text("C2H6O", timeout=10_000)
        except AssertionError:
            print("assistant said:", page.get_by_test_id("assistant-message").last.inner_text())
            print("console:", *console[-20:], sep="\n  ")
            raise
        expect(page.get_by_test_id("assistant-message")).to_contain_text("MW 46.07 g/mol")
        expect(page.get_by_role("combobox", name="Model")).to_be_enabled()
        expect(page.get_by_test_id("answered-by")).to_have_text("fake")
        # open the structure from the chat card in a new tab
        tab_count = tabs.count()
        table.get_by_role("button", name="Open C2H6O in a new tab").click()
        expect(tabs).to_have_count(tab_count + 1)
        page.wait_for_function(
            "async () => ['CCO','OCC'].includes(await window.chemomniBridge.getSmiles())",
            polling=200,
            timeout=5_000,
        )
        # history survives a reload
        page.wait_for_timeout(700)
        page.reload()
        wait_ready(page)
        expect(page.get_by_test_id("artifact-table")).to_contain_text("C2H6O", timeout=5_000)
        expect(page.get_by_test_id("property-bar")).to_contain_text("C2H6O", timeout=5_000)

        # ---- M2: edit into a new tab, replace + undo, selection numbering, highlight ----
        def editor_smiles() -> str:
            return page.evaluate("async () => window.chemomniBridge.getSmiles()")

        def send(text: str) -> None:
            n_before = page.get_by_test_id("assistant-message").count()
            box.fill(text)
            box.press("Enter")
            expect(page.get_by_test_id("assistant-message")).to_have_count(n_before + 1)
            expect(page.get_by_test_id("answered-by").nth(n_before)).to_be_visible(timeout=15_000)

        box = page.get_by_role("textbox", name="Message")
        set_structure(page, ASPIRIN)
        expect(page.get_by_test_id("property-bar")).to_contain_text("C9H8O4", timeout=5_000)
        page.wait_for_timeout(800)  # autosave
        aspirin_tab = page.locator("[data-testid=tab][aria-selected=true]").inner_text()
        original = editor_smiles()

        n_tabs = tabs.count()
        send("add a methyl")
        expect(tabs).to_have_count(n_tabs + 1)
        expect(page.locator("[data-testid=tab][aria-selected=true]")).to_contain_text("Homologue")
        expect(page.get_by_test_id("artifact-diff").last).to_contain_text("C10H10O4")
        page.wait_for_function("async () => (await window.chemomniBridge.getSmiles()).length > 0", polling=200)
        assert editor_smiles() != original

        # back to the aspirin tab, replace in place, then undo
        tabs.filter(has_text=aspirin_tab).first.click()
        page.wait_for_function(
            "async (s) => (await window.chemomniBridge.getSmiles()) === s", arg=original, polling=200, timeout=5_000
        )
        send("replace it with the homologue")
        expect(tabs).to_have_count(n_tabs + 1)  # no extra tab
        toast = page.get_by_test_id("toast").filter(has_text="Replaced")
        expect(toast).to_be_visible()
        page.wait_for_function(
            "async (s) => (await window.chemomniBridge.getSmiles()) !== s", arg=original, polling=200, timeout=5_000
        )
        toast.get_by_role("button", name="Undo").click()
        page.wait_for_function(
            "async (s) => (await window.chemomniBridge.getSmiles()) === s", arg=original, polling=200, timeout=5_000
        )

        # selection is sent as molfile atom numbers that point at the same element
        page.evaluate(
            """() => {
                const k = window.chemomniKetcher
                let oxygen = null
                k.editor.struct().atoms.forEach((a, id) => { if (oxygen === null && a.label === 'O') oxygen = id })
                k.editor.selection({ atoms: [oxygen] })
            }"""
        )
        with page.expect_request("**/api/chat") as req:
            send("highlight the first atom")
        snap = json.loads(req.value.post_data)["snapshot"]["active_tab"]
        assert len(snap["selection_atoms"]) == 1, snap["selection_atoms"]
        n = snap["selection_atoms"][0]
        lines = snap["molfile"].splitlines()
        start = next(i for i, ln in enumerate(lines) if "BEGIN ATOM" in ln)
        end = next(i for i, ln in enumerate(lines) if "END ATOM" in ln)
        atom_lines = lines[start + 1 : end]
        element = next(ln.split()[3] for ln in atom_lines if ln.split()[2] == str(n))
        assert element == "O", (n, element)
        # the fake assistant highlights molfile atom 1: Ketcher must select the atom with that element
        first_el = atom_lines[0].split()[3]
        selected = page.evaluate(
            """() => {
                const k = window.chemomniKetcher
                const ids = k.editor.selection()?.atoms ?? []
                return ids.map((id) => k.editor.struct().atoms.get(id).label)
            }"""
        )
        assert selected == [first_el], (selected, first_el)

        # ---- M3: analog series on a grid tab, sortable table, split into one tab per molecule ----
        def editor_components() -> int:
            return len([s for s in editor_smiles().split(".") if s])

        n_tabs = tabs.count()
        send("suggest 5 analogs")  # fake: 5 valid + duplicate + parent + invalid
        expect(tabs).to_have_count(n_tabs + 1)
        active_tab = page.locator("[data-testid=tab][aria-selected=true]")
        expect(active_tab).to_contain_text("Analogs")
        grid = page.get_by_test_id("artifact-grid").last
        expect(grid.get_by_test_id("grid-card")).to_have_count(5)
        expect(grid).to_contain_text("3 rejected")
        page.wait_for_function(
            "async () => (await window.chemomniBridge.getSmiles()).split('.').length === 5", polling=200, timeout=5_000
        )
        # sort by MW, descending: the chloro analog (#5) is the heaviest
        mw = grid.get_by_role("button", name="MW", exact=True)
        mw.click()
        mw.click()
        expect(grid.locator("tbody tr").first.locator("td").first).to_have_text("5")
        if len(sys.argv) > 3:
            page.screenshot(path=sys.argv[3])

        active_tab.hover()
        active_tab.get_by_role("button", name="Split Analogs into one tab per molecule").click()
        expect(tabs).to_have_count(n_tabs + 1 + 5)
        expect(active_tab).to_contain_text("Analogs #1")
        page.wait_for_function(
            "async () => { const s = await window.chemomniBridge.getSmiles(); return s && !s.includes('.') }",
            polling=200,
            timeout=5_000,
        )
        assert editor_components() == 1

        # ---- select a phenyl in the editor, ask for a pyridine: every position at once ----
        def draw_and_select_phenyl(smiles: str, formula: str) -> None:
            page.get_by_role("button", name="New tab", exact=True).click()
            page.wait_for_timeout(300)
            set_structure(page, smiles)
            expect(page.get_by_test_id("property-bar")).to_contain_text(formula, timeout=5_000)
            page.wait_for_timeout(800)  # autosave
            ring_size = page.evaluate(
                """() => {
                    const k = window.chemomniKetcher
                    const adj = new Map()
                    k.editor.struct().bonds.forEach((b) => {
                        for (const [x, y] of [[b.begin, b.end], [b.end, b.begin]]) {
                            if (!adj.has(x)) adj.set(x, [])
                            adj.get(x).push(y)
                        }
                    })
                    let ring = null
                    const walk = (start, cur, path) => {
                        if (ring) return
                        if (path.length === 6) {
                            if (adj.get(cur).includes(start)) ring = [...path]
                            return
                        }
                        for (const n of adj.get(cur)) if (!path.includes(n)) walk(start, n, [...path, n])
                    }
                    adj.forEach((_, id) => walk(id, id, [id]))
                    k.editor.selection({ atoms: ring })
                    return ring ? ring.length : 0
                }"""
            )
            assert ring_size == 6

        draw_and_select_phenyl("CC(=O)Nc1ccccc1", "C8H9NO")
        n_tabs = tabs.count()
        send("make the selected phenyl a pyridine")
        expect(tabs).to_have_count(n_tabs + 1)
        expect(active_tab).to_contain_text("Pyridine scan")
        scan_grid = page.get_by_test_id("artifact-grid").last
        expect(scan_grid.get_by_test_id("grid-card")).to_have_count(3)
        for position in ("ortho", "meta", "para"):
            expect(scan_grid).to_contain_text(f"N at {position}")
        page.wait_for_function(
            "async () => (await window.chemomniBridge.getSmiles()).split('.').length === 3", polling=200, timeout=5_000
        )
        if len(sys.argv) > 4:
            page.screenshot(path=sys.argv[4])

        # one fluorine AND one methyl on the five CH of a phenyl: 20 placements, 10 distinct compounds
        draw_and_select_phenyl("COC(=O)c1ccccc1", "C8H8O2")
        n_tabs = tabs.count()
        send("monofluorinate and monomethylate the selected phenyl")
        expect(tabs).to_have_count(n_tabs + 1)
        expect(active_tab).to_contain_text("F + Me scan")
        combo_grid = page.get_by_test_id("artifact-grid").last
        expect(combo_grid.get_by_test_id("grid-card")).to_have_count(10)
        page.wait_for_function(
            "async () => (await window.chemomniBridge.getSmiles()).split('.').length === 10", polling=200, timeout=5_000
        )

        # ---- M4: number the structures, then refer to them ----
        page.get_by_role("button", name="New tab", exact=True).click()
        page.wait_for_timeout(300)
        set_structure(page, "CCO.c1ccccc1N.CC(=O)O")
        expect(page.get_by_test_id("property-bar")).to_contain_text("C2H6O", timeout=5_000)
        page.wait_for_timeout(800)  # autosave

        def ket_labels() -> list[str]:
            return page.evaluate(
                """async () => {
                    const ket = JSON.parse(await window.chemomniKetcher.getKet())
                    return ket.root.nodes.filter((n) => n.type === 'text').map((n) => n.paragraphs[0].parts[0].text)
                }"""
            )

        assert ket_labels() == []
        page.get_by_role("button", name="Number compounds").click()
        # sample the editor until the numbers have been there for a moment (or give up after 8 s)
        series = page.evaluate(
            """async () => {
                const out = []
                const t0 = performance.now()
                while (performance.now() - t0 < 8000) {
                    const ket = JSON.parse(await window.chemomniKetcher.getKet())
                    out.push(`${ket.root.nodes.filter((n) => n.type === 'text').length}/${ket.root.nodes.filter((n) => n.$ref).length}`)
                    if (out.slice(-5).filter((x) => x === '3/3').length === 5) break
                    await new Promise((r) => setTimeout(r, 150))
                }
                return out
            }"""
        )
        labels = ket_labels()
        assert sorted(labels) == ["1", "2", "3"], (labels, series, editor_smiles(), errors)
        expect(page.get_by_text("Numbered 3 structures: 1, 2, 3.")).to_be_visible()
        assert editor_components() == 3  # the numbers are text objects, not molecules
        page.get_by_role("button", name="Number compounds").click()  # nothing left to number
        expect(page.get_by_text("Every structure already has a number")).to_be_visible()

        # reading order: acetic acid (top row) is 1, ethanol 2, aniline 3. Compound 2 as the query finds
        # ethanol's C-C-O inside acetic acid only.
        n_before = page.get_by_test_id("artifact-structures").count()
        send("use compound 2 as the query for a substructure search of the other compounds")
        expect(page.get_by_text("1 of 2 structure(s) contain the query.")).to_be_visible()
        hits = page.get_by_test_id("artifact-structures").last
        expect(hits).to_contain_text("Matches for compound 2")
        assert page.get_by_test_id("artifact-structures").count() == n_before + 1
        if len(sys.argv) > 5:
            page.screenshot(path=sys.argv[5])

        # ---- M4: import and export. Ketcher stacks the records of an SDF; the server lays them on a grid ----
        def mol_boxes() -> list[list[float]]:
            return page.evaluate(
                """async () => {
                    const ket = JSON.parse(await window.chemomniKetcher.getKet())
                    return ket.root.nodes.filter((n) => n.$ref).map((n) => {
                        const xs = ket[n.$ref].atoms.map((a) => a.location[0])
                        const ys = ket[n.$ref].atoms.map((a) => a.location[1])
                        return [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)]
                    })
                }"""
            )

        def wait_for_numbers(count: int) -> None:
            """Sample the editor until the numbers have been there for a moment (a single read can be stale)."""
            page.evaluate(
                """async (count) => {
                    const t0 = performance.now()
                    let stable = 0
                    while (performance.now() - t0 < 10000 && stable < 5) {
                        const ket = JSON.parse(await window.chemomniKetcher.getKet())
                        const ok = ket.root.nodes.filter((n) => n.type === 'text').length === count
                            && ket.root.nodes.filter((n) => n.$ref).length === count
                        stable = ok ? stable + 1 : 0
                        await new Promise((r) => setTimeout(r, 150))
                    }
                }""",
                count,
            )

        def upload(name: str, mime: str, data: bytes) -> None:
            page.get_by_test_id("import-input").set_input_files(files=[{"name": name, "mimeType": mime, "buffer": data}])

        tabs_before = page.get_by_test_id("tab").count()
        upload("series.sdf", "chemical/x-mdl-sdfile", SERIES_SDF.encode())
        expect(page.get_by_text("Imported 3 structures from series.sdf, numbered 1\u20133.")).to_be_visible(timeout=15_000)
        assert page.get_by_test_id("tab").count() == tabs_before + 1
        expect(page.locator("[data-testid=tab][aria-selected=true]")).to_contain_text("series")
        wait_for_numbers(3)
        assert sorted(ket_labels()) == ["1", "2", "3"], (ket_labels(), errors)
        boxes = mol_boxes()
        assert len(boxes) == 3
        for i, a in enumerate(boxes):
            for b in boxes[i + 1 :]:
                assert not (a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]), ("records overlap", boxes)

        page.get_by_role("button", name="Export", exact=True).click()
        with page.expect_download() as download:
            page.get_by_test_id("export-csv").click()
        assert download.value.suggested_filename == "series.csv", download.value.suggested_filename
        rows = Path(download.value.path()).read_text().strip().split("\n")
        assert rows[0].startswith("number,smiles,formula,mw,clogp,tpsa"), rows[0]
        assert sorted((r.split(",")[0], r.split(",")[1]) for r in rows[1:]) == [
            ("1", "CCO"),
            ("2", "CC(=O)O"),
            ("3", "Nc1ccccc1"),
        ], rows  # the records come in file order: 2 columns, the first record top left
        expect(page.get_by_text("Exported 3 structures to series.csv.")).to_be_visible()

        page.get_by_role("button", name="Export", exact=True).click()
        with page.expect_download() as download:
            page.get_by_test_id("export-sdf").click()
        sdf = Path(download.value.path()).read_text()
        assert sdf.count("$$$$") == 3 and "> <Number>" in sdf and "> <mw>" in sdf, sdf[:300]

        # a KET drawing goes through Ketcher itself, into a tab of its own
        ket_text = page.evaluate("async () => await window.chemomniKetcher.getKet()")
        tabs_before = page.get_by_test_id("tab").count()
        upload("drawing.ket", "application/json", ket_text.encode())
        expect(page.get_by_text("Opened drawing.ket in a new tab.")).to_be_visible(timeout=15_000)
        assert page.get_by_test_id("tab").count() == tabs_before + 1
        wait_for_numbers(3)
        assert len(mol_boxes()) == 3 and sorted(ket_labels()) == ["1", "2", "3"]

        # what cannot be read is reported and leaves no tab behind
        tabs_before = page.get_by_test_id("tab").count()
        upload("notes.txt", "text/plain", b"hello there\nnot chemistry\n")
        expect(page.get_by_text("Could not import notes.txt: no structure could be read from notes.txt")).to_be_visible()
        upload("photo.png", "image/png", b"\x89PNG")
        expect(page.get_by_text("photo.png: this file type is not supported")).to_be_visible()
        assert page.get_by_test_id("tab").count() == tabs_before

        page.screenshot(path=sys.argv[2] if len(sys.argv) > 2 else "smoke.png")
        assert not errors, errors
        browser.close()
    print(json.dumps(metrics))


if __name__ == "__main__":
    main()
