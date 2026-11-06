"""Single-point changes on a part of a molecule, enumerated exhaustively (decision Q53).

The user selects a substructure (a phenyl, say) and asks for "pyridine" or "monofluoro". Which
positions exist, and which of them are the same by symmetry, is chemistry the LLM should not
guess: this module lists every position on the selected atoms, builds the variant for each, and
merges symmetry-equivalent results.

Two operations:
- ``substitute``: replace one hydrogen of a selected atom by a group (F, Cl, C, OC, C#N, ...)
- ``replace_atom``: change the element of a selected atom (C -> N turns phenyl into pyridine)

Several changes may be combined ("one fluorine and one methyl", "two fluorines"): every way of giving
each change its own atom is tried, so no combination is left to the model's memory.
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from typing import Literal

from rdkit import Chem

from chemomni.chem.props import canonical_smiles, parse_smiles

MAX_VARIANTS = 20
MAX_COMBINATIONS = 5000  # placements tried before building any molecule
MAX_GROUP_HEAVY_ATOMS = 12
ORIG = "_orig"  # property: the atom's 1-based number in the user's molfile
RING_POSITIONS = {1: "ortho", 2: "meta", 3: "para"}

Operation = Literal["substitute", "replace_atom"]

# short names for the notes shown to the user
GROUP_NAMES = {
    "C": "Me",
    "CC": "Et",
    "O": "OH",
    "N": "NH2",
    "OC": "OMe",
    "C#N": "CN",
    "C(F)(F)F": "CF3",
    "C(=O)O": "COOH",
    "[N+](=O)[O-]": "NO2",
}


@dataclass(frozen=True)
class Change:
    operation: Operation
    group: str | None = None
    element: str | None = None

    @property
    def what(self) -> str:
        if self.operation == "substitute":
            return GROUP_NAMES.get(self.group or "", self.group or "?")
        return self.element or "?"


@dataclass
class _Plan:
    change: Change
    group: Chem.Mol | None
    atomic_number: int
    sites: list[int]


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
    tried: int = 0  # placements built
    merged: int = 0  # placements folded into an equivalent one


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


def _plan(target: Target, change: Change) -> tuple[_Plan | None, str | None]:
    parent = target.mol
    if change.operation == "substitute":
        group, err = parse_group(change.group or "")
        if group is None:
            return None, err or "group is required"
        sites = [i for i in target.atoms if parent.GetAtomWithIdx(i).GetTotalNumHs() > 0]
        if not sites:
            return None, "no selected atom carries a hydrogen to replace"
        return _Plan(change, group, 0, sites), None
    try:
        number = Chem.GetPeriodicTable().GetAtomicNumber(change.element or "")
    except Exception:
        return None, f"unknown element: {change.element}"
    sites = [i for i in target.atoms if _can_become(parent.GetAtomWithIdx(i), number)]
    if not sites:
        return (
            None,
            f"no selected atom can become {change.element} (already {change.element}, or a substituted ring atom)",
        )
    return _Plan(change, None, number, sites), None


def _apply(parent: Chem.Mol, picks: Sequence[tuple[_Plan, int]]) -> Chem.Mol:
    """``parent`` with every change applied at its own atom. Atom indices of ``parent`` stay valid."""
    rw = Chem.RWMol(parent)
    rw.RemoveAllConformers()  # the layout is computed later
    for plan, idx in picks:
        if plan.group is not None:
            hydrogens = parent.GetAtomWithIdx(idx).GetTotalNumHs()
            offset = rw.GetNumAtoms()
            rw.InsertMol(plan.group)
            rw.AddBond(idx, offset, Chem.BondType.SINGLE)
            atom = rw.GetAtomWithIdx(idx)
            atom.SetNoImplicit(True)  # say exactly how many hydrogens are left
            atom.SetNumExplicitHs(hydrogens - 1)
        else:
            atom = rw.GetAtomWithIdx(idx)
            atom.SetAtomicNum(plan.atomic_number)
            atom.SetFormalCharge(0)
            atom.SetNoImplicit(False)  # hydrogens follow from the new element's valence
            atom.SetNumExplicitHs(0)
        atom.SetChiralTag(Chem.ChiralType.CHI_UNSPECIFIED)
    Chem.SanitizeMol(rw)
    return rw


def _placements(plans: list[_Plan]) -> Iterator[tuple[int, ...]]:
    """Every way to give each change its own atom (identical changes are not counted twice)."""
    for combo in itertools.product(*(p.sites for p in plans)):
        if len(set(combo)) != len(combo):
            continue
        if any(
            plans[i].change == plans[j].change and combo[i] > combo[j]
            for i in range(len(plans))
            for j in range(i + 1, len(plans))
        ):
            continue
        yield combo


def _note(target: Target, plans: list[_Plan], labels: dict[int, str], combos: list[tuple[int, ...]]) -> str:
    def number(idx: int) -> int:
        return target.numbers.get(idx, idx + 1)

    if len(plans) == 1:  # equivalent positions are listed together: "ortho (atoms 6, 10)"
        where = _where(labels.get(combos[0][0]), [number(c[0]) for c in combos])
        return f"{plans[0].change.what} at {where}"
    parts = [
        f"{plan.change.what} at {_where(labels.get(idx), [number(idx)])}"
        for plan, idx in zip(plans, combos[0], strict=True)
    ]
    return ", ".join(parts) + (f" (+{len(combos) - 1} equivalent)" if len(combos) > 1 else "")


def scan(target: Target, changes: Sequence[Change]) -> ScanResult:
    parent = target.mol
    selected = Chem.MolFragmentToSmiles(parent, atomsToUse=target.atoms, canonical=True)
    result = ScanResult(ok=False, parent_smiles=canonical_smiles(parent), selected_smiles=selected)
    if not changes:
        result.errors = ["no change given"]
        return result

    plans: list[_Plan] = []
    for change in changes:
        plan, err = _plan(target, change)
        if plan is None:
            result.errors = [err or "invalid change"]
            return result
        plans.append(plan)
    size = math.prod(len(p.sites) for p in plans)
    if size > MAX_COMBINATIONS:
        result.errors = [f"{size} placements to try; select fewer atoms (at most {MAX_COMBINATIONS})"]
        return result
    placements = list(_placements(plans))
    if not placements:
        usable = len({i for p in plans for i in p.sites})
        result.errors = [f"{len(plans)} changes need {len(plans)} different atoms, but only {usable} can take one"]
        return result

    labels = position_labels(target)
    by_structure: dict[str, list[tuple[int, ...]]] = {}
    for combo in placements:
        try:
            smiles = canonical_smiles(_apply(parent, list(zip(plans, combo, strict=True))))
        except Exception as exc:
            reason = _first_line(exc)
            if "kekulize" in reason:
                reason = "would break the aromatic ring"
            at = "; ".join(
                f"{p.change.what} at atom {target.numbers.get(i, i + 1)}" for p, i in zip(plans, combo, strict=True)
            )
            result.skipped.append({"smiles": at, "reason": reason})
            continue
        by_structure.setdefault(smiles, []).append(combo)

    result.tried = len(placements) - len(result.skipped)
    for smiles, combos in by_structure.items():
        result.merged += len(combos) - 1
        result.candidates.append((smiles, _note(target, plans, labels, combos)))
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
