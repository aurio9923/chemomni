from __future__ import annotations

import itertools
import json
import math
import time

import httpx
import pytest
from rdkit import Chem
from rdkit.Chem import rdDepictor

from chemomni.chem.edit import _mean_bond_length
from chemomni.chem.generate import evaluate
from chemomni.chem.layout import bbox, grid, layout_smiles, split_molfile
from chemomni.chem.names import NameResolver
from chemomni.llm.fake_provider import FakeProvider
from tests.test_chat import chat

ASPIRIN = "CC(=O)Oc1ccccc1C(=O)O"
ANALOGS = [
    ("CC(=O)Oc1ccc(F)cc1C(=O)O", "4-F"),
    ("CC(=O)Oc1ccccc1C(N)=O", "amide"),
    ("CCC(=O)Oc1ccccc1C(=O)O", "propionyl"),
    ("CC(=O)Oc1ccc(Cl)cc1C(=O)O", "4-Cl"),
    ("CC(=O)Oc1cccnc1C(=O)O", "aza"),
]
BENZENE_RING = Chem.MolFromSmarts("c1ccccc1")


def _mol(smiles: str) -> Chem.Mol:
    m = Chem.MolFromSmiles(smiles)
    rdDepictor.Compute2DCoords(m)
    return m


def _components(molblock: str) -> list[Chem.Mol]:
    return list(Chem.GetMolFrags(Chem.MolFromMolBlock(molblock), asMols=True))


# ---------------------------------------------------------------- layout


def test_grid_rows_and_columns_do_not_overlap() -> None:
    mols = [_mol(s) for s, _ in ANALOGS]
    combined = grid(mols)
    parts = list(Chem.GetMolFrags(combined, asMols=True))
    assert len(parts) == 5
    boxes = [bbox(p) for p in parts]
    for i, a in enumerate(boxes):
        for b in boxes[i + 1 :]:
            separated = a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1]
            assert separated, (a, b)
    # 5 molecules -> 3 columns: the 4th starts the second row, below the first
    centre = [((b[0] + b[2]) / 2, (b[1] + b[3]) / 2) for b in boxes]
    assert centre[1][0] > centre[0][0] and centre[2][0] > centre[1][0]
    assert centre[3][1] < centre[0][1]
    assert math.isclose(centre[3][0], centre[0][0], abs_tol=1.5)


def test_layout_smiles_uses_ketcher_bond_length() -> None:
    block = layout_smiles([_mol(ASPIRIN), _mol("CCO")])
    assert math.isclose(_mean_bond_length(Chem.MolFromMolBlock(block)), 1.0, abs_tol=0.05)


def test_split_keeps_what_the_user_drew() -> None:
    two = Chem.CombineMols(Chem.AddHs(_mol("CCO"), addCoords=True), _mol("c1ccccc1"))
    parts = split_molfile(Chem.MolToV3KMolBlock(two))
    assert [s for _, s in parts] == ["CCO", "c1ccccc1"]
    first = Chem.MolFromMolBlock(parts[0][0], removeHs=False)
    assert first.GetNumAtoms() == 9  # explicit hydrogens survive
    x0, y0, x1, y1 = bbox(Chem.MolFromMolBlock(parts[1][0]))
    assert abs((x0 + x1) / 2) < 1e-3 and abs((y0 + y1) / 2) < 1e-3  # moved to the origin


# ---------------------------------------------------------------- evaluation


def test_evaluate_rejects_with_reasons_and_keeps_filters_for_the_table() -> None:
    pains = "O=C1C=CC(=O)C=C1"  # quinone: alerts, but still accepted
    r = evaluate(
        ASPIRIN,
        [*ANALOGS[:2], (ASPIRIN, "same"), (ANALOGS[0][0], "again"), ("C1CC", "broken"), (pains, "quinone")],
    )
    assert r.ok and [a.index for a in r.accepted] == [1, 2, 3]
    assert [x["reason"] for x in r.rejected] == [
        "identical to the parent",
        "duplicate of #1",
        "cannot parse SMILES: C1CC",
    ]
    quinone = r.accepted[2]
    assert quinone.props["brenk_alerts"] or quinone.props["pains_alerts"]
    assert 0 < r.accepted[0].similarity < 1
    assert len(r.accepted[0].changed_atoms) == 1  # just the fluorine


