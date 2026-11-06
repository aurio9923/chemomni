from __future__ import annotations

import httpx
import pytest
from rdkit import Chem
from rdkit.Chem import rdDepictor

from chemomni.chem.scan import scan, target_from_selection, target_from_smarts
from tests.test_chat import chat

ACETANILIDE = "CC(=O)Nc1ccccc1"
ASPIRIN = "CC(=O)Oc1ccccc1C(=O)O"


def _molblock(mol: Chem.Mol) -> str:
    rdDepictor.Compute2DCoords(mol)
    return Chem.MolToV3KMolBlock(mol)


def _drawn(smiles: str) -> tuple[str, Chem.Mol]:
    mol = Chem.MolFromSmiles(smiles)
    return _molblock(mol), mol


def _ring_numbers(mol: Chem.Mol) -> list[int]:
    """1-based molfile numbers of the first ring (the phenyl)."""
    return [i + 1 for i in mol.GetRingInfo().AtomRings()[0]]


def _scan(smiles: str, operation: str, **kwargs):  # type: ignore[no-untyped-def]
    molfile, mol = _drawn(smiles)
    target, err = target_from_selection(molfile, _ring_numbers(mol))
    assert target is not None, err
    return scan(target, operation, **kwargs)  # type: ignore[arg-type]


def _canon(*smiles: str) -> set[str]:
    return {Chem.CanonSmiles(s) for s in smiles}


# ---------------------------------------------------------------- the chemistry


def test_phenyl_to_pyridine_gives_ortho_meta_para() -> None:
    r = _scan(ACETANILIDE, "replace_atom", element="N")
    assert r.ok
    assert {s for s, _ in r.candidates} == _canon("CC(=O)Nc1ccccn1", "CC(=O)Nc1cccnc1", "CC(=O)Nc1ccncc1")
    notes = [n for _, n in r.candidates]
    assert [n.split(" (")[0] for n in notes] == ["N at ortho", "N at meta", "N at para"]
    assert r.merged == 2  # the other ortho and the other meta
    assert "atoms 6, 10" in notes[0] and "atoms 7, 9" in notes[1] and "atom 8" in notes[2]
    assert r.skipped == []  # the attachment carbon is not a position, so it is not reported


def test_mono_fluorination_of_a_phenyl() -> None:
    r = _scan(ACETANILIDE, "substitute", group="F")
    assert {s for s, _ in r.candidates} == _canon("CC(=O)Nc1ccccc1F", "CC(=O)Nc1cccc(F)c1", "CC(=O)Nc1ccc(F)cc1")


def test_unsubstituted_ring_collapses_to_one_variant() -> None:
    r = _scan("c1ccccc1", "substitute", group="Cl")
    assert [s for s, _ in r.candidates] == ["Clc1ccccc1"]
    assert r.merged == 5


def test_substituted_ring_lists_each_free_position_by_atom_number() -> None:
    r = _scan(ASPIRIN, "replace_atom", element="N")  # two attachment points: no ortho/meta/para
    assert r.ok and len(r.candidates) == 4
    assert all(n.startswith("N at atom ") for _, n in r.candidates)
    assert all("ortho" not in n for _, n in r.candidates)


def test_selected_side_chain_atoms_and_groups() -> None:
    molfile, _ = _drawn("CCCc1ccccc1")
    target, _ = target_from_selection(molfile, [1, 2, 3])
    r = scan(target, "substitute", group="OC")  # type: ignore[arg-type]
    assert {s for s, _ in r.candidates} == _canon("COCCCc1ccccc1", "CC(OC)Cc1ccccc1", "CCC(OC)c1ccccc1")


def test_heteroatom_swap_and_failures_are_explained() -> None:
    r = _scan("c1ccoc1", "replace_atom", element="N")  # furan: the ring O cannot become N
    assert r.ok
    assert r.skipped == [{"smiles": "N at atom 4", "reason": "would break the aromatic ring"}]


def test_errors_are_actionable() -> None:
    molfile, mol = _drawn(ACETANILIDE)
    ring = _ring_numbers(mol)
    target, _ = target_from_selection(molfile, ring)
    assert scan(target, "substitute", group="F.F").errors == ["a group must be one connected fragment"]  # type: ignore[arg-type]
    assert scan(target, "substitute", group="C1CC").errors[0].startswith("group:")  # type: ignore[arg-type]
    assert "already N" in scan(_pyridine_target(), "replace_atom", element="N").errors[0]


def test_too_many_variants_are_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("chemomni.chem.scan.MAX_VARIANTS", 2)
    r = _scan(ACETANILIDE, "substitute", group="F")  # 3 distinct variants
    assert not r.ok and r.candidates == [] and "3 distinct variants; select fewer atoms" in r.errors[0]


def _pyridine_target():  # type: ignore[no-untyped-def]
    molfile, mol = _drawn("c1ccncc1")
    target, _ = target_from_selection(molfile, _ring_numbers(mol))
    # only the nitrogen: nothing else to convert
    n = [a.GetIdx() for a in target.mol.GetAtoms() if a.GetSymbol() == "N"]  # type: ignore[union-attr]
    target.atoms = n  # type: ignore[union-attr]
    return target


