from __future__ import annotations

import httpx
from rdkit import Chem
from rdkit.Chem import rdDepictor

from chemomni.chem.edit import edit, selection_info
from chemomni.llm.context import Snapshot, snapshot_block
from chemomni.llm.fake_provider import FakeProvider
from tests.test_chat import chat

ASPIRIN = "CC(=O)Oc1ccccc1C(=O)O"
F_ASPIRIN = "CC(=O)Oc1ccc(F)cc1C(=O)O"


def _two_molecule_molfile() -> tuple[str, Chem.Mol]:
    asp = Chem.MolFromSmiles(ASPIRIN)
    rdDepictor.Compute2DCoords(asp)
    eth = Chem.MolFromSmiles("CCO")
    rdDepictor.Compute2DCoords(eth)
    conf = eth.GetConformer()
    for i in range(eth.GetNumAtoms()):
        p = conf.GetAtomPosition(i)
        conf.SetAtomPosition(i, (p.x + 10, p.y, 0))
    return Chem.MolToV3KMolBlock(Chem.CombineMols(asp, eth)), asp


# ---------------------------------------------------------------- chemistry


def test_edit_keeps_the_users_layout_and_other_molecules() -> None:
    molfile, asp = _two_molecule_molfile()
    r = edit(ASPIRIN, F_ASPIRIN, molfile)
    assert r.ok and r.new_smiles == Chem.CanonSmiles(F_ASPIRIN)
    new = Chem.MolFromMolBlock(r.molblock)
    # the shared core keeps the parent's coordinates
    p_new, p_old = new.GetConformer().GetAtomPosition(0), asp.GetConformer().GetAtomPosition(0)
    assert abs(p_new.x - p_old.x) < 0.01 and abs(p_new.y - p_old.y) < 0.01  # molfiles keep 4 decimals
    assert len(r.changed_atoms) == 1  # just the fluorine
    doc = Chem.MolFromMolBlock(r.document_molblock)
    assert sorted(Chem.MolToSmiles(doc).split(".")) == sorted([Chem.CanonSmiles(F_ASPIRIN), "CCO"])


def test_edit_rejects_invalid_or_unchanged() -> None:
    assert edit(ASPIRIN, ASPIRIN).errors == ["the new structure is identical to the parent"]
    assert edit(ASPIRIN, "c1ccccc1(").ok is False
    assert edit("nonsense", "CCO").errors[0].startswith("parent:")


def test_selection_info_and_context() -> None:
    molfile, _ = _two_molecule_molfile()
    info = selection_info(molfile, [2, 3, 99])
    assert info == {"atoms": [2, 3], "elements": ["C", "O"], "fragment_smiles": "C=O"}
    block = snapshot_block(
        Snapshot.model_validate(
            {
                "active_tab": {
                    "id": "t",
                    "name": "x",
                    "smiles": [ASPIRIN, "CCO"],
                    "molfile": molfile,
                    "selection_atoms": [2, 3],
                }
            }
        )
    )
    assert "<selection>" in block and "2:C, 3:O" in block


# ---------------------------------------------------------------- tools through the loop


async def _active_tab(client: httpx.AsyncClient) -> dict:
    tab = (await client.get("/api/workspace")).json()["tabs"][0]
    molfile, _ = _two_molecule_molfile()
    return (
        await client.patch(
            f"/api/tabs/{tab['id']}",
            json={"revision": tab["revision"], "content": molfile, "content_format": "mol", "smiles": [ASPIRIN, "CCO"]},
        )
    ).json()


def _snapshot(tab: dict, revision: int | None = None) -> dict:
    molfile, _ = _two_molecule_molfile()
    return {
        "active_tab": {
            "id": tab["id"],
            "name": tab["name"],
            "revision": tab["revision"] if revision is None else revision,
            "smiles": [ASPIRIN, "CCO"],
            "molfile": molfile,
        },
        "tabs": [{"id": tab["id"], "name": tab["name"], "smiles": [ASPIRIN, "CCO"]}],
    }


def _script(client: httpx.AsyncClient, target: str) -> None:
    args = {"parent_smiles": ASPIRIN, "new_smiles": F_ASPIRIN, "description": "4-F aspirin", "target": target}
    client._transport.app.state.provider = FakeProvider(  # type: ignore[attr-defined]
        [[{"tool": "edit_structure", "args": args, "id": "e1"}], [{"text": "ok"}]]
    )