def test_evaluate_without_valid_candidates() -> None:
    r = evaluate(ASPIRIN, [("C1CC", "x"), (ASPIRIN, "same")])
    assert not r.ok and r.errors == ["no valid new structures"] and len(r.rejected) == 2
    assert evaluate("nonsense", ANALOGS).errors[0].startswith("parent:")


def _ring_coords(mol: Chem.Mol) -> list[tuple[float, float]]:
    conf = mol.GetConformer()
    match = mol.GetSubstructMatch(BENZENE_RING)
    return sorted((round(conf.GetAtomPosition(i).x, 2), round(conf.GetAtomPosition(i).y, 2)) for i in match)


def test_analogs_share_the_parents_drawing() -> None:
    # the user's aspirin, drawn at Ketcher's scale and rotated by 90 degrees
    parent = _mol(ASPIRIN)
    conf = parent.GetConformer()
    for i in range(parent.GetNumAtoms()):
        p = conf.GetAtomPosition(i)
        conf.SetAtomPosition(i, (-p.y / 1.5, p.x / 1.5, 0))
    r = evaluate(ASPIRIN, ANALOGS[:4], Chem.MolToV3KMolBlock(parent))
    reference = _ring_coords(Chem.MolFromMolBlock(r.parent_molblock))
    for a in r.accepted:
        assert _ring_coords(Chem.MolFromMolBlock(a.molblock)) == reference, a.rationale
    # on the grid, each cell's ring is the first cell's ring moved by one vector (same spot per cell)
    rings = [_ring_coords(c) for c in _components(r.grid_molblock)]
    assert len(rings) == 4
    for ring in rings[1:]:
        shifts = {(round(b[0] - a[0], 1), round(b[1] - a[1], 1)) for a, b in zip(rings[0], ring, strict=True)}
        assert len(shifts) == 1, shifts
    assert math.isclose(_mean_bond_length(Chem.MolFromMolBlock(r.grid_molblock)), 1.0, abs_tol=0.05)


def test_twenty_candidates_fit_the_tool_budget() -> None:
    subs = ["F", "Cl", "Br", "C", "O", "N", "C#N", "OC", "CF", "C(F)(F)F"]
    cands = [(f"CC(=O)Oc1cc({s})ccc1C(=O)O", s) for s in subs] + [(f"CC(=O)Oc1ccc({s})cc1C(=O)O", s) for s in subs]
    t0 = time.perf_counter()
    r = evaluate(ASPIRIN, cands)
    assert r.ok and len(r.accepted) == 20
    assert time.perf_counter() - t0 < 10


# ---------------------------------------------------------------- tools through the loop


async def _aspirin_tab(client: httpx.AsyncClient) -> dict:
    tab = (await client.get("/api/workspace")).json()["tabs"][0]
    return (
        await client.patch(
            f"/api/tabs/{tab['id']}",
            json={"revision": tab["revision"], "smiles": [ASPIRIN]},
        )
    ).json()


def _snapshot(tab: dict) -> dict:
    return {
        "active_tab": {"id": tab["id"], "name": tab["name"], "revision": tab["revision"], "smiles": [ASPIRIN]},
        "tabs": [{"id": tab["id"], "name": tab["name"], "smiles": [ASPIRIN]}],
    }


def _provider(client: httpx.AsyncClient, script: list) -> FakeProvider:
    provider = FakeProvider(script)
    client._transport.app.state.provider = provider  # type: ignore[attr-defined]
    return provider


