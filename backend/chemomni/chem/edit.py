"""Structure edits proposed as whole SMILES (PLAN §6, decision Q22).

The edited molecule is laid out on the user's own drawing: the maximum common substructure
with the parent keeps the parent's 2D coordinates, so the picture does not jump around.
When the active tab holds several molecules, only the matching one is replaced.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from rdkit import Chem
from rdkit.Chem import rdDepictor, rdFMCS

from chemomni.chem.props import canonical_smiles, parse_smiles

MCS_TIMEOUT_S = 2


@dataclass
class EditResult:
    ok: bool
    errors: list[str] = field(default_factory=list)
    parent_smiles: str | None = None
    new_smiles: str | None = None
    parent_molblock: str | None = None  # the parent as laid out on the user's tab (or computed)
    molblock: str | None = None  # edited molecule only, aligned to the parent
    document_molblock: str | None = None  # whole tab with the parent replaced (replace_active)
    changed_atoms: list[int] = field(default_factory=list)  # 0-based atoms of the new molecule outside the MCS


def _fragments_from_molfile(molfile: str | None) -> list[Chem.Mol]:
    if not molfile:
        return []
    mol = Chem.MolFromMolBlock(molfile, sanitize=False, removeHs=False)
    if mol is None:
        return []
    try:
        Chem.SanitizeMol(mol)
        mol = Chem.RemoveHs(mol)
    except Exception:
        return []
    return list(Chem.GetMolFrags(mol, asMols=True, sanitizeFrags=True))


def _with_coords(mol: Chem.Mol) -> Chem.Mol:
    mol = Chem.Mol(mol)
    if mol.GetNumConformers() == 0:
        rdDepictor.SetPreferCoordGen(True)
        rdDepictor.Compute2DCoords(mol)
    return mol


def _align(new: Chem.Mol, template: Chem.Mol) -> tuple[Chem.Mol, list[int]]:
    """Lay out ``new`` on ``template``'s coordinates through their MCS. Returns (mol, changed atoms)."""
    new = Chem.Mol(new)
    mcs = rdFMCS.FindMCS(
        [template, new],
        timeout=MCS_TIMEOUT_S,
        ringMatchesRingOnly=True,
        completeRingsOnly=True,
        atomCompare=rdFMCS.AtomCompare.CompareElements,
        bondCompare=rdFMCS.BondCompare.CompareOrder,
    )
    matched: set[int] = set()
    if mcs.numAtoms >= 2:
        query = Chem.MolFromSmarts(mcs.smartsString)
        t_match = template.GetSubstructMatch(query)
        n_match = new.GetSubstructMatch(query)
        if t_match and n_match:
            atom_map = list(zip(t_match, n_match, strict=True))
            try:
                rdDepictor.GenerateDepictionMatching2DStructure(new, template, atomMap=atom_map)
                matched = set(n_match)
            except ValueError:
                matched = set()
    if not matched:
        rdDepictor.Compute2DCoords(new)
    changed = [a.GetIdx() for a in new.GetAtoms() if a.GetIdx() not in matched]
    return new, changed


def edit(parent_smiles: str, new_smiles: str, molfile: str | None = None) -> EditResult:
    parent, err = parse_smiles(parent_smiles)
    if parent is None:
        return EditResult(ok=False, errors=[f"parent: {err}"])
    new, err = parse_smiles(new_smiles)
    if new is None:
        return EditResult(ok=False, errors=[f"new structure: {err}"])
    parent_can, new_can = canonical_smiles(parent), canonical_smiles(new)
    if parent_can == new_can:
        return EditResult(ok=False, errors=["the new structure is identical to the parent"])

    fragments = _fragments_from_molfile(molfile)
    match_idx = next(
        (i for i, f in enumerate(fragments) if _safe_canonical(f) == parent_can),
        None,
    )
    template = fragments[match_idx] if match_idx is not None else _with_coords(parent)
    aligned, changed = _align(new, template)

    document = None
    if match_idx is not None:
        others = [f for i, f in enumerate(fragments) if i != match_idx]
        combined = aligned
        for frag in others:
            combined = Chem.CombineMols(combined, frag)
        document = Chem.MolToV3KMolBlock(combined)

    return EditResult(
        ok=True,
        parent_smiles=parent_can,
        new_smiles=new_can,
        parent_molblock=Chem.MolToV3KMolBlock(template),
        molblock=Chem.MolToV3KMolBlock(aligned),
        document_molblock=document,
        changed_atoms=changed,
    )


def _safe_canonical(mol: Chem.Mol) -> str | None:
    try:
        m = Chem.Mol(mol)
        Chem.SanitizeMol(m)
        return Chem.MolToSmiles(m)
    except Exception:
        return None


def selection_info(molfile: str | None, atoms_1based: list[int]) -> dict[str, object] | None:
    """Describe the user's selection: elements and the selected fragment as SMILES."""
    if not molfile or not atoms_1based:
        return None
    mol = Chem.MolFromMolBlock(molfile, sanitize=False, removeHs=False)
    if mol is None:
        return None
    idx = [a - 1 for a in atoms_1based if 0 < a <= mol.GetNumAtoms()]
    if not idx:
        return None
    try:
        Chem.SanitizeMol(mol)
        fragment = Chem.MolFragmentToSmiles(mol, atomsToUse=idx, canonical=False)
    except Exception:
        fragment = None
    return {
        "atoms": [i + 1 for i in idx],
        "elements": [mol.GetAtomWithIdx(i).GetSymbol() for i in idx],
        "fragment_smiles": fragment,
    }
