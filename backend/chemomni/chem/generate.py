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

from chemomni.chem.edit import _fragments_from_molfile, align, parent_template
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
    similarity: float | None  # Tanimoto to its parent (Morgan r=2, 2048 bits)
    molblock: str  # this molecule alone, aligned to its parent's drawing, at Ketcher's scale
    changed_atoms: list[int]  # 0-based atoms outside the common substructure with the parent
    parent_smiles: str | None = None  # the structure this one was derived from, when one was named


@dataclass
class Evaluation:
    ok: bool
    errors: list[str] = field(default_factory=list)
    parent_smiles: str | None = None  # only when every candidate comes from the same parent
    parent_molblock: str | None = None
    accepted: list[Accepted] = field(default_factory=list)
    rejected: list[dict[str, str]] = field(default_factory=list)
    grid_molblock: str | None = None


@dataclass
class _Chosen:
    mol: Chem.Mol
    can: str
    rationale: str
    parent: Chem.Mol | None  # named by the caller: similarity, "identical to the parent"
    parent_can: str | None


# A candidate is (SMILES, rationale) or (SMILES, rationale, its own parent SMILES or None).
CandidateSpec = tuple[str, str] | tuple[str, str, str | None]

MIN_LAYOUT_SIMILARITY = 0.3  # a candidate with no named parent follows the most similar structure of the tab


def evaluate(
    parent_smiles: str | None,
    candidates: list[CandidateSpec],
    molfile: str | None = None,
    *,
    any_element: bool = False,
) -> Evaluation:
    """Check the candidates and lay them out on the drawing of their parent.

    ``parent_smiles`` is the parent of every candidate that does not name its own, so a series can start
    from one molecule or each candidate can come from a different compound of the tab. A candidate with
    no parent at all follows the most similar structure drawn on the tab (layout only), and gets a plain
    layout when nothing is similar. ``molfile`` is the active tab.
    """
    default_parent = None
    if parent_smiles:
        default_parent, err = parse_smiles(parent_smiles)
        if default_parent is None:
            return Evaluation(ok=False, errors=[f"parent: {err}"])

    seen: dict[str, int] = {}
    chosen: list[_Chosen] = []
    rejected: list[dict[str, str]] = []
    for item in candidates:
        smiles, rationale = item[0], item[1]
        own_parent = item[2] if len(item) > 2 else None
        mol, err = parse_smiles(smiles)
        if mol is None:
            rejected.append({"smiles": smiles, "reason": err or "invalid"})
            continue
        parent = default_parent
        if own_parent:
            parent, perr = parse_smiles(own_parent)
            if parent is None:
                rejected.append({"smiles": smiles, "reason": f"its parent: {perr}"})
                continue
        parent_can = canonical_smiles(parent) if parent is not None else None
        can = canonical_smiles(mol)
        if can == parent_can:
            rejected.append({"smiles": smiles, "reason": "identical to the parent"})
        elif can in seen:
            rejected.append({"smiles": smiles, "reason": f"duplicate of #{seen[can]}"})
        else:
            seen[can] = len(chosen) + 1
            chosen.append(_Chosen(mol, can, rationale, parent, parent_can))
    if not chosen:
        return Evaluation(ok=False, errors=["no valid new structures"], rejected=rejected)

    placed, changed, parent_placed = _layout(chosen, molfile, any_element)
    accepted = [
        Accepted(
            index=i + 1,
            smiles=c.can,
            rationale=c.rationale,
            props=compute_properties(c.can),
            similarity=(
                round(
                    DataStructs.TanimotoSimilarity(_morgan.GetFingerprint(c.parent), _morgan.GetFingerprint(c.mol)), 3
                )
                if c.parent is not None
                else None
            ),
            molblock=Chem.MolToV3KMolBlock(placed[i]),
            changed_atoms=changed[i],
            parent_smiles=c.parent_can,
        )
        for i, c in enumerate(chosen)
    ]
    parents = {c.parent_can for c in chosen}
    single = next(iter(parents)) if len(parents) == 1 else None
    return Evaluation(
        ok=True,
        parent_smiles=single,
        parent_molblock=Chem.MolToV3KMolBlock(parent_placed) if single and parent_placed is not None else None,
        accepted=accepted,
        rejected=rejected,
        grid_molblock=grid_molblock(placed),
    )


def _closest_on_tab(mol: Chem.Mol, tab_structures: list[Chem.Mol]) -> Chem.Mol | None:
    """The structure on the tab that ``mol`` most resembles (for its layout), if any is similar enough."""
    if not tab_structures:
        return None
    fp = _morgan.GetFingerprint(mol)
    score, best = max(
        ((DataStructs.TanimotoSimilarity(fp, _morgan.GetFingerprint(t)), t) for t in tab_structures),
        key=lambda pair: pair[0],
    )
    return best if score >= MIN_LAYOUT_SIMILARITY else None


def _layout(
    chosen: list[_Chosen], molfile: str | None, any_element: bool = False
) -> tuple[list[Chem.Mol], list[list[int]], Chem.Mol | None]:
    """Coordinates for every candidate, plus the common parent's drawing when there is exactly one.

    Candidates that share a parent are aligned to the parent's drawing and moved by the same offset, so
    the common core sits at the same spot of each grid cell. Different parents keep their own drawings:
    each candidate follows the orientation its parent has on the tab, in its own cell.
    """
    tab_structures = _fragments_from_molfile(molfile) if any(c.parent is None for c in chosen) else []
    templates: dict[str, tuple[Chem.Mol, tuple[float, float]]] = {}

    def template_for(parent: Chem.Mol) -> tuple[Chem.Mol, tuple[float, float]]:
        key = canonical_smiles(parent)
        if key not in templates:
            template, _, _ = parent_template(parent, molfile)
            template = to_ketcher_scale(template)
            x0, y0, x1, y1 = bbox(template)
            templates[key] = (template, ((x0 + x1) / 2, (y0 + y1) / 2))
        return templates[key]

    placed: list[Chem.Mol] = []
    changed: list[list[int]] = []
    for c in chosen:
        guide = c.parent if c.parent is not None else _closest_on_tab(c.mol, tab_structures)
        if guide is None:
            placed.append(centred(to_ketcher_scale(c.mol)))
            changed.append([])
            continue
        template, centre = template_for(guide)
        # align() returns coordinates at the template's scale (1.0 here)
        aligned, diff = align(c.mol, template, mcs_timeout=GRID_MCS_TIMEOUT_S, any_element=any_element)
        shares_core = len(diff) < c.mol.GetNumAtoms()
        placed.append(centred(aligned, centre) if shares_core else centred(aligned))
        changed.append(diff)
    parents = {c.parent_can for c in chosen}
    only = chosen[0].parent if len(parents) == 1 else None
    parent_placed = None
    if only is not None:
        template, centre = template_for(only)
        parent_placed = centred(template, centre)
    return placed, changed, parent_placed
