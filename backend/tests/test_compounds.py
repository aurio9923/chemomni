"""Numbered compounds (PLAN Q57): labels in the KET, matching to molfile structures, ref arguments, searches.

Fixtures were captured from Ketcher 3.18 in a real browser:
- ``three_labelled.json``: ethanol, aniline and acetic acid with the numbers 1, 2, 3 written under them
  (the same layout Ketcher produced from 'CCO.c1ccccc1N.CC(=O)O') and a text "NaOH, rt" that is no label
- ``drawn_queries.json``: queries as Ketcher exports them (any atom, atom list, plain structure)
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import httpx
from rdkit import Chem

from chemomni.chem.compounds import (
    CompoundTable,
    assign_labels,
    build_compounds,
    number_compounds,
    read_ket,
)
from chemomni.chem.similarity import match_compounds
from chemomni.llm.context import Snapshot, snapshot_block
from chemomni.llm.fake_provider import FakeProvider
from tests.test_chat import chat

FIXTURES = Path(__file__).parent / "fixtures"
THREE = json.loads((FIXTURES / "three_labelled.json").read_text())
QUERIES = json.loads((FIXTURES / "drawn_queries.json").read_text())


def _ket(edit: Any = None, *, drop_labels: bool = False) -> str:
    doc = json.loads(THREE["ket"])
    if drop_labels:
        doc["root"]["nodes"] = [n for n in doc["root"]["nodes"] if n.get("type") != "text"]
    if edit:
        edit(doc)
    return json.dumps(doc)


def _texts(doc: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {n["paragraphs"][0]["parts"][0]["text"]: n for n in doc["root"]["nodes"] if n.get("type") == "text"}


def _table(ket: str | None = None) -> CompoundTable:
    return build_compounds(THREE["ket"] if ket is None else ket, THREE["mol"])


# ------------------------------------------------------------------------------------ reading


def test_the_fixture_is_read_as_three_molecules_and_four_texts() -> None:
    molecules, texts = read_ket(THREE["ket"])
    assert len(molecules) == 3
    assert [t.text for t in texts] == ["1", "2", "3", "NaOH, rt"]
    assert [t.label for t in texts] == ["1", "2", "3", None]  # an annotation is not a number


def test_unreadable_ket_gives_nothing() -> None:
    assert read_ket(None) == ([], [])
    assert read_ket("not json") == ([], [])
    assert read_ket('{"foo": 1}') == ([], [])


def test_numbers_attach_to_the_structure_above_them() -> None:
    table = _table()
    assert [(c.label, c.smiles) for c in table.compounds] == [
        ("1", "CCO"),
        ("2", "Nc1ccccc1"),
        ("3", "CC(=O)O"),
    ]
    assert table.notes == []


def test_label_spellings_and_refs() -> None:
    table = _table()
    for ref in ("2", "#2", "compound 2", "化合物2", "No. 2", " 2 "):
        compound, err = table.resolve(ref)
        assert err is None and compound is not None and compound.smiles == "Nc1ccccc1", ref
    for ref in ("", "two", "1 and 2"):
        assert table.resolve(ref)[0] is None


def test_unknown_number_lists_the_numbered_ones() -> None:
    compound, err = _table().resolve("9")
    assert compound is None and err is not None
    assert "numbered: 1, 2, 3" in err


def test_a_tab_without_numbers_tells_the_model_what_the_user_can_do() -> None:
    table = _table(_ket(drop_labels=True))
    assert [c.label for c in table.compounds] == [None, None, None]
    _, err = table.resolve("1")
    assert err is not None and "Number compounds" in err
    assert _table("").labelled == []  # no KET at all: structures are still listed


# ------------------------------------------------------------------------------- ambiguity


def test_a_number_between_two_structures_is_flagged() -> None:
    def between(doc: dict[str, Any]) -> None:
        texts = _texts(doc)
        doc["root"]["nodes"].remove(texts["2"])  # aniline loses its number ...
        texts["1"]["boundingBox"].update(x=7.7, y=-9.9)  # ... and ethanol's number sits halfway to it

    table = _table(_ket(between))
    assert table.notes == ["the number 1 lies between two structures; the nearest one was taken"]
    assert [c.label for c in table.compounds] == ["1", None, "3"]


def test_a_number_that_is_written_twice_is_flagged() -> None:
    def twice(doc: dict[str, Any]) -> None:
        _texts(doc)["3"]["paragraphs"][0]["parts"][0]["text"] = "1"

    table = _table(_ket(twice))
    assert table.notes == ["the number 1 is written next to several structures"]
    compound, err = table.resolve("1")
    assert compound is None and err is not None and "several" in err


def test_a_distant_number_belongs_to_nothing() -> None:
    def far(doc: dict[str, Any]) -> None:
        _texts(doc)["2"]["boundingBox"]["x"] = 60.0

    table = _table(_ket(far))
    assert [c.label for c in table.compounds] == ["1", None, "3"]
    assert table.notes == ["the number 2 is not next to any unnumbered structure"]


def test_a_second_number_next_to_a_numbered_structure_is_not_attached() -> None:
    def extra(doc: dict[str, Any]) -> None:
        doc["root"]["nodes"].append(copy.deepcopy(_texts(doc)["1"]))
        doc["root"]["nodes"][-1]["paragraphs"][0]["parts"][0]["text"] = "7"

    molecules, texts = read_ket(_ket(extra))
    result = assign_labels(molecules, texts)
    assert result.labels == ["1", "2", "3"]
    assert result.unattached == ["7"]


def test_structures_that_do_not_match_the_drawing_are_kept_without_numbers() -> None:
    def shift(doc: dict[str, Any]) -> None:
        for atom in doc["mol1"]["atoms"]:
            atom["location"][0] += 5.0

    table = _table(_ket(shift))
    assert sorted(c.smiles or "" for c in table.compounds) == ["CC(=O)O", "CCO", "Nc1ccccc1"]
    assert [c.label for c in table.compounds].count(None) == 1
    assert any("could not be matched" in n for n in table.notes)


# ----------------------------------------------------------------------- Number compounds


def test_number_compounds_goes_top_to_bottom_left_to_right() -> None:
    new, added = number_compounds(_ket(drop_labels=True))
    assert added == ["1", "2", "3"]
    table = _table(new)
    # the acetic acid is drawn highest (KET's y axis points up), then ethanol and aniline in one row
    assert {c.label: c.smiles for c in table.compounds} == {"1": "CC(=O)O", "2": "CCO", "3": "Nc1ccccc1"}
    assert table.notes == []


def test_number_compounds_keeps_existing_numbers_and_continues() -> None:
    def keep_only_aniline(doc: dict[str, Any]) -> None:
        texts = _texts(doc)
        texts["2"]["paragraphs"][0]["parts"][0]["text"] = "5"
        for k in ("1", "3"):
            doc["root"]["nodes"].remove(texts[k])

    new, added = number_compounds(_ket(keep_only_aniline))
    assert added == ["6", "7"]
    by_label = {c.label: c.smiles for c in _table(new).compounds}
    assert by_label["5"] == "Nc1ccccc1"
    assert set(by_label) == {"5", "6", "7"}


def test_number_compounds_is_idempotent() -> None:
    new, added = number_compounds(THREE["ket"])
    assert added == []
    assert json.loads(new) == json.loads(THREE["ket"])


# ------------------------------------------------------------------------- drawn queries


def _query(name: str) -> Chem.Mol:
    table = build_compounds(None, QUERIES[name]["mol"])
    assert len(table.compounds) == 1
    return table.compounds[0].mol


def _targets(*smiles: str) -> list[Any]:
    # unlabelled structures stacked in one molfile, as on a tab
    from chemomni.chem.layout import layout_smiles

    mols = [Chem.MolFromSmiles(s) for s in smiles]
    return build_compounds(None, layout_smiles(mols)).compounds


def _hits(query: Chem.Mol, *smiles: str) -> list[str]:
    targets = _targets(*smiles)
    rows = match_compounds(query, targets)
    return [t.smiles or "" for t, r in zip(targets, rows, strict=True) if r["match"]]


def test_a_plain_drawn_structure_is_a_substructure_query() -> None:
    aniline = _query("smiles")  # aniline as drawn
    assert _hits(aniline, "Nc1ccccc1", "CC(=O)Nc1ccccc1", "NC1CCCCC1", "c1ccccc1", "Oc1ccccc1") == [
        "Nc1ccccc1",
        "CC(=O)Nc1ccccc1",
    ]


def test_an_any_atom_in_the_drawing_matches_any_substituent() -> None:
    table = build_compounds(None, QUERIES["smarts_any"]["mol"])
    assert table.compounds[0].is_query and table.compounds[0].smiles is None
    assert table.compounds[0].shown.startswith("query ")
    query = table.compounds[0].mol
    # a ring with one substituent of any kind; benzene itself has none
    assert _hits(query, "Cc1ccccc1", "Clc1ccccc1", "c1ccccc1", "CCO") == ["Cc1ccccc1", "Clc1ccccc1"]


def test_an_atom_list_in_the_drawing_matches_either_element() -> None:
    query = _query("smarts_list")  # [N,O] on a six-membered ring
    hits = _hits(query, "Nc1ccccc1", "Oc1ccccc1", "Cc1ccccc1", "Clc1ccccc1")
    assert hits == ["Nc1ccccc1", "Oc1ccccc1"]


def test_a_query_target_is_reported_not_searched() -> None:
    from chemomni.chem.compounds import Compound

    drawn = build_compounds(None, QUERIES["smarts_any"]["mol"]).compounds[0]
    rows = match_compounds(Chem.MolFromSmiles("c"), [Compound(**{**drawn.__dict__, "index": 1})])
    assert rows[0]["ok"] is False and "query" in rows[0]["error"]


# ------------------------------------------------------------------------------ in the chat


def _snapshot(tab: dict[str, Any], *, ket: str | None = None) -> dict[str, Any]:
    smiles = [c.smiles for c in _table().compounds]
    return {
        "active_tab": {
            "id": tab["id"],
            "name": tab["name"],
            "revision": tab["revision"],
            "smiles": smiles,
            "molfile": THREE["mol"],
            "ket": THREE["ket"] if ket is None else ket,
        },
        "tabs": [{"id": tab["id"], "name": tab["name"], "smiles": smiles}],
    }


async def _tab(client: httpx.AsyncClient) -> dict[str, Any]:
    return next(t for t in (await client.get("/api/workspace")).json()["tabs"])


def test_the_snapshot_lists_numbered_compounds_for_the_model() -> None:
    snap = Snapshot.model_validate(_snapshot({"id": "t", "name": "T", "revision": 0}))
    block = snapshot_block(snap)
    assert "<compounds>" in block
    assert "[1] CCO" in block and "[2] Nc1ccccc1" in block and "[3] CC(=O)O" in block
    assert "NaOH" not in block  # annotations are not compounds
    plain = snapshot_block(Snapshot.model_validate(_snapshot({"id": "t", "name": "T", "revision": 0}, ket="")))
    assert "<compounds>" not in plain


async def test_compound_one_as_the_query_searches_the_others(client: httpx.AsyncClient) -> None:
    tab = await _tab(client)
    conv = (await client.get("/api/workspace")).json()["conversation_id"]
    client._transport.app.state.provider = FakeProvider()  # type: ignore[attr-defined]
    events = await chat(client, conv, "Use compound 1 as the query: substructure search of the others", _snapshot(tab))
    call = next(d for n, d in events if n == "tool.start")
    assert call["name"] == "substructure_search"
    assert call["arguments"] == {"query_ref": "1"}  # the model names a number, nothing else
    assert next(d for n, d in events if n == "tool.result")["summary"] == "1/2 match"  # CCO is inside acetic acid
    art = next(d["artifact"] for n, d in events if n == "artifact")
    assert art["type"] == "structure_set"
    assert art["payload"]["title"] == "Matches for compound 1"
    assert [i["label"] for i in art["payload"]["items"]] == ["query 1", "3"]
    text = "".join(d["delta"] for n, d in events if n == "text.delta")
    assert text == "1 of 2 structure(s) contain the query."


async def test_search_with_an_unknown_number_explains(client: httpx.AsyncClient) -> None:
    tab = await _tab(client)
    conv = (await client.get("/api/workspace")).json()["conversation_id"]
    client._transport.app.state.provider = FakeProvider()  # type: ignore[attr-defined]
    events = await chat(client, conv, "compound 9 substructure", _snapshot(tab))
    summary = next(d for n, d in events if n == "tool.result")["summary"]
    assert "no structure is numbered 9" in summary and "numbered: 1, 2, 3" in summary


async def test_refs_replace_copied_smiles(client: httpx.AsyncClient) -> None:
    tab = await _tab(client)
    conv = (await client.get("/api/workspace")).json()["conversation_id"]
    script = [
        [
            {"tool": "compute_properties", "args": {"refs": ["3", "1"]}, "id": "p"},
            {"tool": "similarity", "args": {"ref_a": "1", "ref_b": "3"}, "id": "s"},
            {"tool": "substructure_search", "args": {"smarts": "[OX2H]"}, "id": "q"},
        ],
        [{"text": "done"}],
    ]
    client._transport.app.state.provider = FakeProvider(script)  # type: ignore[attr-defined]
    events = await chat(client, conv, "numbers", _snapshot(tab))
    arts = [d["artifact"] for n, d in events if n == "artifact"]
    rows = arts[0]["payload"]["rows"]
    assert [(r["ref"], r["smiles"]) for r in rows] == [("3", "CC(=O)O"), ("1", "CCO")]
    assert [i["label"] for i in arts[1]["payload"]["items"]] == ["1", "3"]
    # no smiles and no refs: every structure of the tab is searched, hits keep their numbers
    hits = [i["label"] for i in arts[2]["payload"]["items"]]
    assert hits == ["1", "3"]


async def test_edit_structure_takes_a_number(client: httpx.AsyncClient) -> None:
    tab = await _tab(client)
    conv = (await client.get("/api/workspace")).json()["conversation_id"]
    args = {"parent_ref": "1", "new_smiles": "CCCO", "description": "propanol", "target": "new_tab"}
    script = [[{"tool": "edit_structure", "args": args, "id": "e"}], [{"text": "ok"}]]
    client._transport.app.state.provider = FakeProvider(script)  # type: ignore[attr-defined]
    events = await chat(client, conv, "edit 1", _snapshot(tab))
    # the tool must succeed as a whole: a failure after the tab was created made the model retry
    # and open the same edit again and again (seen in real use)
    assert next(d for n, d in events if n == "tool.result")["ok"] is True
    assert sum(1 for n, _ in events if n == "tab.create") == 1
    diff = next(d["artifact"] for n, d in events if n == "artifact")
    assert diff["type"] == "diff" and diff["payload"]["before"]["smiles"] == "CCO"


async def test_number_compounds_endpoint(client: httpx.AsyncClient) -> None:
    r = await client.post("/api/chem/number-compounds", json={"ket": _ket(drop_labels=True)})
    assert r.status_code == 200
    body = r.json()
    assert body["added"] == ["1", "2", "3"]
    assert {c.label for c in _table(body["ket"]).compounds} == {"1", "2", "3"}
