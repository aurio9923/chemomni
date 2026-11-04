"""M1 chemistry tools. SVGs and molfiles go to the UI as artifacts, never into the LLM payload."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from chemomni.chem.depict import depict, molblock
from chemomni.chem.props import canonical_smiles, compute_properties, parse_smiles
from chemomni.chem.similarity import substructure, tanimoto
from chemomni.tools.registry import ToolContext, ToolResult, tool

SmilesList = Field(..., min_length=1, max_length=50, description="SMILES strings, one per molecule.")


def _structure_item(smiles: str, label: str | None = None, highlight: tuple[int, ...] = ()) -> dict[str, Any]:
    return {
        "smiles": smiles,
        "label": label,
        "svg": depict(smiles, highlight=highlight),
        "molfile": molblock(smiles),
    }


# ---------------------------------------------------------------- compute_properties


class ComputePropertiesIn(BaseModel):
    smiles: list[str] = SmilesList


def _summary_props(r: ToolResult) -> str:
    ok = sum(1 for row in r.get("rows", []) if row.get("ok"))
    return f"{ok} molecule(s)"


@tool(
    name="compute_properties",
    description=(
        "Compute standard medicinal-chemistry properties with RDKit: formula, MW, exact mass, "
        "cLogP (Crippen), TPSA, HBD/HBA, rotatable bonds, rings, Fsp3, formal charge, stereocentres, "
        "Lipinski violations, Veber, QED, SA score, PAINS and Brenk alerts, InChIKey. "
        "The user sees a property table; refer to it instead of repeating every number."
    ),
    summary=_summary_props,
)
async def compute_properties_tool(args: ComputePropertiesIn, ctx: ToolContext) -> ToolResult:
    rows = await run_in_threadpool(lambda: [compute_properties(s) for s in args.smiles])
    good = [r for r in rows if r["ok"]]
    if good:
        items = await run_in_threadpool(lambda: [_structure_item(r["smiles"]) for r in good])
        await ctx.emit_artifact(
            "table",
            {"title": "Properties", "rows": [{**r, **item} for r, item in zip(good, items, strict=True)]},
        )
    return {"ok": bool(good), "rows": rows}


# ---------------------------------------------------------------- validate_structures


class ValidateIn(BaseModel):
    smiles: list[str] = SmilesList


@tool(
    name="validate_structures",
    description=(
        "Check that SMILES strings parse and sanitize. Returns canonical SMILES or an error per input. "
        "Use it before presenting any structure you wrote yourself."
    ),
    summary=lambda r: f"{sum(1 for x in r['rows'] if x['ok'])}/{len(r['rows'])} valid",
)
async def validate_structures_tool(args: ValidateIn, ctx: ToolContext) -> ToolResult:
    def _one(s: str) -> dict[str, Any]:
        mol, err = parse_smiles(s)
        if mol is None:
            return {"input": s, "ok": False, "canonical_smiles": None, "errors": [err]}
        undefined = sum(1 for _, lab in _centers(mol) if lab == "?")
        warnings = [f"{undefined} undefined stereocentre(s)"] if undefined else []
        return {"input": s, "ok": True, "canonical_smiles": canonical_smiles(mol), "warnings": warnings}

    rows = await run_in_threadpool(lambda: [_one(s) for s in args.smiles])
    return {"ok": all(r["ok"] for r in rows), "rows": rows}


def _centers(mol: Any) -> list[tuple[int, str]]:
    from rdkit import Chem

    return Chem.FindMolChiralCenters(mol, includeUnassigned=True, useLegacyImplementation=False)


# ---------------------------------------------------------------- similarity


class SimilarityIn(BaseModel):
    smiles_a: str = Field(..., description="First molecule (SMILES).")
    smiles_b: str = Field(..., description="Second molecule (SMILES).")


@tool(
    name="similarity",
    description="Tanimoto similarity of two molecules on Morgan fingerprints (radius 2, 2048 bits).",
    summary=lambda r: f"Tanimoto {r['tanimoto']}" if r.get("ok") else "invalid input",
)
async def similarity_tool(args: SimilarityIn, ctx: ToolContext) -> ToolResult:
    result = await run_in_threadpool(tanimoto, args.smiles_a, args.smiles_b)
    if result["ok"]:
        items = await run_in_threadpool(
            lambda: [_structure_item(result["smiles_a"], "A"), _structure_item(result["smiles_b"], "B")]
        )
        await ctx.emit_artifact("structure_set", {"title": f"Tanimoto similarity {result['tanimoto']}", "items": items})
    return result


# ---------------------------------------------------------------- substructure_search


class SubstructureIn(BaseModel):
    smiles: list[str] = SmilesList
    smarts: str = Field(..., description="SMARTS pattern to search for, e.g. 'C(=O)[OX2H1]' for a carboxylic acid.")


@tool(
    name="substructure_search",
    description="Find which molecules contain a SMARTS pattern and which atoms match.",
    summary=lambda r: (
        f"{sum(1 for x in r['rows'] if x.get('match'))}/{len(r['rows'])} match" if r.get("ok") else "invalid SMARTS"
    ),
)
async def substructure_search_tool(args: SubstructureIn, ctx: ToolContext) -> ToolResult:
    result = await run_in_threadpool(substructure, args.smiles, args.smarts)
    if result["ok"]:
        hits = [r for r in result["rows"] if r.get("match")]
        if hits:
            items = await run_in_threadpool(
                lambda: [_structure_item(r["input"], None, tuple(r["atoms"])) for r in hits]
            )
            await ctx.emit_artifact("structure_set", {"title": f"Matches for {args.smarts}", "items": items})
    return result
