"""Import of structure files into tabs and export with properties and numbers (M4)."""

from __future__ import annotations

import math

import httpx
import pytest
from rdkit import Chem
from rdkit.Chem import AllChem, rdDepictor

from chemomni.chem.io import MAX_STRUCTURES, StructureFileError, export_structures, parse_import, tab_content
from chemomni.chem.layout import bbox
from tests.test_compounds import QUERIES, THREE

SERIES = [
    ("aspirin", "CC(=O)Oc1ccccc1C(=O)O"),
    ("caffeine", "Cn1cnc2c1c(=O)n(C)c(=O)n2C"),
    ("ibuprofen", "CC(C)Cc1ccc(cc1)C(C)C(=O)O"),
]


def _sdf(records: list[tuple[str, str]] = SERIES, *, newline: str = "\n") -> str:
    out = []
    for name, smiles in records:
        mol = Chem.MolFromSmiles(smiles)
        mol.SetProp("_Name", name)
        rdDepictor.Compute2DCoords(mol)
        out.append(Chem.MolToMolBlock(mol) + f"> <ID>\n{name.upper()}\n\n$$$$\n")
    return "".join(out).replace("\n", newline)


def _boxes(molfile: str) -> list[tuple[float, float, float, float]]:
    mol = Chem.MolFromMolBlock(molfile, sanitize=False, removeHs=False)
    return [bbox(frag) for frag in Chem.GetMolFrags(mol, asMols=True, sanitizeFrags=False)]


