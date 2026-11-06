"""Single-point changes on a part of a molecule, enumerated exhaustively (decision Q53).

The user selects a substructure (a phenyl, say) and asks for "pyridine" or "monofluoro". Which
positions exist, and which of them are the same by symmetry, is chemistry the LLM should not
guess: this module lists every position on the selected atoms, builds the variant for each, and
merges symmetry-equivalent results.

Two operations:
- ``substitute``: replace one hydrogen of a selected atom by a group (F, Cl, C, OC, C#N, ...)
- ``replace_atom``: change the element of a selected atom (C -> N turns phenyl into pyridine)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from rdkit import Chem

from chemomni.chem.props import canonical_smiles, parse_smiles

MAX_VARIANTS = 20
MAX_GROUP_HEAVY_ATOMS = 12
ORIG = "_orig"  # property: the atom's 1-based number in the user's molfile
RING_POSITIONS = {1: "ortho", 2: "meta", 3: "para"}

Operation = Literal["substitute", "replace_atom"]


@dataclass
class Target:
    """A molecule and the atoms (indices into it) a scan may change."""

    mol: Chem.Mol
    atoms: list[int]
    numbers: dict[int, int] = field(default_factory=dict)  # atom index -> number to show the user


@dataclass
class ScanResult:
    ok: bool
    errors: list[str] = field(default_factory=list)
    parent_smiles: str | None = None
    selected_smiles: str | None = None  # the selected part on its own, for the answer
    candidates: list[tuple[str, str]] = field(default_factory=list)  # (SMILES, rationale)
    skipped: list[dict[str, str]] = field(default_factory=list)
    merged: int = 0  # positions folded into an equivalent one


# ---------------------------------------------------------------- finding the atoms


def target_from_selection(molfile: str, atoms_1based: list[int]) -> tuple[Target | None, str | None]:
    """The molecule holding the user's selected atoms (numbers are molfile atom numbers)."""
    mol = Chem.MolFromMolBlock(molfile, sanitize=False, removeHs=False)
    if mol is None:
        return None, "the editor content could not be read"
    for atom in mol.GetAtoms():
        atom.SetIntProp(ORIG, atom.GetIdx() + 1)
    try:
        Chem.SanitizeMol(mol)
        mol = Chem.RemoveHs(mol)
    except Exception as exc:
        return None, f"the drawing is not a valid structure: {_first_line(exc)}"
    wanted = set(atoms_1based)
    holders: list[Chem.Mol] = []
    for frag in Chem.GetMolFrags(mol, asMols=True, sanitizeFrags=True):
        if any(a.GetIntProp(ORIG) in wanted for a in frag.GetAtoms()):
            holders.append(frag)
    if not holders:
        return None, "the selection holds only hydrogens or atoms that do not exist"
    if len(holders) > 1:
        return None, "the selection spans several molecules; select atoms of one molecule"
    frag = holders[0]
    atoms = [a.GetIdx() for a in frag.GetAtoms() if a.GetIntProp(ORIG) in wanted]
    return Target(frag, atoms, {a.GetIdx(): a.GetIntProp(ORIG) for a in frag.GetAtoms()}), None


def target_from_smarts(parent_smiles: str, smarts: str) -> tuple[Target | None, str | None]:
    parent, err = parse_smiles(parent_smiles)
    if parent is None:
        return None, f"parent: {err}"
    query = Chem.MolFromSmarts(smarts)
    if query is None:
        return None, f"invalid SMARTS: {smarts}"
    matches = parent.GetSubstructMatches(query)
    if not matches:
        return None, f"the pattern {smarts} does not occur in the molecule"
    atoms = sorted({i for m in matches for i in m})
    return Target(parent, atoms, {i: i + 1 for i in range(parent.GetNumAtoms())}), None


# ---------------------------------------------------------------- naming positions


def position_labels(target: Target) -> dict[int, str]:
    """ortho / meta / para for the atoms of one six-membered ring with a single attachment point."""
    mol, selected = target.mol, set(target.atoms)
    rings = [r for r in mol.GetRingInfo().AtomRings() if len(r) == 6 and set(r) <= selected]
    if len(rings) != 1:
        return {}
    ring = rings[0]
    attached = [
        i for i, a in enumerate(ring) if any(n.GetIdx() not in ring for n in mol.GetAtomWithIdx(a).GetNeighbors())
    ]
    if len(attached) != 1:
        return {}
    labels = {}
    for pos, idx in enumerate(ring):
        distance = min(abs(pos - attached[0]), 6 - abs(pos - attached[0]))
        if distance in RING_POSITIONS:
            labels[idx] = RING_POSITIONS[distance]
    return labels


def _where(ring_label: str | None, numbers: list[int]) -> str:
    """ "para (atom 8)", "ortho (atoms 6, 10)" or "atom 8"; several numbers mean equivalent positions."""
    listed = ", ".join(str(n) for n in numbers)
    noun = "atoms" if len(numbers) > 1 else "atom"
    return f"{ring_label} ({noun} {listed})" if ring_label else f"{noun} {listed}"


