"""Deterministic provider for tests and E2E (no network).

Two modes:
- scripted: a list of turns, each a list of events, replayed in order (``FakeProvider(script)``)
- default: a tiny rule-based assistant that calls ``compute_properties`` on the active tab
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from typing import Any

from chemomni.llm.provider import Done, LLMEvent, Message, TextDelta, ToolCall, ToolSpecJSON

ScriptTurn = list[dict[str, Any]]


class FakeProvider:
    model = "fake"

    def __init__(self, script: list[ScriptTurn] | None = None) -> None:
        self._script = list(script) if script is not None else None
        self.calls: list[dict[str, Any]] = []  # what the loop sent, for assertions

    async def stream(self, messages: list[Message], tools: list[ToolSpecJSON]) -> AsyncIterator[LLMEvent]:
        self.calls.append({"messages": messages, "tools": tools})
        events = self._next_scripted() if self._script is not None else self._rule_based(messages)
        for ev in events:
            if "text" in ev:
                # stream in small pieces like a real model
                for piece in re.findall(r".{1,12}", ev["text"], flags=re.S):
                    yield TextDelta(piece)
            elif "tool" in ev:
                yield ToolCall(ev.get("id", f"call_{len(self.calls)}"), ev["tool"], json.dumps(ev["args"]))
        yield Done(finish_reason="stop", usage={"prompt_tokens": 0, "completion_tokens": 0})

    def _next_scripted(self) -> ScriptTurn:
        assert self._script is not None
        if not self._script:
            return [{"text": "(fake script exhausted)"}]
        return self._script.pop(0)

    @staticmethod
    def _rule_based(messages: list[Message]) -> ScriptTurn:
        """Keywords in the user's message pick a tool: 'pyridine' / 'fluor' scan the selection, 'analog'
        proposes a series, 'replace' / 'methyl' edit the first molecule, 'highlight' selects atom 1,
        'bioisostere' looks for rules and applies the first three that fit (to the selection or the only
        molecule), 'tautomer' lists the tautomers (or the canonical one with 'canonical'), anything else
        computes properties."""
        last = messages[-1]
        if last["role"] == "tool":
            result = json.loads(last["content"])
            if not result.get("ok"):
                return [{"text": "That did not work: " + "; ".join(result.get("errors", []))}]
            if "tautomer_count" in result:  # tautomers
                shown = len(result.get("accepted", []))
                return [
                    {"text": f"{result['tautomer_count']} tautomer(s); canonical {result['canonical']}; {shown} shown."}
                ]
            if "distinct_products" in result:  # apply_bioisosteres
                return [
                    {
                        "text": f"Applied {len(result['rules'])} rule(s): {len(result['accepted'])} product(s) "
                        f"in tab {result['tab_name']}."
                    }
                ]
            if "rules" in result and "molecule" in result:  # find_bioisosteres: apply the first few that fit
                ids = [r["id"] for r in result["rules"]][:3]
                if not ids:
                    return [{"text": "No bioisostere rule fits."}]
                asked = next((m for m in reversed(messages) if m["role"] == "user"), None)
                ref = re.search(r"(?:compound|化合物)\s*(\d+[a-z]?)", str(asked["content"]).lower()) if asked else None
                args = {"rule_ids": ids, "title": "Bioisosteres", **({"ref": ref.group(1)} if ref else {})}
                return [{"tool": "apply_bioisosteres", "args": args}]
            if "searched" in result:
                return [{"text": f"{result['matches']} of {result['searched']} structure(s) contain the query."}]
            if "selected" in result and "accepted" in result:
                return [{"text": f"Scanned {result['selected']}: {len(result['accepted'])} variant(s)."}]
            if "accepted" in result:
                return [
                    {
                        "text": f"Proposed {len(result['accepted'])} analog(s) in tab {result['tab_name']}; "
                        f"{len(result['rejected'])} rejected."
                    }
                ]
            if "applied" in result:
                return [{"text": f"Done ({result['applied']}): {result['formula']} in tab {result['tab_name']}."}]
            if "atoms" in result and "rows" not in result:
                return [{"text": f"Highlighted atom(s) {result['atoms']}."}]
            rows = [r for r in result.get("rows", []) if r.get("ok")]
            if not rows:
                return [{"text": "I could not compute properties for that structure."}]
            first = rows[0]
            return [
                {
                    "text": (
                        f"Computed properties for {len(rows)} molecule(s). "
                        f"The first one, **{first['formula']}**, has MW {first['mw']} g/mol "
                        f"and cLogP {first['clogp']} (Crippen)."
                    )
                }
            ]
        context = "\n".join(m["content"] for m in messages if m["role"] == "system")
        match = re.search(r"^SMILES:\n((?:- .+\n?)+)", context, flags=re.M)
        if not match:
            return [{"text": "Draw a structure on the active tab first, then ask again."}]
        smiles = [line[2:].strip() for line in match.group(1).strip().splitlines()]
        text = str(last.get("content", "")).lower()
        ref = re.search(r"(?:compound|化合物)\s*(\d+[a-z]?)", text)
        if ref and ("substructure" in text or "部分構造" in text):  # the numbered drawing is the query
            return [{"tool": "substructure_search", "args": {"query_ref": ref.group(1)}}]
        if "tautomer" in text or "互変異性" in text or "トートマー" in text:
            args = {"ref": ref.group(1)} if ref else {}
            if "canonical" in text or "正準" in text or "カノニカル" in text:
                args["show"] = "canonical"
            return [{"tool": "tautomers", "args": args}]
        if "bioisoster" in text or "バイオアイソスター" in text:
            args = {"ref": ref.group(1)} if ref else {}
            if "acid" in text or "カルボン酸" in text:
                args["family"] = "carboxylic_acid_isosteres"
            return [{"tool": "find_bioisosteres", "args": args}]
        if "methyl" in text and "fluor" in text:  # both, on different atoms
            changes = [{"operation": "substitute", "group": "F"}, {"operation": "substitute", "group": "C"}]
            return [{"tool": "scan_positions", "args": {"changes": changes, "title": "F + Me scan"}}]
        if "pyridine" in text:
            args = {"changes": [{"operation": "replace_atom", "element": "N"}], "title": "Pyridine scan"}
            return [{"tool": "scan_positions", "args": args}]
        if "fluor" in text:
            args = {"changes": [{"operation": "substitute", "group": "F"}], "title": "Fluoro scan"}
            return [{"tool": "scan_positions", "args": args}]
        if "analog" in text:
            parent = smiles[0]
            # five valid analogs plus one duplicate, the parent itself and an invalid SMILES
            prefixes = ("C", "F", "O", "N", "Cl")
            candidates = [{"smiles": f"{p}{parent}", "rationale": f"{p} at the end"} for p in prefixes]
            candidates += [
                {"smiles": f"C{parent}", "rationale": "duplicate"},
                {"smiles": parent, "rationale": "parent"},
                {"smiles": "C1CC", "rationale": "broken ring"},
            ]
            args = {"parent_smiles": parent, "candidates": candidates, "title": "Analogs"}
            return [{"tool": "propose_structures", "args": args}]
        if "replace" in text or "methyl" in text:
            target = "replace_active" if "replace" in text else "new_tab"
            args = {
                "parent_smiles": smiles[0],
                "new_smiles": "C" + smiles[0],
                "description": "Homologue",
                "target": target,
            }
            return [{"tool": "edit_structure", "args": args}]
        if "highlight" in text:
            return [{"tool": "highlight_atoms", "args": {"atoms": [1]}}]
        return [{"tool": "compute_properties", "args": {"smiles": smiles}}]
