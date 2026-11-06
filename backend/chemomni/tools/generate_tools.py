"""M3 tools: analog proposals on a grid tab, opening structures, names to structures (PLAN §6)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from chemomni.api.tabs import create_tab
from chemomni.chem.depict import depict, depict_molblock, molblock
from chemomni.chem.generate import Evaluation, evaluate
from chemomni.chem.layout import layout_smiles
from chemomni.chem.names import NameResolver
from chemomni.chem.props import canonical_smiles, parse_smiles
from chemomni.models import TabRead
from chemomni.tools.registry import ToolContext, ToolResult, tool

GRID_CARD = (220, 160)


def _tab_payload(tab: Any) -> dict[str, Any]:
    return TabRead.model_validate(tab).model_dump(mode="json")


# ---------------------------------------------------------------- propose_structures


class Candidate(BaseModel):
    smiles: str = Field(..., description="The complete proposed molecule as SMILES.")
    rationale: str = Field(
        ..., max_length=160, description="Why this one, in a few words, e.g. 'blocks para-oxidation'."
    )


class ProposeIn(BaseModel):
    parent_smiles: str | None = Field(
        None,
        description="The molecule the series starts from, as it appears on the active tab. "
        "Omit only for ideas without a starting structure.",
    )
    candidates: list[Candidate] = Field(..., min_length=1, max_length=20)
    title: str = Field(..., max_length=60, description="Short tab name, e.g. 'Aspirin analogs'.")


def _summary_propose(r: ToolResult) -> str:
    if not r.get("ok"):
        return "no valid structures"
    rejected = len(r.get("rejected", []))
    return f"{len(r['accepted'])} accepted" + (f", {rejected} rejected" if rejected else "")


def _compact(item: dict[str, Any]) -> dict[str, Any]:
    """What the LLM needs to talk about one grid item; the user sees the full table."""
    keys = ("smiles", "similarity", "mw", "clogp", "tpsa", "qed", "sa_score", "lipinski_violations")
    return {
        "n": item["index"],
        **{k: item[k] for k in keys},
        "alerts": len(item["pains_alerts"]) + len(item["brenk_alerts"]),
    }


@tool(
    name="propose_structures",
    description=(
        "Present new structures you designed (analogs of the active tab's molecule, or ideas) in one call. "
        "Each candidate is validated with RDKit; invalid ones, duplicates and the parent itself come back "
        "as rejected (fix and resend those only). Accepted ones open together on a new grid tab, laid out "
        "like the parent, and the user sees a table with properties and Tanimoto similarity to the parent. "
        "Do not call validate_structures first."
    ),
    summary=_summary_propose,
)
async def propose_structures_tool(args: ProposeIn, ctx: ToolContext) -> ToolResult:
    active = ctx.snapshot.get("active_tab") or {}
    pairs = [(c.smiles, c.rationale) for c in args.candidates]
    result: Evaluation = await run_in_threadpool(evaluate, args.parent_smiles, pairs, active.get("molfile"))
    if not result.ok:
        return {"ok": False, "errors": result.errors, "rejected": result.rejected}

    tab = await create_tab(
        ctx.session,
        ctx.workspace_id,
        name=args.title,
        content_format="mol",
        content=result.grid_molblock or "",
        smiles=[a.smiles for a in result.accepted],
        source="ai",
        origin_message_id=ctx.message_id,
    )
    ctx.emit_event("tab.create", {"tab": _tab_payload(tab), "activate": True})

    def _cards() -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
        items = [
            {
                **a.props,
                "index": a.index,
                "label": f"#{a.index}",
                "smiles": a.smiles,
                "rationale": a.rationale,
                "similarity": a.similarity,
                "molfile": a.molblock,
                "svg": depict_molblock(a.molblock, *GRID_CARD, tuple(a.changed_atoms)),
            }
            for a in result.accepted
        ]
        parent = (
            {"smiles": result.parent_smiles, "svg": depict_molblock(result.parent_molblock, *GRID_CARD)}
            if result.parent_molblock
            else None
        )
        return items, parent

    items, parent = await run_in_threadpool(_cards)
    await ctx.emit_artifact(
        "grid",
        {
            "title": args.title,
            "tab_id": tab.id,
            "tab_name": tab.name,
            "parent": parent,
            "items": items,
            "rejected": result.rejected,
        },
    )
    return {
        "ok": True,
        "tab_id": tab.id,
        "tab_name": tab.name,
        "parent_smiles": result.parent_smiles,
        "accepted": [_compact(i) for i in items],
        "rejected": result.rejected,
    }


# ---------------------------------------------------------------- open_in_new_tab


class OpenIn(BaseModel):
    smiles: list[str] = Field(..., min_length=1, max_length=50, description="Molecules to open, as SMILES.")
    title: str = Field(..., max_length=60, description="Short tab name.")


def _validated(smiles: list[str]) -> tuple[list[Any], list[str], list[dict[str, str]]]:
    mols, canon, rejected = [], [], []
    for s in smiles:
        mol, err = parse_smiles(s)
        if mol is None:
            rejected.append({"smiles": s, "reason": err or "invalid"})
            continue
        can = canonical_smiles(mol)
        if can in canon:
            continue
        mols.append(mol)
        canon.append(can)
    return mols, canon, rejected


@tool(
    name="open_in_new_tab",
    description=(
        "Open molecules in a new editor tab (several molecules are laid out on a grid). Use it when the "
        "user asks to open or show structures in the editor, e.g. after name_to_structure."
    ),
    summary=lambda r: f"opened {len(r['molecules'])} molecule(s)" if r.get("ok") else "nothing to open",
)
async def open_in_new_tab_tool(args: OpenIn, ctx: ToolContext) -> ToolResult:
    mols, canon, rejected = await run_in_threadpool(_validated, args.smiles)
    if not mols:
        return {"ok": False, "errors": ["no valid structures"], "rejected": rejected}
    content = await run_in_threadpool(layout_smiles, mols)
    tab = await create_tab(
        ctx.session,
        ctx.workspace_id,
        name=args.title,
        content_format="mol",
        content=content,
        smiles=canon,
        source="ai",
        origin_message_id=ctx.message_id,
    )
    ctx.emit_event("tab.create", {"tab": _tab_payload(tab), "activate": True})
    items = await run_in_threadpool(
        lambda: [{"smiles": s, "label": None, "svg": depict(s), "molfile": molblock(s)} for s in canon]
    )
    await ctx.emit_artifact("structure_set", {"title": f"Opened in “{tab.name}”", "tab_id": tab.id, "items": items})
    return {"ok": True, "tab_id": tab.id, "tab_name": tab.name, "molecules": canon, "rejected": rejected}


# ---------------------------------------------------------------- name_to_structure


class NameIn(BaseModel):
    name: str = Field(
        ..., min_length=1, max_length=200, description="Compound name: IUPAC, common, trade or drug name."
    )


_default_resolver: NameResolver | None = None


def _resolver(ctx: ToolContext) -> NameResolver:
    global _default_resolver
    if "names" in ctx.services:
        return ctx.services["names"]
    _default_resolver = _default_resolver or NameResolver()
    return _default_resolver


@tool(
    name="name_to_structure",
    description=(
        "Look up the structure for a compound name (OPSIN for systematic names, then PubChem). Use it "
        "instead of writing SMILES for named compounds from memory. The user sees a structure card; call "
        "open_in_new_tab if they want it in the editor."
    ),
    summary=lambda r: f"{r['source']}: {r['name']}" if r.get("ok") else "not found",
)
async def name_to_structure_tool(args: NameIn, ctx: ToolContext) -> ToolResult:
    result = await _resolver(ctx).resolve(args.name)
    if result["ok"]:
        svg, mol = await run_in_threadpool(lambda: (depict(result["smiles"]), molblock(result["smiles"])))
        await ctx.emit_artifact(
            "structure_set",
            {
                "title": f"{result['name']} ({result['source']})",
                "items": [{"smiles": result["smiles"], "label": result["name"], "svg": svg, "molfile": mol}],
            },
        )
    return result
