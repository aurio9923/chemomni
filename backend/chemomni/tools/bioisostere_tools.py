"""M5 tools: which bioisostere rules fit, and applying them at every position (PLAN §6, decision Q60).

The model chooses rules by id; RDKit finds the places, builds the products and merges duplicates
(``chem.bioisostere``). The model never writes the SMARTS or the SMILES of a replacement.
"""

from __future__ import annotations

from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from chemomni.chem import bioisostere as bio
from chemomni.chem.bioisostere_rules import Rule, rules
from chemomni.chem.props import canonical_smiles
from chemomni.chem.scan import Target, target_from_selection, target_from_smarts
from chemomni.tools.generate_tools import _single_molecule, present_candidates
from chemomni.tools.refs import smiles_for
from chemomni.tools.registry import ToolContext, ToolResult, tool

MAX_RULES_PER_CALL = 10
MAX_RISK_NOTES = 2  # per rule, to keep the answer short


class TargetIn(BaseModel):
    atoms: list[int] | None = Field(
        None,
        max_length=200,
        description="1-based molfile atom numbers of the part to replace. Omit to use the atoms the user has "
        "selected, or the whole molecule when nothing is selected.",
    )
    ref: str | None = Field(
        None, description="Number of the compound in the editor to work on (see <compounds>), e.g. '3'."
    )
    smarts: str | None = Field(
        None,
        description="Limit the work to the atoms of a pattern you name (e.g. 'c1ccccc1' for phenyls). Rarely "
        "needed: the rules find their own part. Applies to `ref`, or to the only molecule on the tab.",
    )


class FindIn(TargetIn):
    family: str | None = Field(
        None,
        description="Only rules of one group: " + ", ".join(rules().families) + ". Omit to look at all of them.",
    )


class ApplyIn(TargetIn):
    rule_ids: list[str] = Field(
        ...,
        min_length=1,
        max_length=MAX_RULES_PER_CALL,
        description="Rule ids exactly as find_bioisosteres returned them. Choose the ones that answer the "
        "question; a few are usually enough.",
    )
    title: str = Field(..., max_length=60, description="Short tab name, e.g. 'Acid isosteres of 3'.")


# ---------------------------------------------------------------- finding the target


async def _target(args: TargetIn, ctx: ToolContext) -> tuple[Target | None, str | None]:
    """The molecule and the atoms the rules may replace. Explicit atoms, then a numbered compound, then the
    user's selection, then the only molecule on the tab."""
    active = ctx.snapshot.get("active_tab") or {}
    molfile = active.get("molfile")
    selection = active.get("selection_atoms") or []

    def resolve_ref() -> tuple[str | None, str | None]:
        if args.ref:
            compound, err = smiles_for(ctx, args.ref)
            if compound is None or compound.smiles is None:
                return None, err or f"unknown compound {args.ref}"
            return compound.smiles, None
        smiles = _single_molecule(active)
        if smiles is None:
            return None, "the active tab holds several molecules: give `ref` (a compound number) or select the part"
        return smiles, None

    if args.atoms or (selection and not args.ref and not args.smarts):
        if not molfile:
            return None, "the editor content is not available; give `ref`"
        return await run_in_threadpool(target_from_selection, molfile, args.atoms or selection)
    smiles, err = resolve_ref()
    if smiles is None:
        return None, err
    if args.smarts:
        return await run_in_threadpool(target_from_smarts, smiles, args.smarts)
    target, err = await run_in_threadpool(bio.whole_molecule, smiles)
    if target is None:
        return None, f"compound {args.ref}: {err}" if args.ref else err
    return target, None


def _risks(rule: Rule) -> list[str]:
    return [f"{r.topic}: {r.note}" for r in rule.risks[:MAX_RISK_NOTES]]


def _has_selection(target: Target) -> bool:
    return len(target.atoms) < target.mol.GetNumAtoms()


# ---------------------------------------------------------------- find_bioisosteres


def _summary_find(r: ToolResult) -> str:
    if not r.get("ok"):
        return "nothing to look at"
    return f"{len(r['rules'])} rule(s) fit"


