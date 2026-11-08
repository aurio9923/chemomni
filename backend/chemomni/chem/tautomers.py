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
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from rdkit import Chem
from rdkit.Chem.MolStandardize import rdMolStandardize

from chemomni.chem.props import canonical_smiles

MAX_TRANSFORMS = 1000  # RDKit's default, kept explicit
TIME_LIMIT_S = 5.0  # of the 30 s a tool may take


@dataclass
class TautomerSet:
    parent: str  # canonical SMILES of the molecule as given
    canonical: str  # canonical tautomer, which may be the molecule as given
    others: list[str] = field(default_factory=list)  # every other tautomer: the canonical one first, then best score
    total: int = 1  # distinct tautomers found, the given one included
    complete: bool = True  # False when a limit stopped the enumeration

    @property
    def is_canonical(self) -> bool:
        return self.canonical == self.parent


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

    scored = {Chem.MolToSmiles(t): en.ScoreTautomer(t) for t in found.tautomers}
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
    )
