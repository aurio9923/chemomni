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

        # M1: chat with the fake provider computes properties of the active tab (ethanol)
        page.get_by_role("button", name="New chat").click()
        expect(page.get_by_test_id("user-message")).to_have_count(0)
        box = page.get_by_role("textbox", name="Message")
        box.fill("Compute the properties")
        box.press("Enter")
        expect(page.get_by_test_id("user-message")).to_have_count(1)
        table = page.get_by_test_id("artifact-table")
        expect(table).to_contain_text("C2H6O", timeout=10_000)
        expect(page.get_by_test_id("assistant-message")).to_contain_text("MW 46.07 g/mol")
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

        page.screenshot(path=sys.argv[2] if len(sys.argv) > 2 else "smoke.png")
        assert not errors, errors
        browser.close()
    print(json.dumps(metrics))


if __name__ == "__main__":
    main()