async def test_propose_structures_opens_a_grid_tab(client: httpx.AsyncClient) -> None:
    tab = await _aspirin_tab(client)
    conv = (await client.get("/api/workspace")).json()["conversation_id"]
    provider = FakeProvider()  # rule-based: "analogs" -> 5 valid + duplicate + parent + invalid
    client._transport.app.state.provider = provider  # type: ignore[attr-defined]
    events = await chat(client, conv, "suggest 5 analogs", _snapshot(tab))

    created = next(d for n, d in events if n == "tab.create")
    assert created["activate"] and created["tab"]["name"] == "Analogs" and created["tab"]["source"] == "ai"
    assert len(created["tab"]["smiles_cache"]) == 5
    assert len(_components(created["tab"]["content"])) == 5

    grid_art = next(d["artifact"] for n, d in events if n == "artifact")
    assert grid_art["type"] == "grid"
    p = grid_art["payload"]
    assert [i["index"] for i in p["items"]] == [1, 2, 3, 4, 5]
    assert all(i["svg"].startswith("<?xml") or "<svg" in i["svg"] for i in p["items"])
    assert p["parent"]["smiles"] == Chem.CanonSmiles(ASPIRIN)
    assert {x["reason"] for x in p["rejected"]} == {
        "duplicate of #1",
        "identical to the parent",
        "cannot parse SMILES: C1CC",
    }

    # the LLM gets a compact payload: no SVG, no molfile
    tool_msg = next(m for m in provider.calls[1]["messages"] if m["role"] == "tool")
    assert "<svg" not in tool_msg["content"] and "M  V30" not in tool_msg["content"]
    payload = json.loads(tool_msg["content"])
    assert set(payload["accepted"][0]) >= {"n", "smiles", "similarity", "mw", "qed"}

    result = next(d for n, d in events if n == "tool.result")
    assert result["summary"] == "5 accepted, 3 rejected"
    assert "Proposed 5 analog(s)" in "".join(d["delta"] for n, d in events if n == "text.delta")


async def test_propose_without_valid_candidates_creates_no_tab(client: httpx.AsyncClient) -> None:
    tab = await _aspirin_tab(client)
    conv = (await client.get("/api/workspace")).json()["conversation_id"]
    args = {"parent_smiles": ASPIRIN, "candidates": [{"smiles": "C1CC", "rationale": "x"}], "title": "None"}
    _provider(client, [[{"tool": "propose_structures", "args": args, "id": "p1"}], [{"text": "sorry"}]])
    events = await chat(client, conv, "analogs", _snapshot(tab))
    assert not any(n == "tab.create" for n, _ in events)
    result = next(d for n, d in events if n == "tool.result")
    assert result["ok"] is False and result["summary"] == "no valid new structures"


async def test_open_in_new_tab(client: httpx.AsyncClient) -> None:
    tab = await _aspirin_tab(client)
    conv = (await client.get("/api/workspace")).json()["conversation_id"]
    args = {"smiles": ["CCO", "OCC", "c1ccccc1", "bad("], "title": "Solvents"}
    _provider(client, [[{"tool": "open_in_new_tab", "args": args, "id": "o1"}], [{"text": "opened"}]])
    events = await chat(client, conv, "open them", _snapshot(tab))
    created = next(d for n, d in events if n == "tab.create")
    assert created["tab"]["smiles_cache"] == ["CCO", "c1ccccc1"]  # deduplicated, invalid dropped
    art = next(d["artifact"] for n, d in events if n == "artifact")
    assert art["type"] == "structure_set" and art["payload"]["tab_id"] == created["tab"]["id"]
    assert next(d for n, d in events if n == "tool.result")["summary"] == "opened 2 molecule(s)"


# ---------------------------------------------------------------- split


async def test_split_tab(client: httpx.AsyncClient) -> None:
    block = layout_smiles([_mol(ASPIRIN), _mol("CCO"), _mol("c1ccccc1")])
    src = (
        await client.post("/api/tabs", json={"name": "Set", "content_format": "mol", "content": block, "smiles": ["x"]})
    ).json()
    r = await client.post(f"/api/tabs/{src['id']}/split")
    assert r.status_code == 201
    parts = r.json()
    assert [t["name"] for t in parts] == ["Set #1", "Set #2", "Set #3"]
    assert [t["smiles_cache"] for t in parts] == [[Chem.CanonSmiles(ASPIRIN)], ["CCO"], ["c1ccccc1"]]
    assert (await client.get(f"/api/tabs/{src['id']}")).json()["revision"] == src["revision"]  # kept as is

    single = (await client.post(f"/api/tabs/{parts[1]['id']}/split")).status_code
    assert single == 400


async def test_split_ket_tab_uses_the_editor_molfile_or_smiles(client: httpx.AsyncClient) -> None:
    src = (
        await client.post(
            "/api/tabs", json={"name": "K", "content_format": "ket", "content": "{}", "smiles": ["CCO", "c1ccccc1"]}
        )
    ).json()
    from_smiles = (await client.post(f"/api/tabs/{src['id']}/split")).json()
    assert [t["smiles_cache"] for t in from_smiles] == [["CCO"], ["c1ccccc1"]]
    molfile = layout_smiles([_mol("CN"), _mol("CCl"), _mol("CBr")])
    from_editor = (await client.post(f"/api/tabs/{src['id']}/split", json={"molfile": molfile})).json()
    assert [t["smiles_cache"] for t in from_editor] == [["CN"], ["CCl"], ["CBr"]]