async def test_edit_opens_new_tab(client: httpx.AsyncClient) -> None:
    tab = await _active_tab(client)
    conv = (await client.get("/api/workspace")).json()["conversation_id"]
    _script(client, "new_tab")
    events = await chat(client, conv, "add F", _snapshot(tab))
    created = next(d for n, d in events if n == "tab.create")
    assert created["activate"] is True
    assert created["tab"]["source"] == "ai" and created["tab"]["name"] == "4-F aspirin"
    assert created["tab"]["origin_message_id"] == events[0][1]["message_id"]
    diff = next(d["artifact"] for n, d in events if n == "artifact")
    assert diff["type"] == "diff" and diff["payload"]["applied"] == "new_tab"
    assert diff["payload"]["after"]["formula"] == "C9H7FO4"
    tabs = (await client.get("/api/workspace")).json()["tabs"]
    assert len(tabs) == 2
    assert (await client.get(f"/api/tabs/{tab['id']}")).json()["revision"] == tab["revision"]  # untouched


async def test_replace_active_updates_only_the_parent(client: httpx.AsyncClient) -> None:
    tab = await _active_tab(client)
    conv = (await client.get("/api/workspace")).json()["conversation_id"]
    _script(client, "replace_active")
    events = await chat(client, conv, "replace it", _snapshot(tab))
    rep = next(d for n, d in events if n == "tab.replace")
    assert rep["tab_id"] == tab["id"] and rep["revision"] == tab["revision"] + 1
    stored = (await client.get(f"/api/tabs/{tab['id']}")).json()
    doc = Chem.MolFromMolBlock(stored["content"])
    assert sorted(Chem.MolToSmiles(doc).split(".")) == sorted([Chem.CanonSmiles(F_ASPIRIN), "CCO"])
    assert not any(n == "tab.create" for n, _ in events)


async def test_replace_falls_back_when_user_edited_meanwhile(client: httpx.AsyncClient) -> None:
    tab = await _active_tab(client)
    conv = (await client.get("/api/workspace")).json()["conversation_id"]
    _script(client, "replace_active")
    events = await chat(client, conv, "replace it", _snapshot(tab, revision=tab["revision"] - 1))
    names = [n for n, _ in events]
    assert "tab.replace" not in names
    rejected = next(d for n, d in events if n == "tab.replace_rejected")
    created = next(d for n, d in events if n == "tab.create")
    assert rejected["fallback_tab_id"] == created["tab"]["id"]
    result = next(d for n, d in events if n == "tool.result")
    assert result["summary"].startswith("opened in a new tab (active tab changed")


async def test_highlight_atoms(client: httpx.AsyncClient) -> None:
    tab = await _active_tab(client)
    conv = (await client.get("/api/workspace")).json()["conversation_id"]
    client._transport.app.state.provider = FakeProvider(  # type: ignore[attr-defined]
        [
            [{"tool": "highlight_atoms", "args": {"atoms": [3, 2, 99]}, "id": "h1"}],
            [{"tool": "highlight_atoms", "args": {"atoms": [3, 2]}, "id": "h2"}],
            [{"text": "there"}],
        ]
    )
    events = await chat(client, conv, "where?", _snapshot(tab))
    results = [d for n, d in events if n == "tool.result"]
    assert results[0]["ok"] is False and "out of range" in results[0]["summary"]
    hl = next(d for n, d in events if n == "highlight")
    assert hl == {"tab_id": tab["id"], "atoms": [2, 3]}


def test_before_and_after_share_the_layout() -> None:
    from chemomni.chem.depict import depict_molblock

    molfile, _ = _two_molecule_molfile()
    r = edit(ASPIRIN, F_ASPIRIN, molfile)
    before = Chem.MolFromMolBlock(r.parent_molblock)
    after = Chem.MolFromMolBlock(r.molblock)
    # same core atom (the acetyl methyl) sits at the same place on both sides
    assert abs(before.GetConformer().GetAtomPosition(0).x - after.GetConformer().GetAtomPosition(0).x) < 0.01
    svg = depict_molblock(r.molblock, 280, 200, tuple(r.changed_atoms))
    assert svg and "<svg" in svg
