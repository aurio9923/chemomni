"""Structure edits proposed as whole SMILES (PLAN §6, decision Q22).

The edited molecule is laid out on the user's own drawing: the maximum common substructure
with the parent keeps the parent's 2D coordinates, so the picture does not jump around.
When the active tab holds several molecules, only the matching one is replaced.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

from rdkit import Chem
from rdkit.Chem import rdDepictor, rdFMCS

from chemomni.chem.props import canonical_smiles, parse_smiles

MCS_TIMEOUT_S = 2
BOND_CHANGE_MAPPINGS = 48  # equally good atom mappings tried when looking for the tautomer's moved atoms
RDKIT_BOND_LENGTH = 1.5  # what rdDepictor uses for new atoms; Ketcher draws with ~1.0


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


def _mean_bond_length(mol: Chem.Mol) -> float | None:
    if mol.GetNumConformers() == 0 or mol.GetNumBonds() == 0:
        return None
    conf = mol.GetConformer()
    lengths = [
        (conf.GetAtomPosition(b.GetBeginAtomIdx()) - conf.GetAtomPosition(b.GetEndAtomIdx())).Length()
        for b in mol.GetBonds()
    ]
    lengths = [x for x in lengths if x > 1e-6]
    return sum(lengths) / len(lengths) if lengths else None


def _scale(mol: Chem.Mol, factor: float) -> Chem.Mol:
    """Copy of ``mol`` with its 2D coordinates multiplied by ``factor``."""
    mol = Chem.Mol(mol)
    conf = mol.GetConformer()
    for i in range(mol.GetNumAtoms()):
        p = conf.GetAtomPosition(i)
        conf.SetAtomPosition(i, (p.x * factor, p.y * factor, 0.0))
    return mol


@contextmanager
def _rdkit_depictor() -> Iterator[None]:
    """Template matching must use RDKit's own depictor, whatever another module set globally.

    CoordGen (enabled for plain depictions in depict.py) lays out matched structures at its own
    bond length, which mixes scales with the template.
    """
    previous = rdDepictor.GetPreferCoordGen()
    rdDepictor.SetPreferCoordGen(False)
    try:
        yield
    finally:
        rdDepictor.SetPreferCoordGen(previous)


def _moved(a: Chem.Mol, b: Chem.Mol, atom_map: list[tuple[int, int]]) -> set[int]:
    """Atoms of ``b`` whose hydrogens, charge or bond types differ from their partner in ``a``.

    ``atom_map`` pairs atoms of ``a`` with atoms of ``b``. For two tautomers this is where a proton or a
    double bond moved; the heavy-atom skeleton is the same, so an element comparison would see nothing.
    """
    partner = {n: t for t, n in atom_map}
    moved: set[int] = set()
    for t, n in atom_map:
        atom_a, atom_b = a.GetAtomWithIdx(t), b.GetAtomWithIdx(n)
        if (
            atom_a.GetTotalNumHs(includeNeighbors=True) != atom_b.GetTotalNumHs(includeNeighbors=True)
            or atom_a.GetFormalCharge() != atom_b.GetFormalCharge()
        ):
            moved.add(n)
            continue
        for bond in atom_b.GetBonds():
            other = partner.get(bond.GetOtherAtomIdx(n))
            if other is None:
                continue
            twin = a.GetBondBetweenAtoms(t, other)
            if twin is None or twin.GetBondType() != bond.GetBondType():
                moved.add(n)
                break
    return moved


def align(
    new: Chem.Mol,
    template: Chem.Mol,
    mcs_timeout: int = MCS_TIMEOUT_S,
    *,
    any_element: bool = False,
    bond_changes: bool = False,
) -> tuple[Chem.Mol, list[int]]:
    """Lay out ``new`` on ``template``'s coordinates through their MCS. Returns (mol, changed atoms).

    ``any_element`` matches atoms and bonds by topology only, so a ring in which one atom changed
    (phenyl to pyridine) still keeps the template's ring; the atoms whose element differs count as changed.
    ``bond_changes`` is for tautomers: it also matches by topology, and the atoms that count as changed are
    the ones whose hydrogens, charge or bond types differ (of the equally good mappings, the one that
    changes the fewest atoms, so a symmetric group is not marked for nothing).

    The template is first brought to RDKit's bond length so the atoms RDKit adds match the
    constrained ones, and the result is scaled back to the template's (the user's) bond length.
    Without this, drawings from Ketcher (bond length ~1.0) get longer new bonds and bent rings.
    """
    new = Chem.Mol(new)
    any_element = any_element or bond_changes
    factor = RDKIT_BOND_LENGTH / (_mean_bond_length(template) or RDKIT_BOND_LENGTH)
    reference = _scale(template, factor)
    mcs = rdFMCS.FindMCS(
        [reference, new],
        timeout=mcs_timeout,
        ringMatchesRingOnly=True,
        completeRingsOnly=True,
        atomCompare=rdFMCS.AtomCompare.CompareAny if any_element else rdFMCS.AtomCompare.CompareElements,
        bondCompare=rdFMCS.BondCompare.CompareAny if any_element else rdFMCS.BondCompare.CompareOrder,
    )
    matched: set[int] = set()
    if mcs.numAtoms >= 2:
        query = Chem.MolFromSmarts(mcs.smartsString)
        t_match = reference.GetSubstructMatch(query)
        n_match = new.GetSubstructMatch(query)
        if t_match and n_match:
            atom_map = list(zip(t_match, n_match, strict=True))
            moved: set[int] = set()
            if bond_changes:
                tries = new.GetSubstructMatches(query, uniquify=False, maxMatches=BOND_CHANGE_MAPPINGS)
                best = min(
                    (
                        (pairs, _moved(reference, new, pairs))
                        for pairs in (list(zip(t_match, m, strict=True)) for m in tries)
                    ),
                    key=lambda item: len(item[1]),
                    default=(atom_map, set()),
                )
                atom_map, moved = best
            try:
                with _rdkit_depictor():
                    rdDepictor.GenerateDepictionMatching2DStructure(new, reference, atomMap=atom_map)
                matched = {n for t, n in atom_map if _same_element(reference, t, new, n)} - moved
            except ValueError:
                matched = set()
    if not matched:
        with _rdkit_depictor():
            rdDepictor.Compute2DCoords(new)
    changed = [a.GetIdx() for a in new.GetAtoms() if a.GetIdx() not in matched]
    return _scale(new, 1 / factor), changed


def parent_template(parent: Chem.Mol, molfile: str | None) -> tuple[Chem.Mol, int | None, list[Chem.Mol]]:
    """The parent as drawn on the user's tab if it is there, else a fresh layout.

    Returns (template, index of the matching fragment or None, all fragments of the tab).
    """
    parent_can = canonical_smiles(parent)
    fragments = _fragments_from_molfile(molfile)
    match_idx = next((i for i, f in enumerate(fragments) if _safe_canonical(f) == parent_can), None)
    template = fragments[match_idx] if match_idx is not None else _with_coords(parent)
    return template, match_idx, fragments


def _same_element(a: Chem.Mol, i: int, b: Chem.Mol, j: int) -> bool:
    return a.GetAtomWithIdx(i).GetAtomicNum() == b.GetAtomWithIdx(j).GetAtomicNum()


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

    template, match_idx, fragments = parent_template(parent, molfile)
    aligned, changed = align(new, template)

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
