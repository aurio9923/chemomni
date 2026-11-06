"""Checking structures proposed by the LLM (PLAN §6, tool 4; decision Q23).

Accepted: parses and sanitizes, not a duplicate, not the parent. Filters such as Lipinski or PAINS
never reject; they are shown in the table. Accepted molecules are laid out on the parent's drawing
so the grid reads as a series.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from rdkit import Chem, DataStructs
from rdkit.Chem import rdFingerprintGenerator

from chemomni.chem.edit import align, parent_template
from chemomni.chem.layout import bbox, centred, grid_molblock, to_ketcher_scale
from chemomni.chem.props import canonical_smiles, compute_properties, parse_smiles

GRID_MCS_TIMEOUT_S = 1  # per candidate; 20 candidates must fit in the 30 s tool budget
_morgan = rdFingerprintGenerator.GetMorganGenerator(radius=2, fpSize=2048)


@dataclass
class Accepted:
    index: int  # 1-based position on the grid
    smiles: str
    rationale: str
    props: dict[str, Any]
    similarity: float | None  # Tanimoto to the parent (Morgan r=2, 2048 bits)
    molblock: str  # this molecule alone, aligned to the parent, at Ketcher's scale
    changed_atoms: list[int]  # 0-based atoms outside the common substructure with the parent


@dataclass
class Evaluation:
    ok: bool
    errors: list[str] = field(default_factory=list)
    parent_smiles: str | None = None
    parent_molblock: str | None = None
    accepted: list[Accepted] = field(default_factory=list)
    rejected: list[dict[str, str]] = field(default_factory=list)
    grid_molblock: str | None = None


def evaluate(parent_smiles: str | None, candidates: list[tuple[str, str]], molfile: str | None = None) -> Evaluation:
    """``candidates`` are (SMILES, rationale) pairs; ``molfile`` is the active tab (for the layout)."""
    parent = None
    if parent_smiles:
        parent, err = parse_smiles(parent_smiles)
        if parent is None:
            return Evaluation(ok=False, errors=[f"parent: {err}"])
    parent_can = canonical_smiles(parent) if parent is not None else None

    seen: dict[str, int] = {}
    chosen: list[tuple[Chem.Mol, str, str]] = []
    rejected: list[dict[str, str]] = []
    for smiles, rationale in candidates:
        mol, err = parse_smiles(smiles)
        if mol is None:
            rejected.append({"smiles": smiles, "reason": err or "invalid"})
            continue
        can = canonical_smiles(mol)
        if can == parent_can:
            rejected.append({"smiles": smiles, "reason": "identical to the parent"})
        elif can in seen:
            rejected.append({"smiles": smiles, "reason": f"duplicate of #{seen[can]}"})
        else:
            seen[can] = len(chosen) + 1
            chosen.append((mol, can, rationale))
    if not chosen:
        return Evaluation(ok=False, errors=["no valid new structures"], parent_smiles=parent_can, rejected=rejected)

    placed, changed = _layout(parent, [m for m, _, _ in chosen], molfile)
    parent_fp = _morgan.GetFingerprint(parent) if parent is not None else None
    accepted = [
        Accepted(
            index=i + 1,
            smiles=can,
            rationale=rationale,
            props=compute_properties(can),
            similarity=(
                round(DataStructs.TanimotoSimilarity(parent_fp, _morgan.GetFingerprint(mol)), 3)
                if parent_fp is not None
                else None
            ),
            molblock=Chem.MolToV3KMolBlock(placed[i]),
            changed_atoms=changed[i],
        )
        for i, (mol, can, rationale) in enumerate(chosen)
    ]
    parent_block = Chem.MolToV3KMolBlock(placed[-1]) if parent is not None else None
    return Evaluation(
        ok=True,
        parent_smiles=parent_can,
        parent_molblock=parent_block,
        accepted=accepted,
        rejected=rejected,
        grid_molblock=grid_molblock(placed[: len(chosen)]),
    )


def _layout(
    parent: Chem.Mol | None, mols: list[Chem.Mol], molfile: str | None
) -> tuple[list[Chem.Mol], list[list[int]]]:
    """Coordinates for each molecule (plus the parent last, when there is one).

    With a parent, every molecule is aligned to the parent's drawing and moved by the same offset,
    so the common core sits at the same spot of each grid cell.
    """
    if parent is None:
        return [centred(to_ketcher_scale(m)) for m in mols], [[] for _ in mols]
    template, _, _ = parent_template(parent, molfile)
    template = to_ketcher_scale(template)
    x0, y0, x1, y1 = bbox(template)
    centre = ((x0 + x1) / 2, (y0 + y1) / 2)
    placed, changed = [], []
    for mol in mols:
        # align() returns coordinates at the template's scale (1.0 here)
        aligned, diff = align(mol, template, mcs_timeout=GRID_MCS_TIMEOUT_S)
        shares_core = len(diff) < mol.GetNumAtoms()
        placed.append(centred(aligned, centre) if shares_core else centred(aligned))
        changed.append(diff)
    placed.append(centred(template, centre))
    return placed, changed
