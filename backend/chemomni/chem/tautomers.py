"""Tautomers of one molecule and its canonical tautomer (decision Q63).

RDKit's ``TautomerEnumerator`` does the work. Three settings and one limit matter here, each learned from
a run on real molecules (see ``tests/test_tautomers.py``):

- ``RemoveSp3Stereo`` and ``RemoveBondStereo`` are switched off. With RDKit's defaults the canonical form
  of (S)-ibuprofen, L-alanine or (R)-2-methylcyclohexanone has lost its stereocentre. Kept stereo is only
  lost where the centre really becomes sp2 in a tautomer.
- The charge state is not touched: a carboxylate stays a carboxylate (tautomers, not protomers). A salt
  is refused rather than guessed at.
- The enumeration is bounded (RDKit's own limit on transformations, plus a time limit), because a few
  molecules have hundreds of tautomers (a pentapeptide has over two hundred). Hitting either limit is
  reported as ``complete = False``: the list and the canonical form are then partial.

The canonical tautomer is RDKit's consistent choice by a scoring function, so the same compound drawn as
different tautomers gets one label. It is not a prediction of the form that predominates in water or in
a binding site (barbituric acid's canonical form is an aromatic enol, but it exists as the triketone).

RDKit lists every form its hydrogen-shift rules can reach, whatever its energy. Some of them no chemist
would draw, and they are marked as unfavoured (decision Q64) so that the tool can leave them out:

- a ring that is aromatic in the drawn form and is not in the tautomer (phenol as cyclohexadienone,
  2-pyridone with a CH2 in the ring). The drawn form is the reference, ring by ring: barbituric acid drawn
  as the triketone loses nothing, so its forms stay.
- the carbonyl of an amide, lactam, urea, carbamate, imide, acid or ester written as C-OH (or C-SH) with
  the double bond moved to the nitrogen (imidic acid, lactim) or to the alpha carbon (enol of an acid
  derivative), and a hydroxamic acid written as C(OH)-N=O. Inside an aromatic ring the same change is a
  hydroxy-heteroarene (2-hydroxypyridine) and stays; so does an enol that is conjugated with another
  carbonyl (the other enol of a beta-keto amide).

A group counts only when the tautomer has more of it than the drawn form: what the user drew is never the
reason to hide something. Enols of ketones and aldehydes, amino/imino forms and the N-H forms of azoles
are not touched.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from rdkit import Chem
from rdkit.Chem.MolStandardize import rdMolStandardize

from chemomni.chem.props import canonical_smiles

MAX_TRANSFORMS = 1000  # RDKit's default, kept explicit
TIME_LIMIT_S = 5.0  # of the 30 s a tool may take

LOST_AROMATIC_RING = "lost_aromatic_ring"
# Groups that mark an unfavoured tautomer when it has more of them than the drawn form. The carbon is never
# aromatic (`!a`): in an aromatic ring the same atoms are a hydroxy-heteroarene, which is a real tautomer.
UNFAVOURED_GROUPS: dict[str, str] = {
    # amide / lactam / urea / carbamate / imide / hydroxamic acid / thioamide as C(OH)=N or C(SH)=N
    "imidic_acid": "[CX3;!a]([OX2H1,SX2H1])=[NX2]",
    # amide / acid / ester as its enol C=C(OH)-N/O/S; kept when the enol is conjugated with a carbonyl
    "acid_enol": "[CX3;!a]([OX2H1,SX2H1])([#7,#8,#16])=[CX3;!$([#6]-[#6]=[O,S])]",
    # hydroxamic acid as C(OH)-N=O: with the imidic form hidden it would be the only form left of one
    "nitroso_carbinol": "[CX4]([OX2H1])[NX2]=[OX1]",
}
UNFAVOURED_WHY: dict[str, str] = {
    LOST_AROMATIC_RING: "a ring that is aromatic in the drawn form is not aromatic",
    "imidic_acid": "an amide, lactam, urea or imide written as C(OH)=N (imidic acid, lactim)",
    "acid_enol": "an amide, acid or ester written as its enol C=C(OH)-N/O",
    "nitroso_carbinol": "a hydroxamic acid written as C(OH)-N=O",
}
_GROUP_QUERIES = {name: Chem.MolFromSmarts(smarts) for name, smarts in UNFAVOURED_GROUPS.items()}


@dataclass
class TautomerSet:
    parent: str  # canonical SMILES of the molecule as given
    canonical: str  # canonical tautomer, which may be the molecule as given
    others: list[str] = field(default_factory=list)  # every other tautomer: the canonical one first, then best score
    total: int = 1  # distinct tautomers found, the given one included
    complete: bool = True  # False when a limit stopped the enumeration
    unfavoured: dict[str, tuple[str, ...]] = field(default_factory=dict)  # SMILES in `others` -> why (Q64)

    @property
    def is_canonical(self) -> bool:
        return self.canonical == self.parent

    @property
    def favoured(self) -> list[str]:
        """`others` without the unfavoured forms, in the same order."""
        return [s for s in self.others if s not in self.unfavoured]

    def hidden_by_reason(self) -> dict[str, int]:
        """How many unfavoured forms each reason accounts for (a form with two reasons counts under both)."""
        counts: dict[str, int] = {}
        for reasons in self.unfavoured.values():
            for reason in reasons:
                counts[reason] = counts.get(reason, 0) + 1
        return counts


class _Deadline(rdMolStandardize.TautomerEnumeratorCallback):
    """Stops the enumeration when the time is up (RDKit then reports ``Canceled``)."""

    def __init__(self, seconds: float) -> None:
        super().__init__()
        self._end = time.monotonic() + seconds

    def __call__(self, mol: Chem.Mol, result: object) -> bool:
        return time.monotonic() < self._end


def enumerator(max_transforms: int = MAX_TRANSFORMS) -> rdMolStandardize.TautomerEnumerator:
    """An enumerator with this project's settings (stereo kept)."""
    en = rdMolStandardize.TautomerEnumerator()
    en.SetRemoveSp3Stereo(False)
    en.SetRemoveBondStereo(False)
    en.SetMaxTransforms(max_transforms)
    return en