# ---------------------------------------------------------------- building variants


def parse_group(group: str) -> tuple[Chem.Mol | None, str | None]:
    """A substituent written as SMILES whose first atom attaches to the molecule."""
    if "." in group:
        return None, "a group must be one connected fragment"
    mol, err = parse_smiles(group)
    if mol is None:
        return None, f"group: {err}"
    if mol.GetNumHeavyAtoms() > MAX_GROUP_HEAVY_ATOMS:
        return None, f"group has more than {MAX_GROUP_HEAVY_ATOMS} heavy atoms"
    return mol, None


def _substituted(parent: Chem.Mol, idx: int, group: Chem.Mol) -> Chem.Mol:
    rw = Chem.RWMol(parent)
    rw.RemoveAllConformers()  # the layout is computed later from the SMILES
    atom = rw.GetAtomWithIdx(idx)
    hydrogens = atom.GetTotalNumHs()
    offset = rw.GetNumAtoms()
    rw.InsertMol(group)
    rw.AddBond(idx, offset, Chem.BondType.SINGLE)
    atom = rw.GetAtomWithIdx(idx)
    atom.SetNoImplicit(True)  # say exactly how many hydrogens are left
    atom.SetNumExplicitHs(hydrogens - 1)
    atom.SetChiralTag(Chem.ChiralType.CHI_UNSPECIFIED)
    Chem.SanitizeMol(rw)
    return rw


def _replaced(parent: Chem.Mol, idx: int, atomic_number: int) -> Chem.Mol:
    rw = Chem.RWMol(parent)
    rw.RemoveAllConformers()
    atom = rw.GetAtomWithIdx(idx)
    atom.SetAtomicNum(atomic_number)
    atom.SetFormalCharge(0)
    atom.SetNoImplicit(False)  # hydrogens follow from the new element's valence
    atom.SetNumExplicitHs(0)
    atom.SetChiralTag(Chem.ChiralType.CHI_UNSPECIFIED)
    Chem.SanitizeMol(rw)
    return rw


def scan(
    target: Target,
    operation: Operation,
    *,
    group: str | None = None,
    element: str | None = None,
) -> ScanResult:
    parent = target.mol
    parent_can = canonical_smiles(parent)
    selected = Chem.MolFragmentToSmiles(parent, atomsToUse=target.atoms, canonical=True)
    result = ScanResult(ok=False, parent_smiles=parent_can, selected_smiles=selected)

    group_mol: Chem.Mol | None = None
    atomic_number = 0
    if operation == "substitute":
        group_mol, err = parse_group(group or "")
        if group_mol is None:
            result.errors = [err or "group is required"]
            return result
        sites = [i for i in target.atoms if parent.GetAtomWithIdx(i).GetTotalNumHs() > 0]
        what = group
    else:
        try:
            atomic_number = Chem.GetPeriodicTable().GetAtomicNumber(element or "")
        except Exception:
            result.errors = [f"unknown element: {element}"]
            return result
        sites = [i for i in target.atoms if _can_become(parent.GetAtomWithIdx(i), atomic_number)]
        what = element
    if not sites:
        result.errors = [
            "no selected atom carries a hydrogen to replace"
            if operation == "substitute"
            else f"no selected atom can become {element} (already {element}, or a substituted ring atom)"
        ]
        return result

    labels = position_labels(target)
    by_structure: dict[str, list[int]] = {}
    for idx in sites:
        number = target.numbers.get(idx, idx + 1)
        try:
            built = (
                _substituted(parent, idx, group_mol) if group_mol is not None else _replaced(parent, idx, atomic_number)
            )
            smiles = canonical_smiles(built)
        except Exception as exc:
            reason = _first_line(exc)
            if "kekulize" in reason:
                reason = "would break the aromatic ring"
            result.skipped.append({"smiles": f"{what} at atom {number}", "reason": reason})
            continue
        by_structure.setdefault(smiles, []).append(idx)

    for smiles, indices in by_structure.items():
        result.merged += len(indices) - 1
        numbers = [target.numbers.get(i, i + 1) for i in indices]
        ring_label = labels.get(indices[0])
        result.candidates.append((smiles, f"{what} at {_where(ring_label, numbers)}"))
    if not result.candidates:
        result.errors = ["no valid variant: " + "; ".join(s["reason"] for s in result.skipped[:3])]
        return result
    if len(result.candidates) > MAX_VARIANTS:
        n = len(result.candidates)
        result.errors = [f"{n} distinct variants; select fewer atoms or a smaller pattern (at most {MAX_VARIANTS})"]
        result.candidates = []
        return result
    result.ok = True
    return result


def _can_become(atom: Chem.Atom, atomic_number: int) -> bool:
    """A substituted aromatic atom (3 connections) has no room to turn into N, O or S."""
    if atom.GetAtomicNum() == atomic_number:
        return False
    return not (atom.GetIsAromatic() and atom.GetDegree() >= 3)


def _first_line(exc: Exception) -> str:
    text = str(exc).strip().splitlines()
    return text[0] if text else type(exc).__name__
