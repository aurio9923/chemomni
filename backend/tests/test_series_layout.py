"""Edits of several compounds collected on one tab keep each compound's own drawing.

Reported from real use: "convert the esters of 1, 3 and 5 to acids and put them on one tab" gave
structures in RDKit's own orientation, not the way the three compounds were drawn. A grid used to follow
ONE parent; now every candidate follows the drawing of its own parent (named, or the most similar
structure of the tab).
"""

from __future__ import annotations

import math
from typing import Any

import httpx
import pytest
from rdkit import Chem
from rdkit.Chem import rdDepictor, rdFMCS

from chemomni.chem.generate import evaluate
from chemomni.chem.layout import to_ketcher_scale, translate
from chemomni.llm.fake_provider import FakeProvider
from chemomni.tools.generate_tools import _compact
from tests.test_chat import chat
from tests.test_compounds import _snapshot, _tab

ESTERS = ["COC(=O)c1nnn2ccccc12", "COC(=O)c1cnn2ccncc12", "COC(=O)c1cnn2ncccc12"]
ACIDS = ["O=C(O)c1nnn2ccccc12", "O=C(O)c1cnn2ccncc12", "O=C(O)c1cnn2ncccc12"]
# how each ester was drawn on the tab: rotation in degrees, mirrored or not
DRAWN = [(0, False), (90, False), (200, True)]


def _drawn(smiles: str, angle: float, mirror: bool, x: float) -> Chem.Mol:
    mol = Chem.MolFromSmiles(smiles)
    rdDepictor.Compute2DCoords(mol)
    mol = to_ketcher_scale(mol)
    conf = mol.GetConformer()
    a = math.radians(angle)
    for i in range(mol.GetNumAtoms()):
        p = conf.GetAtomPosition(i)
        px = -p.x if mirror else p.x
        conf.SetAtomPosition(i, (px * math.cos(a) - p.y * math.sin(a), px * math.sin(a) + p.y * math.cos(a), 0.0))
    return translate(mol, x, 0.0)


def _tab_of_esters() -> tuple[str, list[Chem.Mol]]:
    parents = [_drawn(s, *d, x=12.0 * i) for i, (s, d) in enumerate(zip(ESTERS, DRAWN, strict=True))]
    combined = parents[0]
    for m in parents[1:]:
        combined = Chem.CombineMols(combined, m)
    return Chem.MolToV3KMolBlock(combined), parents


def _drift(parent: Chem.Mol, child_block: str) -> float:
    """How far the shared atoms moved relative to the parent's drawing, ignoring a plain translation."""
    child = Chem.MolFromMolBlock(child_block)
    mcs = rdFMCS.FindMCS([parent, child], ringMatchesRingOnly=True, completeRingsOnly=True)
    query = Chem.MolFromSmarts(mcs.smartsString)
    pm, cm = parent.GetSubstructMatch(query), child.GetSubstructMatch(query)
    pc, cc = parent.GetConformer(), child.GetConformer()
    pp = [(pc.GetAtomPosition(i).x, pc.GetAtomPosition(i).y) for i in pm]
    cp = [(cc.GetAtomPosition(i).x, cc.GetAtomPosition(i).y) for i in cm]
    px, py = sum(p[0] for p in pp) / len(pp), sum(p[1] for p in pp) / len(pp)
    cx, cy = sum(p[0] for p in cp) / len(cp), sum(p[1] for p in cp) / len(cp)
    return max(math.hypot((a[0] - px) - (b[0] - cx), (a[1] - py) - (b[1] - cy)) for a, b in zip(pp, cp, strict=True))


def test_plain_layout_loses_the_drawing() -> None:
    """The control: without the tab, structures come out in RDKit's orientation (what the user saw)."""
    _molfile, parents = _tab_of_esters()
    result = evaluate(None, [(a, "acid") for a in ACIDS], None)
    assert result.ok
    drifts = [_drift(p, a.molblock) for p, a in zip(parents, result.accepted, strict=True)]
    assert max(drifts) > 0.5


def test_each_edit_keeps_its_own_parents_drawing() -> None:
    molfile, parents = _tab_of_esters()
    result = evaluate(None, [(a, "acid", e) for a, e in zip(ACIDS, ESTERS, strict=True)], molfile)
    assert result.ok and len(result.accepted) == 3
    for parent, item in zip(parents, result.accepted, strict=True):
        assert _drift(parent, item.molblock) < 0.02
    assert [a.parent_smiles for a in result.accepted] == [Chem.CanonSmiles(e) for e in ESTERS]
    assert result.parent_smiles is None and result.parent_molblock is None  # three parents: no single parent card
    assert [round(a.similarity or 0, 2) > 0.4 for a in result.accepted] == [True] * 3  # each against its own parent


