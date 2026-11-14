"""Compound numbers as tool arguments: the model names a number, the backend finds the structure (PLAN Q57)."""

from __future__ import annotations

from chemomni.chem.compounds import Compound, CompoundTable, build_compounds
from chemomni.tools.registry import ToolContext

# Appended to the description of every argument that takes a compound number. gpt-6-luna and claude-haiku-5-5
# passed parent_ref="1" on tabs without numbers in 5 of 16 edit calls; the model reads this, not the system prompt.
WHEN_NUMBERED = (
    " Use it only when <compounds> lists numbered structures and the user named one by its number; "
    "when the tab has no numbers, give the SMILES instead."
)


def table_of(ctx: ToolContext) -> CompoundTable:
    """The numbered structures of the active tab, from the editor state the client sent."""
    active = ctx.snapshot.get("active_tab") or {}
    return build_compounds(active.get("ket"), active.get("molfile"))


def compound_for(ctx: ToolContext, ref: str) -> tuple[Compound | None, str | None]:
    return table_of(ctx).resolve(ref)


def smiles_for(ctx: ToolContext, ref: str) -> tuple[Compound | None, str | None]:
    """Like ``compound_for`` but for tools that need a SMILES: a query drawing has none."""
    compound, err = compound_for(ctx, ref)
    if compound is not None and compound.smiles is None:
        return None, f"compound {compound.label} is a query drawing (atom lists, any atom, ...) and has no SMILES"
    return compound, err
