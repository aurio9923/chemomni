"""Build the LLM message list (PLAN §8): system prompt, workspace snapshot, trimmed history."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field

from chemomni.chem.edit import selection_info
from chemomni.llm.prompts import SYSTEM_PROMPT
from chemomni.models import Message as MessageRow

MAX_MOLFILE_ATOMS = 200
MAX_MOLECULES_FOR_MOLFILE = 20


class ActiveTabSnapshot(BaseModel):
    id: str
    name: str = ""
    revision: int = 0
    smiles: list[str] = Field(default_factory=list)
    molfile: str | None = None
    selection_atoms: list[int] = Field(default_factory=list, description="1-based molfile atom numbers")


class TabSummary(BaseModel):
    id: str
    name: str
    smiles: list[str] = Field(default_factory=list)


class Snapshot(BaseModel):
    active_tab: ActiveTabSnapshot | None = None
    tabs: list[TabSummary] = Field(default_factory=list)


def _atom_count(molfile: str) -> int:
    for line in molfile.splitlines():
        if "COUNTS" in line:  # V3000: "M  V30 COUNTS na nb ..."
            parts = line.split()
            try:
                return int(parts[3])
            except (IndexError, ValueError):
                return 0
    return 0


def snapshot_block(snap: Snapshot) -> str:
    lines = ["<workspace>", "Tabs:"]
    for t in snap.tabs[:50]:
        mols = ", ".join(t.smiles[:5]) + (" …" if len(t.smiles) > 5 else "")
        lines.append(f"- {t.name} (id {t.id}): {mols or 'empty'}")
    lines.append("</workspace>")
    a = snap.active_tab
    if a is None:
        lines.append("<active_tab>none</active_tab>")
        return "\n".join(lines)
    lines.append(f'<active_tab name="{a.name}" id="{a.id}">')
    if a.smiles:
        lines.append("SMILES:")
        lines.extend(f"- {s}" for s in a.smiles)
        include_molfile = (
            a.molfile and len(a.smiles) <= MAX_MOLECULES_FOR_MOLFILE and _atom_count(a.molfile) <= MAX_MOLFILE_ATOMS
        )
        if include_molfile:
            lines.append("Molfile (V3000, atom numbers are 1-based):")
            lines.append(a.molfile.strip())
    else:
        lines.append("The active tab is empty.")
    lines.append("</active_tab>")
    sel = selection_info(a.molfile, a.selection_atoms)
    if sel:
        pairs = ", ".join(f"{n}:{el}" for n, el in zip(sel["atoms"], sel["elements"], strict=True))  # type: ignore[arg-type]
        lines.append(f"<selection>The user has selected atoms (molfile number:element) {pairs}.")
        if sel["fragment_smiles"]:
            lines.append(f"Selected fragment: {sel['fragment_smiles']}")
        lines.append('"this", "here" or "the selected ..." refer to this selection.</selection>')
    return "\n".join(lines)


def _blocks_to_openai(row: MessageRow) -> list[dict[str, Any]]:
    """Convert one stored message (a sequence of blocks) into OpenAI chat messages."""
    if row.role == "user":
        text = "".join(b.get("text", "") for b in row.content if b.get("type") == "text")
        return [{"role": "user", "content": text}]
    out: list[dict[str, Any]] = []
    text, calls = "", []

    def flush() -> None:
        nonlocal text, calls
        if text or calls:
            msg: dict[str, Any] = {"role": "assistant", "content": text or None}
            if calls:
                msg["tool_calls"] = calls
            out.append(msg)
        text, calls = "", []

    for b in row.content:
        kind = b.get("type")
        if kind == "text":
            if calls:  # text after tool calls starts a new assistant message
                flush()
            text += b["text"]
        elif kind == "tool_use":
            calls.append(
                {
                    "id": b["id"],
                    "type": "function",
                    "function": {"name": b["name"], "arguments": b["arguments"]},
                }
            )
        elif kind == "tool_result":
            flush()
            out.append({"role": "tool", "tool_call_id": b["tool_call_id"], "content": b["content"]})
    flush()
    return out


def _estimate_tokens(messages: list[dict[str, Any]]) -> int:
    return len(json.dumps(messages, ensure_ascii=False)) // 4


def build_messages(history: list[MessageRow], snap: Snapshot, budget: int) -> list[dict[str, Any]]:
    """System prompt + history (oldest turns dropped to fit) + snapshot just before the new user message.

    ``history`` ends with the new user message.
    """
    system = [{"role": "system", "content": SYSTEM_PROMPT}]
    context = [{"role": "system", "content": snapshot_block(snap)}]
    *previous, current = history
    turns: list[list[dict[str, Any]]] = []
    for row in previous:
        converted = _blocks_to_openai(row)
        if row.role == "user" or not turns:
            turns.append(converted)
        else:
            turns[-1].extend(converted)
    fixed = system + context + _blocks_to_openai(current)
    remaining = budget - _estimate_tokens(fixed)
    kept: list[list[dict[str, Any]]] = []
    for turn in reversed(turns):
        cost = _estimate_tokens(turn)
        if cost > remaining:
            break
        kept.insert(0, turn)
        remaining -= cost
    return system + [m for turn in kept for m in turn] + context + _blocks_to_openai(current)
