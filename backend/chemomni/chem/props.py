"""Standard property set (PLAN §7). Pure RDKit, no web framework imports."""

from __future__ import annotations

import os
import sys
from functools import lru_cache
from typing import Any

from rdkit import Chem, RDConfig, RDLogger
from rdkit.Chem import QED, Crippen, Descriptors, FilterCatalog, Lipinski, rdMolDescriptors

RDLogger.DisableLog("rdApp.*")

_sa_dir = os.path.join(RDConfig.RDContribDir, "SA_Score")
if _sa_dir not in sys.path:
    sys.path.append(_sa_dir)
import sascorer  # noqa: E402  (RDKit Contrib module, path set above)


def _catalog(*names: str) -> FilterCatalog.FilterCatalog:
    params = FilterCatalog.FilterCatalogParams()
    for name in names:
        params.AddCatalog(getattr(FilterCatalog.FilterCatalogParams.FilterCatalogs, name))
    return FilterCatalog.FilterCatalog(params)


@lru_cache(maxsize=1)
def _pains() -> FilterCatalog.FilterCatalog:
    return _catalog("PAINS_A", "PAINS_B", "PAINS_C")


@lru_cache(maxsize=1)
def _brenk() -> FilterCatalog.FilterCatalog:
    return _catalog("BRENK")


def parse_smiles(smiles: str) -> tuple[Chem.Mol | None, str | None]:
    """Parse with explicit sanitization so the error message is useful to a human or an LLM."""
    smiles = smiles.strip()
    if not smiles:
        return None, "empty SMILES"
    mol = Chem.MolFromSmiles(smiles, sanitize=False)
    if mol is None:
        return None, f"cannot parse SMILES: {smiles}"
    try:
        Chem.SanitizeMol(mol)
    except Exception as exc:  # RDKit raises several sanitization exception types
        return None, f"invalid structure: {exc}"
    if mol.GetNumAtoms() == 0:
        return None, "empty molecule"
    return mol, None


def canonical_smiles(mol: Chem.Mol) -> str:
    return Chem.MolToSmiles(mol, canonical=True, isomericSmiles=True)


def _stereo_counts(mol: Chem.Mol) -> tuple[int, int]:
    centers = Chem.FindMolChiralCenters(mol, includeUnassigned=True, useLegacyImplementation=False)
    defined = sum(1 for _, label in centers if label != "?")
    return defined, len(centers) - defined


def _alerts(catalog: FilterCatalog.FilterCatalog, mol: Chem.Mol) -> list[str]:
    return sorted({entry.GetDescription() for entry in catalog.GetMatches(mol)})


def _r(value: float, digits: int = 2) -> float:
    return round(float(value), digits) + 0.0  # + 0.0 turns -0.0 into 0.0


@lru_cache(maxsize=4096)
def _properties_for_canonical(smiles: str) -> dict[str, Any]:
    mol = Chem.MolFromSmiles(smiles)
    mw = Descriptors.MolWt(mol)
    logp = Crippen.MolLogP(mol)
    hbd = Lipinski.NumHDonors(mol)
    hba = Lipinski.NumHAcceptors(mol)
    tpsa = rdMolDescriptors.CalcTPSA(mol)
    rotb = rdMolDescriptors.CalcNumRotatableBonds(mol)
    defined, undefined = _stereo_counts(mol)
    lipinski_violations = sum([mw > 500, logp > 5, hbd > 5, hba > 10])
    return {
        "smiles": smiles,
        "formula": rdMolDescriptors.CalcMolFormula(mol),
        "mw": _r(mw),
        "exact_mass": _r(Descriptors.ExactMolWt(mol), 4),
        "heavy_atoms": mol.GetNumHeavyAtoms(),
        "clogp": _r(logp),
        "tpsa": _r(tpsa),
        "hbd": hbd,
        "hba": hba,
        "rotatable_bonds": rotb,
        "rings": rdMolDescriptors.CalcNumRings(mol),
        "aromatic_rings": rdMolDescriptors.CalcNumAromaticRings(mol),
        "fsp3": _r(rdMolDescriptors.CalcFractionCSP3(mol)),
        "formal_charge": Chem.GetFormalCharge(mol),
        "stereocenters_defined": defined,
        "stereocenters_undefined": undefined,
        "lipinski_violations": lipinski_violations,
        "veber_pass": tpsa <= 140 and rotb <= 10,
        "qed": _r(QED.qed(mol)),
        "sa_score": _r(sascorer.calculateScore(mol)),
        "pains_alerts": _alerts(_pains(), mol),
        "brenk_alerts": _alerts(_brenk(), mol),
        "inchikey": Chem.MolToInchiKey(mol) or None,
    }


def compute_properties(smiles: str) -> dict[str, Any]:
    """Full property row for one SMILES. Invalid input returns ``{"ok": False, ...}``."""
    mol, error = parse_smiles(smiles)
    if mol is None:
        return {"ok": False, "input": smiles, "error": error}
    return {"ok": True, "input": smiles, **_properties_for_canonical(canonical_smiles(mol))}


LIGHT_KEYS = ("mw", "clogp", "tpsa", "hbd", "hba", "rotatable_bonds", "lipinski_violations")


def compute_light(smiles: str) -> dict[str, Any]:
    """Subset shown in the live property bar."""
    row = compute_properties(smiles)
    if not row["ok"]:
        return row
    return {
        "ok": True,
        "input": smiles,
        "smiles": row["smiles"],
        "formula": row["formula"],
        **{k: row[k] for k in LIGHT_KEYS},
    }
