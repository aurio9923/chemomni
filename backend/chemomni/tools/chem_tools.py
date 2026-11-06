"""M1 chemistry tools. SVGs and molfiles go to the UI as artifacts, never into the LLM payload."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator
from rdkit import Chem
from starlette.concurrency import run_in_threadpool

from chemomni.chem.depict import depict, depict_molblock, molblock
from chemomni.chem.props import canonical_smiles, compute_properties, parse_smiles
from chemomni.chem.similarity import match_compounds, substructure, tanimoto
from chemomni.tools.refs import compound_for, smiles_for, table_of
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


RefsField = Field(
    default_factory=list,
    max_length=50,
    description="Numbers the user wrote next to structures in the editor, e.g. ['1', '3a'] (see <compounds>). "
    "Prefer these to copying SMILES.",
)


class ComputePropertiesIn(BaseModel):
    smiles: list[str] = Field(default_factory=list, max_length=50, description="SMILES strings, one per molecule.")
    refs: list[str] = RefsField

    @model_validator(mode="after")
    def _something(self) -> ComputePropertiesIn:
        if not self.smiles and not self.refs:
            raise ValueError("give smiles or refs")
        return self


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
    pairs: list[tuple[str | None, str]] = [(None, s) for s in args.smiles]
    errors: list[str] = []
    for ref in args.refs:
        compound, err = smiles_for(ctx, ref)
        if compound is None or compound.smiles is None:
            errors.append(err or f"unknown compound {ref}")
        else:
            pairs.append((compound.label, compound.smiles))
    if errors:
        return {"ok": False, "errors": errors}
    rows = await run_in_threadpool(lambda: [{**compute_properties(s), "ref": label} for label, s in pairs])
    good = [r for r in rows if r["ok"]]
    if good:
        items = await run_in_threadpool(lambda: [_structure_item(r["smiles"], r["ref"]) for r in good])
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
    smiles_a: str | None = Field(None, description="First molecule (SMILES), or use ref_a.")
    smiles_b: str | None = Field(None, description="Second molecule (SMILES), or use ref_b.")
    ref_a: str | None = Field(None, description="Number of the first compound in the editor (see <compounds>).")
    ref_b: str | None = Field(None, description="Number of the second compound in the editor.")

    @model_validator(mode="after")
    def _both(self) -> SimilarityIn:
        if not (self.smiles_a or self.ref_a) or not (self.smiles_b or self.ref_b):
            raise ValueError("give each molecule as SMILES or as a compound number")
        return self


@tool(
    name="similarity",
    description="Tanimoto similarity of two molecules on Morgan fingerprints (radius 2, 2048 bits).",
    summary=lambda r: f"Tanimoto {r['tanimoto']}" if r.get("ok") else "invalid input",
)
async def similarity_tool(args: SimilarityIn, ctx: ToolContext) -> ToolResult:
    sides: list[tuple[str, str]] = []
    for ref, smiles, default in ((args.ref_a, args.smiles_a, "A"), (args.ref_b, args.smiles_b, "B")):
        if ref:
            compound, err = smiles_for(ctx, ref)
            if compound is None or compound.smiles is None:
                return {"ok": False, "errors": [err or f"unknown compound {ref}"]}
            sides.append((compound.smiles, compound.label or default))
        else:
            sides.append((smiles or "", default))
    result = await run_in_threadpool(tanimoto, sides[0][0], sides[1][0])
    if result["ok"]:
        items = await run_in_threadpool(
            lambda: [_structure_item(result["smiles_a"], sides[0][1]), _structure_item(result["smiles_b"], sides[1][1])]
        )
        await ctx.emit_artifact("structure_set", {"title": f"Tanimoto similarity {result['tanimoto']}", "items": items})
    return result


# ---------------------------------------------------------------- substructure_search


class SubstructureIn(BaseModel):
    smarts: str | None = Field(
        None,
        description="SMARTS pattern to search for, e.g. 'C(=O)[OX2H1]' for a carboxylic acid. "
        "Use query_ref instead when the user points at a structure in the editor.",
    )
    query_ref: str | None = Field(
        None,
        description="Number of a compound in the editor (see <compounds>), e.g. '1', used as the query. The query "
        "is the drawing itself, exactly as drawn (any-atom and atom-list atoms included), so no SMARTS is needed.",
    )
    smiles: list[str] = Field(default_factory=list, max_length=50, description="Molecules to search, as SMILES.")
    target_refs: list[str] = Field(
        default_factory=list, max_length=50, description="Numbers of the compounds in the editor to search."
    )

    @model_validator(mode="after")
    def _one_query(self) -> SubstructureIn:
        if bool(self.smarts) == bool(self.query_ref):
            raise ValueError("give exactly one of smarts and query_ref")
        return self


@tool(
    name="substructure_search",
    description=(
        "Find which molecules contain a substructure and which atoms match. The query is a SMARTS pattern or, "
        'better when the user refers to a compound in the editor ("use compound 1 as the query"), '
        "`query_ref`: the structure is taken from the editor drawing, so you never write it yourself. "
        "Without `smiles` and `target_refs`, every other structure on the active tab is searched. "
        "Atoms match by element and bonds by order (aromatic only to aromatic); hydrogens, charges and "
        "stereochemistry of the query are not required."
    ),
    summary=lambda r: (
        f"{r['matches']}/{r['searched']} match" if r.get("ok") else (r.get("errors") or ["invalid search"])[0]
    ),
)
async def substructure_search_tool(args: SubstructureIn, ctx: ToolContext) -> ToolResult:
    table = table_of(ctx)
    query_compound = None
    if args.query_ref:
        query_compound, err = compound_for(ctx, args.query_ref)
        if query_compound is None:
            return {"ok": False, "errors": [err or f"unknown compound {args.query_ref}"]}

    # who is searched
    targets = []
    errors: list[str] = []
    for ref in args.target_refs:
        compound, err = compound_for(ctx, ref)
        if compound is None:
            errors.append(err or f"unknown compound {ref}")
        else:
            targets.append(compound)
    if errors:
        return {"ok": False, "errors": errors}
    if not args.smiles and not args.target_refs:
        targets = [c for c in table.compounds if c is not query_compound]
        if not targets:
            return {"ok": False, "errors": ["there are no other structures on the active tab to search"]}

    def _run() -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        if query_compound is not None:
            query = query_compound.mol
            described = {"ref": query_compound.label, "structure": query_compound.shown}
        else:
            query = Chem.MolFromSmarts(args.smarts or "")
            if query is None:
                return [], f"invalid SMARTS: {args.smarts}", {}
            described = {"smarts": args.smarts}
        rows += match_compounds(query, targets)
        if args.smiles:
            rows += substructure(args.smiles, args.smarts)["rows"] if args.smarts else _smiles_rows(query, args.smiles)
        return rows, "", described

    rows, error, described = await run_in_threadpool(_run)
    if error:
        return {"ok": False, "errors": [error]}
    hits = [r for r in rows if r.get("match")]
    if hits or query_compound is not None:
        items = await run_in_threadpool(lambda: _search_items(query_compound, hits, targets))
        title = f"Matches for compound {query_compound.label}" if query_compound else f"Matches for {args.smarts}"
        await ctx.emit_artifact("structure_set", {"title": title, "items": items})
    result: ToolResult = {
        "ok": True,
        "query": described,
        "searched": len(rows),
        "matches": len(hits),
        "rows": rows,
    }
    if query_compound is not None:
        result["note"] = "drawn structure used as the query; stereochemistry ignored"
    return result


def _smiles_rows(query: Chem.Mol, smiles: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for s in smiles:
        mol, err = parse_smiles(s)
        if mol is None:
            rows.append({"input": s, "ok": False, "error": err})
            continue
        found = mol.GetSubstructMatches(query)
        rows.append(
            {
                "input": s,
                "ok": True,
                "smiles": canonical_smiles(mol),
                "match": bool(found),
                "n_matches": len(found),
                "atoms": sorted({i for m in found for i in m}),
            }
        )
    return rows


def _search_items(query: Any, hits: list[dict[str, Any]], targets: list[Any]) -> list[dict[str, Any]]:
    """Cards for the query (when it is a drawn compound) and every hit, matched atoms highlighted."""
    items: list[dict[str, Any]] = []

    def _compound_item(compound: Any, label: str, highlight: tuple[int, ...]) -> dict[str, Any]:
        block = Chem.MolToV3KMolBlock(compound.mol)
        return {
            "smiles": compound.shown,
            "label": label,
            "svg": depict_molblock(block, 280, 200, highlight),
            "molfile": block,
        }

    if query is not None:
        items.append(_compound_item(query, f"query {query.label}", tuple(range(query.mol.GetNumAtoms()))))
    by_index = {c.index: c for c in targets}
    for row in hits:
        compound = by_index.get(row.get("index", -1)) if "index" in row else None
        if compound is not None:
            items.append(_compound_item(compound, compound.label or f"#{compound.index}", tuple(row["atoms"])))
        else:
            items.append(_structure_item(row["input"], None, tuple(row["atoms"])))
    return items
