"""M3 tools: proposals and position scans on a grid tab, opening structures, names (PLAN §6)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator
from starlette.concurrency import run_in_threadpool

from chemomni.api.tabs import create_tab
from chemomni.chem.depict import depict, depict_molblock, molblock
from chemomni.chem.generate import Evaluation, evaluate
from chemomni.chem.layout import layout_smiles
from chemomni.chem.names import NameResolver
from chemomni.chem.props import canonical_smiles, parse_smiles
from chemomni.chem.scan import Change, scan, target_from_selection, target_from_smarts
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
        "note": item["rationale"],
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
    pairs = [(c.smiles, c.rationale) for c in args.candidates]
    return await present_candidates(ctx, args.title, args.parent_smiles, pairs)


async def present_candidates(
    ctx: ToolContext,
    title: str,
    parent_smiles: str | None,
    pairs: list[tuple[str, str]],
    *,
    molfile: str | None = None,
    skipped: list[dict[str, str]] | None = None,
    any_element: bool = False,
) -> ToolResult:
    """Validate candidates, open them on a grid tab, show the card; shared by every generating tool."""
    active = ctx.snapshot.get("active_tab") or {}
    result: Evaluation = await run_in_threadpool(
        lambda: evaluate(parent_smiles, pairs, molfile or active.get("molfile"), any_element=any_element)
    )
    rejected = [*result.rejected, *(skipped or [])]
    if not result.ok:
        return {"ok": False, "errors": result.errors, "rejected": rejected}

    tab = await create_tab(
        ctx.session,
        ctx.workspace_id,
        name=title,
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
            "title": title,
            "tab_id": tab.id,
            "tab_name": tab.name,
            "parent": parent,
            "items": items,
            "rejected": rejected,
        },
    )
    return {
        "ok": True,
        "tab_id": tab.id,
        "tab_name": tab.name,
        "parent_smiles": result.parent_smiles,
        "accepted": [_compact(i) for i in items],
        "rejected": rejected,
    }


# ---------------------------------------------------------------- scan_positions


class ChangeIn(BaseModel):
    operation: Literal["substitute", "replace_atom"] = Field(
        ...,
        description="substitute: replace one hydrogen by `group`. replace_atom: change one atom to `element` "
        "(phenyl -> pyridine is element N).",
    )
    group: str | None = Field(
        None,
        description="For substitute: the substituent as SMILES; its first atom attaches. "
        "F, Cl, C (methyl), CC, OC, N, O, C#N, C(F)(F)F, C(=O)O, [N+](=O)[O-].",
    )
    element: Literal["N", "O", "S", "C"] | None = Field(None, description="For replace_atom: the new element.")

    @model_validator(mode="after")
    def _arguments(self) -> ChangeIn:
        if self.operation == "substitute" and not self.group:
            raise ValueError("group is required for substitute")
        if self.operation == "replace_atom" and not self.element:
            raise ValueError("element is required for replace_atom")
        return self


class ScanIn(BaseModel):
    changes: list[ChangeIn] = Field(
        ...,
        min_length=1,
        max_length=3,
        description="The changes to make together. One entry: that change at every position. Several entries: "
        "every way of giving each change its own atom, e.g. [F, methyl] = one fluorine and one methyl on "
        "different atoms in all combinations; [F, F] = difluoro. Several changes belong in ONE call.",
    )
    atoms: list[int] | None = Field(
        None,
        max_length=200,
        description="1-based molfile atom numbers to change. Omit to use the atoms the user has selected.",
    )
    smarts: str | None = Field(
        None,
        description="Name the part by SMARTS when nothing is selected, e.g. 'c1ccccc1' for every phenyl. "
        "Needs parent_smiles if the active tab holds several molecules.",
    )
    parent_smiles: str | None = Field(None, description="Only with smarts: the molecule to search.")
    title: str = Field(..., max_length=60, description="Short tab name, e.g. 'F + Me scan'.")


def _summary_scan(r: ToolResult) -> str:
    if not r.get("ok"):
        return "nothing to scan"
    return f"{len(r['accepted'])} variant(s) of {r['selected']}"


@tool(
    name="scan_positions",
    description=(
        "Try simple changes at every possible position of part of a molecule and show all variants at once: "
        "the part the user selected in the editor (a phenyl, a ring, a side chain), or a part you name with "
        "SMARTS. 'Make this phenyl a pyridine' is one change (replace_atom N); 'monofluorinate' is one change "
        "(substitute F); 'difluorinate' is two (F, F); 'one fluorine and one methyl' is two (F, C). Put "
        "changes made together in one call, in `changes`; for alternatives ('try methyl, chloro and fluoro "
        "here') call once per alternative. Positions that are the same by symmetry are merged, and "
        "ortho/meta/para is named for a ring with one attachment point. Never enumerate positions or "
        "combinations yourself, and do not use it when the user wants one specific position (use "
        "edit_structure). If nothing is selected, ask the user to select the part."
    ),
    summary=_summary_scan,
)
async def scan_positions_tool(args: ScanIn, ctx: ToolContext) -> ToolResult:
    active = ctx.snapshot.get("active_tab") or {}
    molfile = active.get("molfile")
    selection = active.get("selection_atoms") or []
    if args.atoms or (selection and not args.smarts):
        if not molfile:
            return {"ok": False, "errors": ["the editor content is not available; name the part with smarts"]}
        target, err = await run_in_threadpool(target_from_selection, molfile, args.atoms or selection)
    elif args.smarts:
        parent = args.parent_smiles or _single_molecule(active)
        if parent is None:
            return {"ok": False, "errors": ["the active tab holds several molecules; pass parent_smiles"]}
        target, err = await run_in_threadpool(target_from_smarts, parent, args.smarts)
    else:
        return {
            "ok": False,
            "errors": ["nothing is selected: ask the user to select the part to change in the editor"],
        }
    if target is None:
        return {"ok": False, "errors": [err or "could not find the atoms"]}

    changes = [Change(c.operation, c.group, c.element) for c in args.changes]
    scanned = await run_in_threadpool(scan, target, changes)
    if not scanned.ok:
        return {"ok": False, "errors": scanned.errors, "selected": scanned.selected_smiles}
    result = await present_candidates(
        ctx,
        args.title,
        scanned.parent_smiles,
        scanned.candidates,
        molfile=molfile,
        skipped=scanned.skipped,
        any_element=any(c.operation == "replace_atom" for c in args.changes),
    )
    if result.get("ok"):
        result["selected"] = scanned.selected_smiles
        result["placements_tried"] = scanned.tried
        result["equivalent_placements_merged"] = scanned.merged
    return result


def _single_molecule(active: dict[str, Any]) -> str | None:
    smiles = active.get("smiles") or []
    return smiles[0] if len(smiles) == 1 else None


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
