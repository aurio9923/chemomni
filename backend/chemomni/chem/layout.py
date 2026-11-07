"""Putting several molecules on one tab, and taking them apart again (PLAN §6, tools 4-5).

Coordinates use Ketcher's scale (mean bond length 1.0) so AI tabs look like the user's own drawings.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from rdkit import Chem
from rdkit.Chem import rdDepictor

from chemomni.chem.edit import _mean_bond_length, _scale

KETCHER_BOND_LENGTH = 1.0
CELL_PADDING = 1.5  # space between grid cells, in bond lengths
MAX_COLUMNS = 4

BBox = tuple[float, float, float, float]  # min x, min y, max x, max y


def with_coords(mol: Chem.Mol) -> Chem.Mol:
    mol = Chem.Mol(mol)
    if mol.GetNumConformers() == 0:
        rdDepictor.Compute2DCoords(mol)
    return mol


def bbox(mol: Chem.Mol) -> BBox:
    conf = mol.GetConformer()
    xs = [conf.GetAtomPosition(i).x for i in range(mol.GetNumAtoms())]
    ys = [conf.GetAtomPosition(i).y for i in range(mol.GetNumAtoms())]
    return min(xs), min(ys), max(xs), max(ys)


def translate(mol: Chem.Mol, dx: float, dy: float) -> Chem.Mol:
    mol = Chem.Mol(mol)
    conf = mol.GetConformer()
    for i in range(mol.GetNumAtoms()):
        p = conf.GetAtomPosition(i)
        conf.SetAtomPosition(i, (p.x + dx, p.y + dy, 0.0))
    return mol


def to_ketcher_scale(mol: Chem.Mol) -> Chem.Mol:
    """Copy with 2D coordinates (computed if missing) at Ketcher's bond length."""
    mol = with_coords(mol)
    length = _mean_bond_length(mol)
    return _scale(mol, KETCHER_BOND_LENGTH / length) if length else mol


def centred(mol: Chem.Mol, centre: tuple[float, float] | None = None) -> Chem.Mol:
    """Move ``centre`` (default: the molecule's own bounding-box centre) to the origin."""
    if centre is None:
        x0, y0, x1, y1 = bbox(mol)
        centre = ((x0 + x1) / 2, (y0 + y1) / 2)
    return translate(mol, -centre[0], -centre[1])


def grid(mols: Sequence[Chem.Mol], columns: int | None = None) -> Chem.Mol:
    """Combine molecules into one, laid out row by row (left to right, top to bottom).

    Each molecule keeps its coordinates relative to the origin, so molecules aligned to a common
    parent keep their shared core at the same spot of every cell. Cells are as large as the union
    of all bounding boxes.
    """
    if not mols:
        raise ValueError("no molecules to lay out")
    n = len(mols)
    columns = columns or min(MAX_COLUMNS, math.ceil(math.sqrt(n)))
    boxes = [bbox(m) for m in mols]
    min_x, min_y = min(b[0] for b in boxes), min(b[1] for b in boxes)
    max_x, max_y = max(b[2] for b in boxes), max(b[3] for b in boxes)
    cell_w = max(max_x - min_x, KETCHER_BOND_LENGTH) + CELL_PADDING
    cell_h = max(max_y - min_y, KETCHER_BOND_LENGTH) + CELL_PADDING
    combined: Chem.Mol | None = None
    for i, mol in enumerate(mols):
        row, col = divmod(i, columns)
        # molfile y points up: the first row is at the top
        placed = translate(mol, col * cell_w - min_x, -row * cell_h - max_y)
        combined = placed if combined is None else Chem.CombineMols(combined, placed)
    assert combined is not None
    return combined


def grid_molblock(mols: Sequence[Chem.Mol], columns: int | None = None) -> str:
    return Chem.MolToV3KMolBlock(grid(mols, columns))


def layout_smiles(mols: Sequence[Chem.Mol]) -> str:
    """Independent molecules (no common parent) on a grid, each centred in its cell."""
    return grid_molblock([centred(to_ketcher_scale(m)) for m in mols])


def split_molfile(molfile: str) -> list[tuple[str, str | None]]:
    """One (V3000 molfile, SMILES or None) per connected component, each moved to the origin.

    Molecules are not sanitized, so whatever the user drew (explicit hydrogens, odd valences)
    survives the round trip.
    """
    mol = Chem.MolFromMolBlock(molfile, sanitize=False, removeHs=False)
    if mol is None or mol.GetNumAtoms() == 0:
        return []
    parts: list[tuple[str, str | None]] = []
    for frag in Chem.GetMolFrags(mol, asMols=True, sanitizeFrags=False):
        frag = centred(frag) if frag.GetNumConformers() else with_coords(frag)
        parts.append((Chem.MolToV3KMolBlock(frag, kekulize=False), smiles_or_none(frag)))
    return parts


def smiles_or_none(mol: Chem.Mol) -> str | None:
    try:
        m = Chem.Mol(mol)
        Chem.SanitizeMol(m)
        return Chem.MolToSmiles(Chem.RemoveHs(m))
    except Exception:
        return None