def _overlap(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


# ---------------------------------------------------------------------------------------- import


def test_a_multi_record_sdf_becomes_one_grid_tab() -> None:
    imported = parse_import("series.sdf", _sdf())
    assert [s.name for s in imported.structures] == ["aspirin", "caffeine", "ibuprofen"]
    content = tab_content(imported)
    assert len(content.smiles) == 3
    boxes = _boxes(content.molfile)
    assert len(boxes) == 3
    assert not any(_overlap(a, b) for i, a in enumerate(boxes) for b in boxes[i + 1 :])  # Ketcher stacks them
    # reading order: the first record is the top-left one
    assert boxes[0][0] < boxes[1][0] and boxes[0][1] >= boxes[2][1]


def test_windows_line_endings_are_read() -> None:
    assert len(parse_import("series.sdf", _sdf(newline="\r\n")).structures) == 3


def test_a_single_molfile_keeps_its_drawing() -> None:
    mol = Chem.MolFromSmiles("COC(=O)c1nnn2ccccc12")
    rdDepictor.Compute2DCoords(mol)
    conf = mol.GetConformer()
    a = math.radians(37)
    for i in range(mol.GetNumAtoms()):
        p = conf.GetAtomPosition(i)
        conf.SetAtomPosition(
            i, (p.x * math.cos(a) - p.y * math.sin(a) + 5, p.x * math.sin(a) + p.y * math.cos(a) - 3, 0)
        )
    imported = parse_import("drawn.mol", Chem.MolToMolBlock(mol))
    placed = Chem.MolFromMolBlock(tab_content(imported).molfile, sanitize=False)
    # same shape: the distances between all atom pairs are in the same proportion
    original = [conf.GetAtomPosition(i) for i in range(mol.GetNumAtoms())]
    result = [placed.GetConformer().GetAtomPosition(i) for i in range(placed.GetNumAtoms())]
    ratio = (result[0] - result[1]).Length() / (original[0] - original[1]).Length()
    for i in range(len(original)):
        for j in range(i):
            assert (result[i] - result[j]).Length() == pytest.approx(
                ratio * (original[i] - original[j]).Length(), abs=2e-3
            )


def test_a_3d_record_is_drawn_flat_without_its_hydrogens() -> None:
    mol = Chem.AddHs(Chem.MolFromSmiles("CCO"))
    AllChem.EmbedMolecule(mol, randomSeed=7)
    imported = parse_import("docked.sdf", Chem.MolToMolBlock(mol) + "$$$$\n")
    content = tab_content(imported)
    placed = Chem.MolFromMolBlock(content.molfile, sanitize=False, removeHs=False)
    assert placed.GetNumAtoms() == 3
    assert all(abs(placed.GetConformer().GetAtomPosition(i).z) < 1e-6 for i in range(3))
    assert content.smiles == ["CCO"]


def test_a_smiles_list_skips_the_header_comments_and_bad_lines() -> None:
    text = "SMILES name\n# a comment\nCCO ethanol\n\nnot-a-smiles broken\nc1ccccc1\tbenzene\n"
    imported = parse_import("list.smi", text)
    assert [s.name for s in imported.structures] == ["ethanol", "benzene"]
    assert len(imported.skipped) == 1 and imported.skipped[0].startswith("line 5:")


def test_a_csv_with_a_smiles_column() -> None:
    text = 'ID,Canonical_SMILES,activity\nA1,CCO,5.1\nA2,"CC(=O)O",6.0\nA3,,7\n'
    imported = parse_import("table.csv", text)
    assert [s.name for s in imported.structures] == ["A1", "A2"]
    assert imported.skipped and imported.skipped[0].startswith("row 4:")
    tsv = parse_import("table.tsv", "smiles\tid\nCCO\tx\n")
    assert len(tsv.structures) == 1


def test_a_table_without_a_smiles_column_says_so() -> None:
    with pytest.raises(StructureFileError, match="no SMILES column"):
        parse_import("table.csv", "id,value\n1,2\n")


def test_nothing_readable_is_an_error_that_names_the_file() -> None:
    with pytest.raises(StructureFileError, match=r"no structure could be read from notes.txt"):
        parse_import("notes.txt", "hello there\nthis is not chemistry\n")
    with pytest.raises(StructureFileError, match="empty"):
        parse_import("empty.sdf", "  \n")


def test_a_long_list_is_cut_and_the_user_is_told() -> None:
    imported = parse_import("many.smi", "\n".join("C" * (i % 7 + 1) for i in range(MAX_STRUCTURES + 5)))
    assert len(imported.structures) == MAX_STRUCTURES and imported.found == MAX_STRUCTURES + 5
    assert f"only the first {MAX_STRUCTURES} of {MAX_STRUCTURES + 5}" in imported.skipped[-1]


async def test_the_import_endpoint_creates_a_tab(client: httpx.AsyncClient) -> None:
    resp = await client.post("/api/tabs/import", json={"filename": "C:\\data\\series.sdf", "text": _sdf()})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert (body["imported"], body["found"], body["skipped"]) == (3, 3, [])
    assert body["tab"]["name"] == "series" and body["tab"]["content_format"] == "mol"
    assert len(body["tab"]["smiles_cache"]) == 3
    tabs = (await client.get("/api/workspace")).json()["tabs"]
    assert any(t["id"] == body["tab"]["id"] for t in tabs)


async def test_the_import_endpoint_rejects_garbage_with_a_message(client: httpx.AsyncClient) -> None:
    before = len((await client.get("/api/workspace")).json()["tabs"])
    resp = await client.post("/api/tabs/import", json={"filename": "x.txt", "text": "nothing here"})
    assert resp.status_code == 400 and "no structure could be read" in resp.json()["detail"]
    assert len((await client.get("/api/workspace")).json()["tabs"]) == before  # no empty tab left behind


# ---------------------------------------------------------------------------------------- export


def test_csv_has_the_compound_numbers_and_properties() -> None:
    result = export_structures("csv", THREE["mol"], THREE["ket"])
    lines = result.content.strip().split("\n")
    assert lines[0].startswith("number,smiles,formula,mw,clogp,tpsa")
    rows = [line.split(",") for line in lines[1:]]
    assert [(r[0], r[1]) for r in rows] == [("1", "CCO"), ("2", "Nc1ccccc1"), ("3", "CC(=O)O")]
    assert rows[0][3] == "46.07" and result.count == 3 and result.skipped == []


def test_sdf_records_carry_the_number_and_properties() -> None:
    result = export_structures("sdf", THREE["mol"], THREE["ket"])
    supplier = Chem.SDMolSupplier()
    supplier.SetData(result.content)
    mols = [m for m in supplier if m is not None]
    assert [m.GetProp("_Name") for m in mols] == ["1", "2", "3"]
    assert [Chem.MolToSmiles(m) for m in mols] == ["CCO", "Nc1ccccc1", "CC(=O)O"]
    assert mols[0].GetProp("Number") == "1" and mols[0].GetProp("mw") == "46.07"
    assert mols[2].GetProp("formula") == "C2H4O2"
    assert mols[0].GetConformer().GetPositions().any()  # the drawing's coordinates are kept


def test_smiles_list_export() -> None:
    assert export_structures("smi", THREE["mol"], THREE["ket"]).content == "CCO 1\nNc1ccccc1 2\nCC(=O)O 3\n"


def test_without_numbers_the_structures_are_still_exported() -> None:
    result = export_structures("csv", THREE["mol"], None)
    assert [line.split(",")[0] for line in result.content.strip().split("\n")[1:]] == ["", "", ""]
    assert export_structures("smi", THREE["mol"], None).content.count("\n") == 3


def test_a_query_is_not_exported() -> None:
    drawn = QUERIES["smarts_list"]  # [N,O] on a ring: not a molecule
    with pytest.raises(ValueError, match="no structure"):
        export_structures("csv", drawn["mol"], drawn["ket"])


def test_an_unknown_format_is_refused() -> None:
    with pytest.raises(ValueError, match="unknown format"):
        export_structures("xlsx", THREE["mol"])


async def test_the_export_endpoint(client: httpx.AsyncClient) -> None:
    resp = await client.post(
        "/api/chem/export",
        json={"format": "csv", "molfile": THREE["mol"], "ket": THREE["ket"], "name": "../Series 1: acids"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["filename"].endswith(".csv") and "/" not in body["filename"] and ".." not in body["filename"]
    assert body["count"] == 3 and body["content"].startswith("number,smiles")
    bad = await client.post("/api/chem/export", json={"format": "csv", "molfile": "not a molfile"})
    assert bad.status_code == 400


def test_import_then_export_returns_the_same_structures() -> None:
    content = tab_content(parse_import("series.sdf", _sdf()))
    result = export_structures("smi", content.molfile)
    assert sorted(line.split()[0] for line in result.content.strip().split("\n")) == sorted(
        Chem.CanonSmiles(s) for _, s in SERIES
    )