def check_one_molecule(mol: Chem.Mol) -> str | None:
    """Why the molecule cannot be used, or None."""
    if len(Chem.GetMolFrags(mol)) > 1:
        return "it has several components (a salt or a mixture); give the one molecule you mean"
    return None


def _aromatic_rings(mol: Chem.Mol) -> list[list[tuple[int, int]]]:
    """Each aromatic ring as its bonds, written as atom pairs (a ring is aromatic when all its bonds are)."""
    rings = []
    for ring in mol.GetRingInfo().AtomRings():
        pairs = [(a, ring[(i + 1) % len(ring)]) for i, a in enumerate(ring)]
        if all(mol.GetBondBetweenAtoms(a, b).GetIsAromatic() for a, b in pairs):
            rings.append(pairs)
    return rings


def unfavoured_reasons(drawn: Chem.Mol, tautomer: Chem.Mol) -> tuple[str, ...]:
    """Why ``tautomer`` is an unfavoured form of ``drawn``, or () when it is not.

    Both must have the same atom order, which is what RDKit's enumerator returns for the molecule it was
    given (hydrogens and bond orders move, atoms do not).
    """
    reasons = []
    for ring in _aromatic_rings(drawn):
        if not all(tautomer.GetBondBetweenAtoms(a, b).GetIsAromatic() for a, b in ring):
            reasons.append(LOST_AROMATIC_RING)
            break
    for name, query in _GROUP_QUERIES.items():
        if len(tautomer.GetSubstructMatches(query)) > len(drawn.GetSubstructMatches(query)):
            reasons.append(name)
    return tuple(reasons)


def tautomers(
    mol: Chem.Mol, *, max_transforms: int = MAX_TRANSFORMS, time_limit_s: float = TIME_LIMIT_S
) -> TautomerSet:
    """Every tautomer of ``mol`` and the canonical one. Raises ``ValueError`` when RDKit cannot handle it."""
    parent = canonical_smiles(mol)
    en = enumerator(max_transforms)
    deadline = _Deadline(time_limit_s)  # keep a reference: RDKit does not own it
    en.SetCallback(deadline)
    try:
        found = en.Enumerate(mol)
        best = en.PickCanonical(found)
    except Exception as exc:  # RDKit raises several kinds of error on unusual molecules
        raise ValueError(f"RDKit could not enumerate the tautomers: {str(exc).strip().splitlines()[0]}") from exc

    forms = {Chem.MolToSmiles(t): t for t in found.tautomers}
    scored = {smiles: en.ScoreTautomer(t) for smiles, t in forms.items()}
    canonical = Chem.MolToSmiles(best) if best is not None else parent
    scored.setdefault(canonical, en.ScoreTautomer(best) if best is not None else 0)
    scored.setdefault(parent, 0)
    others = sorted((s for s in scored if s != parent), key=lambda s: (s != canonical, -scored[s], s))
    return TautomerSet(
        parent=parent,
        canonical=canonical,
        others=others,
        total=len(scored),
        complete=found.status == rdMolStandardize.TautomerEnumeratorStatus.Completed,
        unfavoured={
            smiles: why for smiles in others if smiles in forms and (why := unfavoured_reasons(mol, forms[smiles]))
        },
    )
