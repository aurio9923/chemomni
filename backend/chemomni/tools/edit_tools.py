"""M2 tools: structure editing and highlighting on the editor (PLAN §6)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator
from starlette.concurrency import run_in_threadpool

from chemomni.api.tabs import create_tab
from chemomni.chem.depict import depict_molblock
from chemomni.chem.edit import EditResult, edit
from chemomni.chem.props import canonical_smiles, compute_light, parse_smiles
from chemomni.models import Tab, TabRead, utcnow
from chemomni.tools.refs import smiles_for
from chemomni.tools.registry import ToolContext, ToolResult, tool


def _canon(smiles: str) -> str | None:
    mol, _ = parse_smiles(smiles)
    return canonical_smiles(mol) if mol is not None else None


def _tab_payload(tab: Tab) -> dict[str, Any]:
    return TabRead.model_validate(tab).model_dump(mode="json")


# ---------------------------------------------------------------- edit_structure


class EditStructureIn(BaseModel):
    parent_smiles: str | None = Field(
        None, description="The molecule being edited, as it appears on the active tab. Or use parent_ref."
    )
    parent_ref: str | None = Field(
        None, description="Number of the compound being edited in the editor (see <compounds>), instead of SMILES."
    )
    new_smiles: str = Field(..., description="The complete edited molecule (whole SMILES, not a fragment).")
    description: str = Field(..., max_length=80, description="Short title of the change, e.g. '4-F aspirin'.")
    target: Literal["new_tab", "replace_active"] = Field(
        "new_tab",
        description="new_tab (default) keeps the original. Use replace_active only if the user asked to replace "
        "or overwrite the structure on the active tab.",
    )

    @model_validator(mode="after")
    def _one_parent(self) -> EditStructureIn:
        if bool(self.parent_smiles) == bool(self.parent_ref):
            raise ValueError("give exactly one of parent_smiles and parent_ref")
        return self


def _summary_edit(r: ToolResult) -> str:
    if not r.get("ok"):
        return "invalid edit"
    return {"replaced": "replaced active tab", "new_tab": "opened in a new tab"}.get(
        r["applied"], "opened in a new tab (active tab changed meanwhile)"
    )


@tool(
    name="edit_structure",
    description=(
        "Apply an edit the user asked for. Write the complete new molecule as SMILES; it is validated with "
        "RDKit and laid out on the parent's drawing. The result opens in a new tab unless the user asked to "
        "replace the active tab. The user sees a before/after card."
    ),
    summary=_summary_edit,
)
async def edit_structure_tool(args: EditStructureIn, ctx: ToolContext) -> ToolResult:
    active = ctx.snapshot.get("active_tab") or {}
    parent = args.parent_smiles
    if args.parent_ref:
        compound, err = smiles_for(ctx, args.parent_ref)
        if compound is None or compound.smiles is None:
            return {"ok": False, "errors": [err or f"unknown compound {args.parent_ref}"]}
        parent = compound.smiles
    result: EditResult = await run_in_threadpool(edit, parent or "", args.new_smiles, active.get("molfile"))
    if not result.ok:
        return {"ok": False, "errors": result.errors}

    # everything that can fail comes before the first change to the workspace: a failure after
    # the tab was created made the model retry and open the same edit again and again
    before = await run_in_threadpool(compute_light, parent or "")
    after = await run_in_threadpool(compute_light, args.new_smiles)
    # both sides keep the user's layout, so the change is easy to spot
    svg_before = await run_in_threadpool(depict_molblock, result.parent_molblock or "")
    svg_after = await run_in_threadpool(depict_molblock, result.molblock or "", 280, 200, tuple(result.changed_atoms))

    active_smiles = {_canon(s) for s in active.get("smiles", [])}
    on_active_tab = result.parent_smiles in active_smiles
    session = ctx.session
    applied = "new_tab"
    tab: Tab | None = None
    note: str | None = None

    if args.target == "replace_active":
        current = await session.get(Tab, active["id"]) if active.get("id") else None
        if current is None or current.is_deleted or not on_active_tab:
            note = "the parent is not on the active tab; opened in a new tab instead"
        elif current.revision != active.get("revision"):
            note = "the user edited the active tab while you were answering; opened in a new tab instead"
        else:
            content = result.document_molblock or result.molblock or ""
            others = [s for s in active.get("smiles", []) if _canon(s) != result.parent_smiles]
            current.content_format = "mol"
            current.content = content
            current.smiles_cache = [result.new_smiles or "", *others]
            current.revision += 1
            current.updated_at = utcnow()
            session.add(current)
            await session.commit()
            await session.refresh(current)
            tab, applied = current, "replaced"
            ctx.emit_event(
                "tab.replace",
                {
                    "tab_id": current.id,
                    "content_format": "mol",
                    "content": content,
                    "revision": current.revision,
                    "smiles": current.smiles_cache,
                },
            )
        if tab is None:
            applied = "new_tab_fallback"

    if tab is None:
        tab = await create_tab(
            session,
            ctx.workspace_id,
            name=args.description,
            content_format="mol",
            content=result.molblock or "",
            smiles=[result.new_smiles or ""],
            source="ai",
            origin_message_id=ctx.message_id,
        )
        ctx.emit_event("tab.create", {"tab": _tab_payload(tab), "activate": True})
        if applied == "new_tab_fallback":
            ctx.emit_event(
                "tab.replace_rejected", {"tab_id": active.get("id"), "reason": note, "fallback_tab_id": tab.id}
            )

    await ctx.emit_artifact(
        "diff",
        {
            "title": args.description,
            "applied": applied,
            "tab_id": tab.id,
            "tab_name": tab.name,
            "before": {**before, "svg": svg_before},
            "after": {**after, "svg": svg_after, "molfile": result.molblock},
        },
    )
    delta = {k: round(after[k] - before[k], 2) for k in ("mw", "clogp", "tpsa")} if before["ok"] and after["ok"] else {}
    return {
        "ok": True,
        "applied": applied,
        "note": note,
        "tab_id": tab.id,
        "tab_name": tab.name,
        "new_smiles": result.new_smiles,
        "formula": after.get("formula"),
        "delta": delta,
    }


# ---------------------------------------------------------------- highlight_atoms


class HighlightIn(BaseModel):
    atoms: list[int] = Field(
        ..., min_length=1, max_length=200, description="1-based atom numbers of the active tab's molfile."
    )


def _molfile_atom_count(molfile: str | None) -> int:
    for line in (molfile or "").splitlines():
        if "COUNTS" in line:
            try:
                return int(line.split()[3])
            except (IndexError, ValueError):
                return 0
    return 0


@tool(
    name="highlight_atoms",
    description="Select atoms on the active tab's editor so the user can see what you are talking about.",
    summary=lambda r: f"{len(r.get('atoms', []))} atom(s)" if r.get("ok") else "invalid atoms",
)
async def highlight_atoms_tool(args: HighlightIn, ctx: ToolContext) -> ToolResult:
    active = ctx.snapshot.get("active_tab") or {}
    n = _molfile_atom_count(active.get("molfile"))
    if not active.get("id") or n == 0:
        return {"ok": False, "errors": ["the active tab has no structure to highlight"]}
    bad = [a for a in args.atoms if not 1 <= a <= n]
    if bad:
        return {"ok": False, "errors": [f"atom numbers out of range 1..{n}: {bad}"]}
    atoms = sorted(set(args.atoms))
    ctx.emit_event("highlight", {"tab_id": active["id"], "atoms": atoms})
    return {"ok": True, "atoms": atoms}