def test_candidates_without_a_named_parent_follow_the_most_similar_drawing() -> None:
    """The model did not name parents (it called propose_structures with no parent at all)."""
    molfile, parents = _tab_of_esters()
    result = evaluate(None, [(a, "acid") for a in ACIDS], molfile)
    assert result.ok and len(result.accepted) == 3
    for parent, item in zip(parents, result.accepted, strict=True):
        assert _drift(parent, item.molblock) < 0.02
    assert all(a.similarity is None for a in result.accepted)  # no parent was named, so no similarity


def test_unrelated_ideas_get_a_plain_layout() -> None:
    molfile, _ = _tab_of_esters()
    result = evaluate(None, [("CCCCCCO", "a long chain")], molfile)
    assert result.ok and result.accepted[0].changed_atoms == []


def test_one_parent_series_is_unchanged() -> None:
    molfile, parents = _tab_of_esters()
    result = evaluate(ESTERS[0], [("O=C(O)c1nnn2ccccc12", "acid"), ("NC(=O)c1nnn2ccccc12", "amide")], molfile)
    assert result.ok and result.parent_smiles == Chem.CanonSmiles(ESTERS[0]) and result.parent_molblock
    assert all(_drift(parents[0], a.molblock) < 0.02 for a in result.accepted)


def test_a_bad_parent_rejects_only_its_candidate() -> None:
    result = evaluate(None, [("CCO", "ok", None), ("CCCO", "bad parent", "not a smiles")])
    assert result.ok and [a.smiles for a in result.accepted] == ["CCO"]
    assert result.rejected[0]["reason"].startswith("its parent")


# ---------------------------------------------------------------------------------- in the chat


async def test_propose_with_parent_refs_makes_one_tab(client: httpx.AsyncClient) -> None:
    tab = await _tab(client)
    conv = (await client.get("/api/workspace")).json()["conversation_id"]
    args: dict[str, Any] = {
        "title": "1, 2, 3 homologues",
        "candidates": [
            {"smiles": "CCCO", "rationale": "propanol", "parent_ref": "1"},
            {"smiles": "CNc1ccccc1", "rationale": "N-methyl", "parent_ref": "2"},
            {"smiles": "CCC(=O)O", "rationale": "propionic acid", "parent_ref": "3"},
        ],
    }
    client._transport.app.state.provider = FakeProvider(  # type: ignore[attr-defined]
        [[{"tool": "propose_structures", "args": args, "id": "p"}], [{"text": "ok"}]]
    )
    events = await chat(client, conv, "edit 1, 2 and 3 together", _snapshot(tab))
    result = next(d for n, d in events if n == "tool.result")
    assert result["ok"] is True
    assert sum(1 for n, _ in events if n == "tab.create") == 1  # one tab for all three
    grid = next(d["artifact"] for n, d in events if n == "artifact")
    assert grid["type"] == "grid" and grid["payload"]["parent"] is None
    assert [i["parent_smiles"] for i in grid["payload"]["items"]] == ["CCO", "Nc1ccccc1", "CC(=O)O"]
    # what the model reads back names each result's own parent (and stays short for a one-parent series)
    items = grid["payload"]["items"]
    assert [_compact(i, with_parent=True)["parent"] for i in items] == ["CCO", "Nc1ccccc1", "CC(=O)O"]
    assert "parent" not in _compact(items[0])


async def test_an_unknown_parent_number_creates_nothing(client: httpx.AsyncClient) -> None:
    tab = await _tab(client)
    conv = (await client.get("/api/workspace")).json()["conversation_id"]
    args = {"title": "x", "candidates": [{"smiles": "CCCO", "rationale": "r", "parent_ref": "9"}]}
    client._transport.app.state.provider = FakeProvider(  # type: ignore[attr-defined]
        [[{"tool": "propose_structures", "args": args, "id": "p"}], [{"text": "ok"}]]
    )
    events = await chat(client, conv, "edit 9", _snapshot(tab))
    result = next(d for n, d in events if n == "tool.result")
    assert result["ok"] is False
    assert not any(n == "tab.create" for n, _ in events)


async def test_a_failure_while_building_the_cards_leaves_no_tab(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A tab created before a later step failed made the model retry and open the same edit again and again."""

    def boom(*_a: Any, **_k: Any) -> str:
        raise RuntimeError("depiction failed")

    monkeypatch.setattr("chemomni.tools.generate_tools.depict_molblock", boom)
    monkeypatch.setattr("chemomni.tools.edit_tools.depict_molblock", boom)
    tab = await _tab(client)
    conv = (await client.get("/api/workspace")).json()["conversation_id"]
    for call in (
        {"tool": "propose_structures", "args": {"title": "x", "candidates": [{"smiles": "CCCO", "rationale": "r"}]}},
        {"tool": "edit_structure", "args": {"parent_ref": "1", "new_smiles": "CCCO", "description": "x"}},
    ):
        client._transport.app.state.provider = FakeProvider(  # type: ignore[attr-defined]
            [[{**call, "id": "p"}], [{"text": "ok"}]]
        )
        events = await chat(client, conv, "go", _snapshot(tab))
        assert next(d for n, d in events if n == "tool.result")["ok"] is False
        assert not any(n == "tab.create" for n, _ in events), call["tool"]
