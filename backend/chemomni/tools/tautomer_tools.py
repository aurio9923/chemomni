"""Tautomers of a molecule and its canonical tautomer (decisions Q63 and Q64).

RDKit enumerates; the model only says which molecule and whether the user wants the list or just the
canonical form. The model never writes a tautomer's SMILES. Forms no chemist would draw (see
``chem/tautomers.py``) are left off the list and counted in ``hidden``, unless ``all_forms`` is set.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field
from rdkit import Chem
from starlette.concurrency import run_in_threadpool

from chemomni.chem import tautomers as taut
from chemomni.chem.props import canonical_smiles, parse_smiles
from chemomni.chem.scan import target_from_selection
from chemomni.tools.generate_tools import _single_molecule, present_candidates
from chemomni.tools.refs import smiles_for
from chemomni.tools.registry import ToolContext, ToolResult, tool

MAX_SHOWN = 20  # one grid; more are counted and reported


class TautomersIn(BaseModel):
    ref: str | None = Field(
        None,
        description="Number of the compound in the editor to work on (see <compounds>), e.g. '3'. Omit to use "
        "the molecule that holds the user's selection, or the only molecule on the tab.",
    )
    smiles: str | None = Field(
        None,
        description="Only a SMILES the user typed into the chat. Never one from memory: for a name use "
        "name_to_structure first.",
    )
    show: Literal["all", "canonical", "none"] = Field(
        "all",
        description="'all' opens every tautomer on a grid tab (the canonical one is labelled). 'canonical' "
        "opens only the canonical tautomer, and only when it differs from the drawn form. 'none' opens no tab "
        "and only reports the canonical form: use it to compare molecules.",
    )
    all_forms: bool = Field(
        False,
        description="Leave it false. True also lists the unfavoured forms that are hidden by default (a ring "
        "that loses its aromaticity, an amide written as C(OH)=N or as an enol): only when the user asks for "
        "every form or for the hidden ones.",
    )
    title: str = Field("Tautomers", max_length=60, description="Short tab name, e.g. 'Tautomers of 3'.")


async def _molecule(args: TautomersIn, ctx: ToolContext) -> tuple[Chem.Mol | None, str | None]:
    """The molecule: a numbered compound, a typed SMILES, the one the selection is in, or the only one on the tab."""
    active = ctx.snapshot.get("active_tab") or {}
    if args.ref and args.smiles:
        return None, "give `ref` or `smiles`, not both"
    if args.ref:
        compound, err = smiles_for(ctx, args.ref)
        if compound is None or compound.smiles is None:
            return None, err or f"unknown compound {args.ref}"
        smiles = compound.smiles
    elif args.smiles:
        smiles = args.smiles
    else:
        selection, molfile = active.get("selection_atoms") or [], active.get("molfile")
        if selection and molfile:
            target, err = await run_in_threadpool(target_from_selection, molfile, selection)
            if target is None:
                return None, err
            return target.mol, None
        smiles = _single_molecule(active)
        if smiles is None:
            return None, "the active tab holds several molecules: give `ref` (a compound number) or select one"
    mol, err = parse_smiles(smiles)
    if mol is None:
        return None, f"compound {args.ref}: {err}" if args.ref else err
    return mol, None


def _summary(r: ToolResult) -> str:
    if not r.get("ok"):
        return "no tautomers"
    if r.get("tautomer_count", 0) <= 1:
        return "no other tautomer"
    if r.get("is_canonical") and not r.get("accepted"):
        return "already canonical"
    shown = len(r.get("accepted", []))
    hidden = r.get("hidden", {}).get("count", 0)
    if not shown and hidden:
        return f"no reasonable tautomer ({hidden} hidden)"
    rest = r["tautomer_count"] - 1 - hidden
    return f"{shown} tautomer(s)" + (f" of {rest}" if rest > shown else "") + (f", {hidden} hidden" if hidden else "")


def _why(reasons: tuple[str, ...]) -> str:
    return "; ".join(taut.UNFAVOURED_WHY[r] for r in reasons)


@tool(
    name="tautomers",
    description=(
        "Enumerate the tautomers of one molecule with RDKit and find its canonical tautomer. Use it when the "
        "user asks for the tautomers or other forms of a molecule, for its canonical tautomer, or whether two "
        "drawings are the same compound written as different tautomers. The molecule is a numbered compound "
        "(`ref`), the one the selection is in, or the only molecule on the tab. With show='all' every other "
        "tautomer is opened on a grid tab (at most 20, the canonical one first and labelled); with "
        "show='canonical' only the canonical one is, and only if it differs from the drawing; show='none' opens "
        "no tab. To tell whether two drawings are one compound, call it for each with show='none' and compare "
        "the canonical SMILES. The result gives the canonical SMILES, how many tautomers there are and whether "
        "the search was complete. RDKit also generates forms no chemist would draw: a ring that loses its "
        "aromaticity (phenol as a cyclohexadienone), an amide, lactam or urea written as C(OH)=N, an amide, "
        "acid or ester written as its enol. They are left off the list and counted in `hidden`; "
        "all_forms=true lists them too. Stereochemistry "
        "is kept and the charge state is not changed. The canonical tautomer is RDKit's consistent choice, "
        "useful for comparing and merging structures; it is not a prediction of which tautomer predominates "
        "in water or in a protein, so do not say that it is. Never write a tautomer's SMILES yourself."
    ),
    summary=_summary,
)
async def tautomers_tool(args: TautomersIn, ctx: ToolContext) -> ToolResult:
    mol, err = await _molecule(args, ctx)
    if mol is None:
        return {"ok": False, "errors": [err or "could not find the molecule"]}
    err = taut.check_one_molecule(mol)
    if err:
        return {"ok": False, "errors": [err]}
    try:
        found = await run_in_threadpool(taut.tautomers, mol)
    except ValueError as exc:
        return {"ok": False, "errors": [str(exc)]}

    result: ToolResult = {
        "ok": True,
        "molecule": found.parent,
        "canonical": found.canonical,
        "is_canonical": found.is_canonical,
        "tautomer_count": found.total,
        "complete": found.complete,
    }
    if not found.complete:
        result["partial"] = (
            "the molecule has very many tautomers and the search stopped early: the list and the canonical "
            "form are incomplete"
        )
    if not found.others:
        result["note"] = "RDKit finds no other tautomer of this molecule"
        return result
    if found.canonical in found.unfavoured:  # e.g. a hydroxamic acid, whose canonical form is C(OH)=N-OH
        result["canonical_is_unfavoured"] = _why(found.unfavoured[found.canonical])
    hidden = {} if args.all_forms else found.unfavoured
    if hidden:
        result["hidden"] = {
            "count": len(hidden),
            "why": {taut.UNFAVOURED_WHY[k]: n for k, n in found.hidden_by_reason().items()},
            "to_see_them": "call again with all_forms=true",
        }

    if args.show == "none":
        result["note"] = "no tab opened (show='none'); compare `canonical` between molecules"
        return result
    if args.show == "all":
        wanted = [s for s in found.others if s not in hidden]
        if not wanted:
            result["note"] = "every other form RDKit generates is an unfavoured one (see `hidden`): no tab opened"
            return result
    else:  # the canonical form is what was asked for, favoured or not
        result.pop("hidden", None)
        wanted = [] if found.is_canonical else [found.canonical]
        if not wanted:
            result["note"] = "the drawn form is already the canonical tautomer"
            return result
    shown = wanted[:MAX_SHOWN]
    pairs = [
        (
            s,
            ("canonical tautomer" if s == found.canonical else f"tautomer {i} of {len(wanted)}")
            + (f" (unfavoured: {_why(found.unfavoured[s])})" if s in found.unfavoured else ""),
        )
        for i, s in enumerate(shown, start=1)
    ]
    presented = await present_candidates(
        ctx, args.title, canonical_smiles(mol), pairs, any_element=True, bond_changes=True
    )
    if not presented.get("ok"):
        return presented
    result.update({k: presented[k] for k in ("tab_id", "tab_name", "accepted", "rejected")})
    if len(wanted) > len(shown):
        result["not_shown"] = len(wanted) - len(shown)
        result["to_see_the_rest"] = (
            "only the first 20 are drawn; ask for the canonical form alone or name a smaller molecule"
        )
    return result