def test_selection_errors() -> None:
    two = Chem.CombineMols(Chem.MolFromSmiles("CCO"), Chem.MolFromSmiles("c1ccccc1"))
    target, err = target_from_selection(_molblock(two), [1, 5])
    assert target is None and "several molecules" in err  # type: ignore[operator]
    target, err = target_from_selection(_molblock(two), [99])
    assert target is None and "does not hold" not in err and "only hydrogens" in err  # type: ignore[operator]


def test_numbers_follow_the_molfile_even_with_explicit_hydrogens() -> None:
    mol = Chem.AddHs(Chem.MolFromSmiles("Cc1ccccc1"))
    # move a hydrogen to the front so every heavy atom number shifts by one
    first_h = next(a.GetIdx() for a in mol.GetAtoms() if a.GetSymbol() == "H")
    order = [first_h, *[i for i in range(mol.GetNumAtoms()) if i != first_h]]
    shifted = Chem.RenumberAtoms(mol, order)
    ring = [a.GetIdx() + 1 for a in shifted.GetAtoms() if a.GetIsAromatic()]
    target, err = target_from_selection(_molblock(shifted), ring)
    assert target is not None, err
    r = scan(target, "replace_atom", element="N")
    assert {s for s, _ in r.candidates} == _canon("Cc1ccccn1", "Cc1cccnc1", "Cc1ccncc1")
    assert r.parent_smiles == "Cc1ccccc1"


def test_smarts_mode() -> None:
    target, err = target_from_smarts("c1ccccc1CC(=O)Nc1ccccc1", "c1ccccc1")
    assert target is not None, err
    r = scan(target, "replace_atom", element="N")
    assert r.ok and len(r.candidates) == 6  # two phenyls, three positions each (not merged across rings)
    assert target_from_smarts("CCO", "c1ccccc1")[1] == "the pattern c1ccccc1 does not occur in the molecule"
    assert target_from_smarts("CCO", "[")[1] == "invalid SMARTS: ["


# ---------------------------------------------------------------- the tool through the chat loop


async def _tab(client: httpx.AsyncClient) -> dict:
    return (await client.get("/api/workspace")).json()["tabs"][0]


def _snapshot(tab: dict, smiles: str, selected: list[int] | None) -> dict:
    molfile, mol = _drawn(smiles)
    return {
        "active_tab": {
            "id": tab["id"],
            "name": tab["name"],
            "revision": tab["revision"],
            "smiles": [smiles],
            "molfile": molfile,
            "selection_atoms": selected if selected is not None else _ring_numbers(mol),
        }
    }


async def test_phenyl_to_pyridine_from_the_selection(client: httpx.AsyncClient) -> None:
    tab, conv = await _tab(client), (await client.get("/api/workspace")).json()["conversation_id"]
    events = await chat(client, conv, "make the selected phenyl a pyridine", _snapshot(tab, ACETANILIDE, None))

    created = next(d for n, d in events if n == "tab.create")
    assert created["tab"]["name"] == "Pyridine scan" and len(created["tab"]["smiles_cache"]) == 3
    art = next(d["artifact"] for n, d in events if n == "artifact")["payload"]
    assert art["parent"]["smiles"] == Chem.CanonSmiles(ACETANILIDE)
    assert [i["rationale"].split(" (")[0] for i in art["items"]] == ["N at ortho", "N at meta", "N at para"]
    assert all(i["similarity"] is not None and i["svg"] for i in art["items"])
    assert art["rejected"] == []
    result = next(d for n, d in events if n == "tool.result")
    assert result["ok"] and result["summary"] == "3 variant(s) of c1ccccc1"
    assert "Scanned c1ccccc1: 3 variant(s)." in "".join(d["delta"] for n, d in events if n == "text.delta")


async def test_monofluorination_of_a_selected_phenyl(client: httpx.AsyncClient) -> None:
    tab, conv = await _tab(client), (await client.get("/api/workspace")).json()["conversation_id"]
    events = await chat(client, conv, "monofluorinate this ring", _snapshot(tab, ACETANILIDE, None))
    created = next(d for n, d in events if n == "tab.create")
    assert sorted(created["tab"]["smiles_cache"]) == sorted(
        _canon("CC(=O)Nc1ccccc1F", "CC(=O)Nc1cccc(F)c1", "CC(=O)Nc1ccc(F)cc1")
    )


async def test_nothing_selected_asks_the_user(client: httpx.AsyncClient) -> None:
    tab, conv = await _tab(client), (await client.get("/api/workspace")).json()["conversation_id"]
    events = await chat(client, conv, "make it a pyridine", _snapshot(tab, ACETANILIDE, []))
    assert not any(n == "tab.create" for n, _ in events)
    result = next(d for n, d in events if n == "tool.result")
    assert result["ok"] is False and "select the part" in result["summary"]