# ---------------------------------------------------------------- names


def _pubchem(handler) -> httpx.AsyncClient:  # type: ignore[no-untyped-def]
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.fixture
def no_opsin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("chemomni.chem.names.opsin_available", lambda: False)


async def test_pubchem_lookup(no_opsin: None) -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if "aspirin" in request.url.path.lower():
            body = {"PropertyTable": {"Properties": [{"CID": 2244, "SMILES": ASPIRIN, "IUPACName": "x"}]}}
            return httpx.Response(200, json=body)
        if "legacy" in request.url.path:
            body = {"PropertyTable": {"Properties": [{"CID": 1, "IsomericSMILES": "CCO"}]}}
            return httpx.Response(200, json=body)
        return httpx.Response(404, json={"Fault": {"Code": "PUGREST.NotFound"}})

    resolver = NameResolver(client=_pubchem(handler))
    r = await resolver.resolve("Aspirin")
    assert r == {
        "ok": True,
        "name": "Aspirin",
        "smiles": Chem.CanonSmiles(ASPIRIN),
        "source": "pubchem",
        "cid": 2244,
        "iupac_name": "x",
    }
    assert "/compound/name/Aspirin/property/SMILES,IUPACName/JSON" in seen[0]
    assert (await resolver.resolve("legacy name"))["smiles"] == "CCO"
    assert "%20" in seen[-1]  # names are URL-encoded
    missing = await resolver.resolve("no such thing")
    assert missing["ok"] is False and missing["errors"][0] == "no structure found for 'no such thing'"
    await resolver.resolve("aspirin")  # cached (case-insensitive): no new request
    assert len(seen) == 3


async def test_pubchem_is_rate_limited(no_opsin: None) -> None:
    times: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        times.append(time.monotonic())
        return httpx.Response(404)

    resolver = NameResolver(client=_pubchem(handler))
    for name in ("a1", "a2", "a3"):
        await resolver.resolve(name)
    assert all(b - a >= 0.19 for a, b in itertools.pairwise(times))


async def test_pubchem_disabled_or_unreachable(no_opsin: None) -> None:
    off = await NameResolver(pubchem=False).resolve("aspirin")
    assert off["ok"] is False and any("CHEMOMNI_PUBCHEM" in e for e in off["errors"])

    def broken(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline")

    down = await NameResolver(client=_pubchem(broken)).resolve("aspirin")
    assert down["ok"] is False and "PubChem request failed: ConnectError" in down["errors"]


@pytest.mark.skipif(
    not __import__("chemomni.chem.names", fromlist=["opsin_available"]).opsin_available(),
    reason="OPSIN (py2opsin + Java) not installed",
)
async def test_opsin_resolves_systematic_names_offline() -> None:
    def no_network(request: httpx.Request) -> httpx.Response:
        raise AssertionError("PubChem must not be called")

    r = await NameResolver(client=_pubchem(no_network)).resolve("2-acetyloxybenzoic acid")
    assert r["ok"] and r["source"] == "opsin" and r["smiles"] == Chem.CanonSmiles(ASPIRIN)


async def test_name_to_structure_tool(client: httpx.AsyncClient, no_opsin: None) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"PropertyTable": {"Properties": [{"CID": 702, "SMILES": "CCO"}]}})

    app = client._transport.app  # type: ignore[attr-defined]
    app.state.services["names"] = NameResolver(client=_pubchem(handler))
    tab = await _aspirin_tab(client)
    conv = (await client.get("/api/workspace")).json()["conversation_id"]
    _provider(client, [[{"tool": "name_to_structure", "args": {"name": "ethanol"}, "id": "n1"}], [{"text": "ok"}]])
    events = await chat(client, conv, "what is ethanol", _snapshot(tab))
    result = next(d for n, d in events if n == "tool.result")
    assert result["ok"] and result["summary"] == "pubchem: ethanol"
    art = next(d["artifact"] for n, d in events if n == "artifact")
    assert art["payload"]["items"][0]["smiles"] == "CCO"
