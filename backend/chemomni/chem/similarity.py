from __future__ import annotations

from typing import Any

from rdkit import Chem, DataStructs
from rdkit.Chem import rdFingerprintGenerator

from chemomni.chem.props import canonical_smiles, parse_smiles

_morgan = rdFingerprintGenerator.GetMorganGenerator(radius=2, fpSize=2048)


def tanimoto(smiles_a: str, smiles_b: str) -> dict[str, Any]:
    a, err_a = parse_smiles(smiles_a)
    b, err_b = parse_smiles(smiles_b)
    if a is None or b is None:
        return {"ok": False, "errors": [e for e in (err_a, err_b) if e]}
    sim = DataStructs.TanimotoSimilarity(_morgan.GetFingerprint(a), _morgan.GetFingerprint(b))
    return {
        "ok": True,
        "smiles_a": canonical_smiles(a),
        "smiles_b": canonical_smiles(b),
        "tanimoto": round(sim, 3),
        "fingerprint": "Morgan r=2, 2048 bits (ECFP4-like)",
    }


def substructure(smiles: list[str], smarts: str) -> dict[str, Any]:
    query = Chem.MolFromSmarts(smarts)
    if query is None:
        return {"ok": False, "errors": [f"invalid SMARTS: {smarts}"]}
    rows: list[dict[str, Any]] = []
    for s in smiles:
        mol, err = parse_smiles(s)
        if mol is None:
            rows.append({"input": s, "ok": False, "error": err})
            continue
        matches = mol.GetSubstructMatches(query)
        rows.append(
            {
                "input": s,
                "ok": True,
                "smiles": canonical_smiles(mol),
                "match": bool(matches),
                "n_matches": len(matches),
                "atoms": sorted({i for m in matches for i in m}),
            }
        )
    return {"ok": True, "smarts": smarts, "rows": rows}
