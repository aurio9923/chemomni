"""Smoke test: editor loads, tabs persist, property bar updates, chat (fake provider) works.

Run against a running server (backend serving frontend/dist):
    uv run --project backend --with playwright python e2e/smoke_m0.py [base_url]
Prints load metrics as JSON on success.
"""

from __future__ import annotations

import json
import sys
import time

from playwright.sync_api import Page, expect, sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
ASPIRIN = "CC(=O)Oc1ccccc1C(=O)O"
ETHANOL = "CCO"


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

        page.screenshot(path=sys.argv[2] if len(sys.argv) > 2 else "smoke.png")
        assert not errors, errors
        browser.close()
    print(json.dumps(metrics))


if __name__ == "__main__":
    main()
