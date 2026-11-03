from __future__ import annotations

import time

import httpx

from chemomni.chem.props import compute_light, compute_properties

ASPIRIN = "CC(=O)Oc1ccccc1C(=O)O"


def test_aspirin_properties() -> None:
    row = compute_properties(ASPIRIN)
    assert row["ok"] is True
    assert row["formula"] == "C9H8O4"
    assert row["mw"] == 180.16
    assert row["hbd"] == 1
    assert row["hba"] == 3
    assert row["lipinski_violations"] == 0
    assert row["veber_pass"] is True
    assert row["inchikey"] == "BSYNRYMUTXBXSQ-UHFFFAOYSA-N"
    assert 0 < row["qed"] < 1
    assert 1 <= row["sa_score"] <= 10


def test_stereo_and_alerts() -> None:
    row = compute_properties("CC(O)C[C@H](N)C")
    assert row["stereocenters_defined"] == 1
    assert row["stereocenters_undefined"] == 1
    # catechol-like quinone precursor triggers a PAINS/Brenk style alert set; just check the shape
    assert isinstance(compute_properties("Oc1ccccc1O")["brenk_alerts"], list)


def test_invalid_smiles_is_reported_not_raised() -> None:
    row = compute_properties("C1CC")
    assert row["ok"] is False
    assert "SMILES" in row["error"] or "invalid" in row["error"]
    assert compute_properties("c1ccccc1(C)(C)")["ok"] is False


def test_light_subset() -> None:
    row = compute_light(ASPIRIN)
    assert set(row) >= {"mw", "clogp", "tpsa", "hbd", "hba", "rotatable_bonds", "lipinski_violations"}
    assert "qed" not in row


def test_ten_molecules_under_budget() -> None:
    smiles = [f"CC(=O)Oc1ccccc1C(=O)O{'C' * i}" for i in range(10)]
    compute_properties("CCO")  # warm catalogs
    t0 = time.perf_counter()
    for s in smiles:
        compute_properties(s)
    assert (time.perf_counter() - t0) < 0.5  # PLAN budget is 50 ms; loose bound for CI noise


async def test_properties_endpoint(client: httpx.AsyncClient) -> None:
    r = await client.post("/api/chem/properties", json={"smiles": [ASPIRIN, "nope"], "light": True})
    rows = r.json()["rows"]
    assert rows[0]["ok"] and rows[0]["mw"] == 180.16
    assert rows[1]["ok"] is False


async def test_validate_endpoint(client: httpx.AsyncClient) -> None:
    r = await client.post("/api/chem/validate", json={"smiles": ["OCC", "C(C"]})
    rows = r.json()["rows"]
    assert rows[0]["canonical_smiles"] == "CCO"
    assert rows[1]["ok"] is False
