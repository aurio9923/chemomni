"""2D depiction (PLAN §7). The only place that draws molecules on the server (decision Q47)."""

from __future__ import annotations

from functools import lru_cache

from rdkit import Chem
from rdkit.Chem import rdDepictor
from rdkit.Chem.Draw import rdMolDraw2D

from chemomni.chem.props import parse_smiles


def _prepare(smiles: str) -> Chem.Mol | None:
    mol, _ = parse_smiles(smiles)
    if mol is None:
        return None
    rdDepictor.SetPreferCoordGen(True)
    rdDepictor.Compute2DCoords(mol)
    return mol


@lru_cache(maxsize=2048)
def depict(smiles: str, width: int = 280, height: int = 200, highlight: tuple[int, ...] = ()) -> str | None:
    """SVG for one SMILES (transparent background), or None if it does not parse."""
    mol = _prepare(smiles)
    if mol is None:
        return None
    drawer = rdMolDraw2D.MolDraw2DSVG(width, height)
    opts = drawer.drawOptions()
    opts.clearBackground = False
    opts.padding = 0.08
    opts.bondLineWidth = 1.5
    opts.addStereoAnnotation = True
    rdMolDraw2D.PrepareAndDrawMolecule(drawer, mol, highlightAtoms=list(highlight) or None)
    drawer.FinishDrawing()
    return drawer.GetDrawingText()


@lru_cache(maxsize=2048)
def molblock(smiles: str) -> str | None:
    """V3000 molfile with 2D coordinates, used to open a structure in a new tab."""
    mol = _prepare(smiles)
    return None if mol is None else Chem.MolToV3KMolBlock(mol)