@tool(
    name="find_bioisosteres",
    description=(
        "List the bioisostere rules that fit a molecule or part of it: acid replacements (tetrazole, acyl "
        "sulfonamide, ...), amide replacements, phenyl and ring swaps, linker and substituent swaps. Call it "
        "first when the user asks for bioisosteres or replacements of a group, then call apply_bioisosteres "
        "with ids from this list. For each rule that fits you get its id, name, class, how many places it fits, "
        "how many different products that gives, and short qualitative risk notes. The target is the user's "
        "selection, a numbered compound (`ref`), or the whole molecule when nothing is selected. A rule that "
        "fits is a starting point, not a recommendation: the notes say what to watch."
    ),
    summary=_summary_find,
)
async def find_bioisosteres_tool(args: FindIn, ctx: ToolContext) -> ToolResult:
    table = rules()
    if args.family and args.family not in table.families:
        return {"ok": False, "errors": [f"unknown family {args.family!r}; choose from: {', '.join(table.families)}"]}
    target, err = await _target(args, ctx)
    if target is None:
        return {"ok": False, "errors": [err or "could not find the atoms"]}
    candidates = table.of_family(args.family) if args.family else table.active
    runs = await run_in_threadpool(bio.find, target, candidates)
    fits = [r for r in runs if r.matches]
    result: ToolResult = {
        "ok": True,
        "molecule": canonical_smiles(target.mol),
        "rules": [
            {
                "id": r.rule.id,
                "name": r.rule.name,
                "name_ja": r.rule.name_ja,
                "family": r.rule.family,
                "class": r.rule.rule_class,
                "places": r.positions,
                "products": len(r.products),
                "new_stereocentre": r.rule.creates_stereocenter,
                "risks": _risks(r.rule),
            }
            for r in fits
        ],
    }
    outside = [r.rule.id for r in runs if not r.matches]
    if outside and _has_selection(target):
        result["fit_only_outside_the_selection"] = outside
    if not fits:
        result["note"] = (
            "no rule fits the selected atoms" if _has_selection(target) else "no rule fits this molecule"
        ) + (f" in the group {args.family}" if args.family else "")
    return result


# ---------------------------------------------------------------- apply_bioisosteres


def _summary_apply(r: ToolResult) -> str:
    if not r.get("ok"):
        return "no products"
    shown, total = len(r["accepted"]), r["distinct_products"]
    return f"{shown} product(s)" + (f" of {total}" if total > shown else "")


@tool(
    name="apply_bioisosteres",
    description=(
        "Apply bioisostere rules, chosen by id from find_bioisosteres, at EVERY place they fit, and show all "
        "distinct products on a new grid tab. Each product is labelled with the rule id and name; places that "
        "are the same by symmetry give one product, and the same molecule from two rules is shown once. "
        "The target is the user's selection (only the part the rule replaces must be inside it), a numbered "
        "compound (`ref`), or the whole molecule. Never write the SMILES of a replacement yourself and never "
        "use an id that find_bioisosteres did not return. At most 20 products are shown; the result says how "
        "many more there were and how to narrow."
    ),
    summary=_summary_apply,
)
async def apply_bioisosteres_tool(args: ApplyIn, ctx: ToolContext) -> ToolResult:
    table = rules()
    ids = list(dict.fromkeys(args.rule_ids))
    chosen = [table.get(i) for i in ids]
    unknown = [i for i, r in zip(ids, chosen, strict=True) if r is None]
    if unknown:
        return {
            "ok": False,
            "errors": [f"unknown rule id(s): {', '.join(unknown)}. Call find_bioisosteres and use the ids it returns"],
        }
    target, err = await _target(args, ctx)
    if target is None:
        return {"ok": False, "errors": [err or "could not find the atoms"]}

    applied = await run_in_threadpool(bio.apply, target, [r for r in chosen if r is not None])
    overview = [
        {
            "id": run.rule.id,
            "name": run.rule.name,
            "places": run.positions,
            "products": len(run.products),
            "risks": _risks(run.rule),
            **({"fits_only_outside_the_selection": True} if not run.matches and run.outside else {}),
        }
        for run in applied.runs
    ]
    if not applied.ok:
        return {"ok": False, "errors": applied.errors, "rules": overview}

    result = await present_candidates(
        ctx,
        args.title,
        canonical_smiles(target.mol),
        [(c.smiles, c.rationale) for c in applied.candidates],
        skipped=applied.skipped,
    )
    if not result.get("ok"):
        return result
    shown = len(result["accepted"])
    result["rules"] = [o for o in overview if o["products"]]
    result["rules_without_a_fit"] = [o["id"] for o in overview if not o["products"]]
    result["distinct_products"] = applied.total
    if applied.total > shown:
        result["not_shown"] = applied.total - shown
        result["to_see_the_rest"] = "name fewer rules, or select the part to change"
    racemic = [r.rule.id for r in applied.runs if r.rule.creates_stereocenter and r.products]
    if racemic:
        result["new_stereocentre_unspecified"] = racemic
    return result
