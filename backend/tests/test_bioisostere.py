"""Bioisostere replacement (M5): the engine and the two tools, against molecules written down by hand.

The expected structures here come from the literature or from drawing the product by hand, never from running
a rule, so a mistake in a rule cannot make its own test pass (the azilsartan lesson). The rule table itself is
checked in ``test_rule_table.py``.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from rdkit import Chem
from rdkit.Chem import rdDepictor
from rdkit.Chem.MolStandardize import rdMolStandardize

from chemomni.chem import bioisostere as bio
from chemomni.chem.bioisostere_rules import rules
from chemomni.chem.scan import target_from_selection
from chemomni.llm.fake_provider import FakeProvider
from tests.test_chat import chat, use_script
from tests.test_compounds import _snapshot as three_snapshot
from tests.test_compounds import _tab as first_tab
from tests.test_rule_table import EXCLUDED

TABLE = rules()
ENUMERATOR = rdMolStandardize.TautomerEnumerator()

TEREPHTHALIC = "OC(=O)c1ccc(cc1)C(=O)O"
TWO_ACIDS = "OC(=O)Cc1ccc(cc1)C(=O)O"  # atoms 1-3 are the aliphatic acid, 11-13 the aromatic one
PRECURSOR = "CCOC1=NC2=CC=CC(C(=O)O)=C2N1CC1=CC=C(C=C1)C1=CC=CC=C1C(=O)O"  # azilsartan with both acids free
AZILSARTAN = "CCOC1=NC2=CC=CC(=C2N1CC3=CC=C(C=C3)C4=CC=CC=C4C5=NOC(=O)N5)C(=O)O"
ATORVASTATIN = "CC(C)c1c(C(=O)Nc2ccccc2)c(-c2ccccc2)c(-c2ccc(F)cc2)n1CCC(O)CC(O)CC(=O)O"


def same(a: str, b: str) -> bool:
    """The same molecule, whichever tautomer is written."""

    def key(s: str) -> str:
        mol = Chem.MolFromSmiles(s)
        assert mol is not None, s
        return Chem.MolToSmiles(ENUMERATOR.Canonicalize(mol))

    return key(a) == key(b)


def products(rule_id: str, smiles: str, atoms: list[int] | None = None) -> list[bio.Candidate]:
    """What ``apply_bioisosteres`` would show for one rule on a molecule (all atoms, or only ``atoms``)."""
    target = _target(smiles, atoms)
    result = bio.apply(target, [_rule(rule_id)])
    assert result.ok, result.errors
    return result.candidates


def _rule(rule_id: str):  # type: ignore[no-untyped-def]
    rule = TABLE.get(rule_id)
    assert rule is not None, rule_id
    return rule


def _molfile(smiles: str) -> str:
    mol = Chem.MolFromSmiles(smiles)
    rdDepictor.Compute2DCoords(mol)
    return Chem.MolToV3KMolBlock(mol)


def _target(smiles: str, atoms: list[int] | None = None) -> bio.Target:
    if atoms is None:
        target, err = bio.whole_molecule(smiles)
    else:
        target, err = target_from_selection(_molfile(smiles), atoms)
    assert target is not None, err
    return target


def _matches(rule_id: str, smiles: str, atoms: list[int] | None = None) -> bio.RuleRun:
    target = _target(smiles, atoms)
    work = bio.neutralised(target.mol)
    return bio.run_rule(_rule(rule_id), target, work, ENUMERATOR)


# ---------------------------------------------------------------- the engine against known molecules


def test_the_acid_next_to_the_benzimidazole_is_kept_and_azilsartan_is_found() -> None:
    found = products("cooh_to_oxadiazolone", PRECURSOR)
    assert len(found) == 2
    assert any(same(c.smiles, AZILSARTAN) for c in found)  # the drug itself
    other = "CCOC1=NC2=CC=CC(=C2N1CC3=CC=C(C=C3)C4=CC=CC=C4C(=O)O)C5=NOC(=O)N5"  # the other acid converted
    assert any(same(c.smiles, other) for c in found)


def test_two_symmetric_acids_give_one_product() -> None:
    run = _matches("cooh_to_tetrazole", TEREPHTHALIC)
    assert run.positions == 2 and len(run.products) == 1 and run.products[0].positions == 2
    found = products("cooh_to_tetrazole", TEREPHTHALIC)
    assert len(found) == 1 and same(found[0].smiles, "OC(=O)c1ccc(cc1)-c1nn[nH]n1")
    assert "2 equivalent sites" in found[0].rationale


def test_a_carboxylate_and_an_acid_give_the_same_products() -> None:
    for rule_id in (r.id for r in TABLE.of_family("carboxylic_acid_isosteres")):
        acid = {c.smiles for c in products(rule_id, "OC(=O)c1ccccc1")}
        anion = {c.smiles for c in products(rule_id, "[O-]C(=O)c1ccccc1")}
        assert acid == anion, rule_id
    zwitterion = products("cooh_to_tetrazole", "[NH3+]CC(=O)[O-]")  # glycine as it is drawn at pH 7
    assert len(zwitterion) == 1 and same(zwitterion[0].smiles, "NCc1nn[nH]n1")  # neutral products


def test_a_selection_limits_the_places() -> None:
    aliphatic, aromatic = [1, 2, 3], [11, 12, 13]
    assert len(products("cooh_to_tetrazole", TWO_ACIDS)) == 2
    only_aliphatic = products("cooh_to_tetrazole", TWO_ACIDS, aliphatic)
    assert len(only_aliphatic) == 1 and same(only_aliphatic[0].smiles, "OC(=O)c1ccc(cc1)Cc1nn[nH]n1")
    only_aromatic = products("cooh_to_tetrazole", TWO_ACIDS, aromatic)
    assert len(only_aromatic) == 1 and same(only_aromatic[0].smiles, "OC(=O)Cc1ccc(cc1)-c1nn[nH]n1")
    # the acid plus the ring it sits on is a larger selection that still holds the whole acid
    assert len(products("cooh_to_tetrazole", TWO_ACIDS, [*aromatic, 5, 6, 7, 8, 9, 10])) == 1


def test_a_selection_that_misses_part_of_the_group_fits_nothing_and_says_why() -> None:
    target = _target(TWO_ACIDS, [1, 2])  # the carbon and one oxygen, not the whole acid
    result = bio.apply(target, [_rule("cooh_to_tetrazole")])
    assert not result.ok and "only outside the selected atoms" in result.errors[0]
    assert result.runs[0].positions == 0 and result.runs[0].outside >= 1


def test_the_selected_part_decides_which_rules_fit() -> None:
    fits = {r.rule.id for r in bio.find(_target(TWO_ACIDS, [1, 2, 3]), TABLE.active) if r.matches}
    assert "cooh_to_tetrazole" in fits and "phenyl_to_alpha_thienyl" not in fits
    whole = {r.rule.id for r in bio.find(_target(TWO_ACIDS), TABLE.active) if r.matches}
    assert "para_phenylene_to_bcp" in whole  # the ring is only reachable when the ring is in the selection


def test_excluded_rules_are_never_offered_even_when_they_fit() -> None:
    molecule = "OC(=O)c1ccc(cc1)C(=O)NC"  # an acid, an amide and a para-phenylene: every excluded rule fits
    target = _target(molecule)
    work = bio.neutralised(target.mol)
    for rule in TABLE.everything:
        if rule.id in EXCLUDED:
            assert bio.run_rule(rule, target, work, ENUMERATOR).positions >= 1, rule.id  # the premise
    offered = {r.rule.id for r in bio.find(target, TABLE.active)}
    assert offered and not offered & EXCLUDED
    applied = bio.apply(target, list(TABLE.active))
    assert not {i for c in applied.candidates for i in c.rule_ids} & EXCLUDED


def test_at_most_twenty_products_are_shown_and_every_rule_gets_a_turn() -> None:
    target = _target(ATORVASTATIN)
    applied = bio.apply(target, list(TABLE.active))
    assert applied.ok and applied.total > bio.MAX_CANDIDATES
    assert len(applied.candidates) == bio.MAX_CANDIDATES
    assert len({c.smiles for c in applied.candidates}) == bio.MAX_CANDIDATES
    first_of_each_rule = {c.rule_ids[0] for c in applied.candidates}
    assert len(first_of_each_rule) == bio.MAX_CANDIDATES  # not twenty products of the first rule
    assert all(len(c.rationale) <= bio.MAX_RATIONALE for c in applied.candidates)


def test_one_molecule_from_two_rules_is_shown_once_even_as_another_tautomer() -> None:
    """The same tetrazole written as the 2H and as the 1H tautomer by two rules is one molecule."""
    first = _rule("cooh_to_tetrazole")
    second = first.model_copy(
        update={"id": "cooh_to_tetrazole_1h", "reaction": "[#6:1][CX3](=O)[OX2H1]>>[#6:1]c1nnn[nH]1"}
    )
    assert Chem.CanonSmiles("Cc1nn[nH]n1") != Chem.CanonSmiles("Cc1nnn[nH]1")  # written differently
    applied = bio.apply(_target("CC(=O)O"), [first, second])
    assert applied.ok and len(applied.candidates) == 1
    candidate = applied.candidates[0]
    assert candidate.rule_ids == ["cooh_to_tetrazole", "cooh_to_tetrazole_1h"]
    assert candidate.rationale.endswith("also cooh_to_tetrazole_1h)") and same(candidate.smiles, "Cc1nn[nH]n1")


def test_a_new_stereocentre_is_announced() -> None:
    racemic = [r for r in TABLE.active if r.creates_stereocenter]
    assert racemic, "the table should have rules that create a stereocentre"
    rule = racemic[0]
    example = rule.tests.positive[0].parent
    applied = bio.apply(_target(example), [rule])
    assert applied.ok and all("new stereocentre" in c.rationale for c in applied.candidates)


def test_a_salt_is_not_guessed_at() -> None:
    target, err = bio.whole_molecule("[Na+].[O-]C(=O)c1ccccc1")
    assert target is None and err is not None and "several components" in err


def test_a_p_halophenyl_never_becomes_a_bridgehead_halide() -> None:
    """User decision of 2026-11-08: the cage rules do not take a halogen as an attachment atom."""
    anilide = "Cc1ccc(C(=O)Nc2ccc(Cl)cc2)cc1"  # only the tolyl ring can be replaced
    expected = {
        "para_phenylene_to_bcp": "CC12CC(C1)(C2)C(=O)Nc1ccc(Cl)cc1",
        "para_phenylene_to_bco": "CC12CCC(CC1)(CC2)C(=O)Nc1ccc(Cl)cc1",
    }
    for rule_id, product in expected.items():
        found = products(rule_id, anilide)
        assert [Chem.CanonSmiles(c.smiles) for c in found] == [Chem.CanonSmiles(product)], rule_id
        assert _matches(rule_id, "Cc1ccc(F)cc1").positions == 0, rule_id  # a halogen on the far side: no fit


def test_a_halothiophene_is_still_offered_for_a_p_halophenyl() -> None:
    """Not covered by the restriction on purpose: 5-chlorothiophene is a common motif (PLAN Q62)."""
    found = products("para_phenylene_to_thiophenediyl", "Cc1ccc(Cl)cc1")
    assert [Chem.CanonSmiles(c.smiles) for c in found] == [Chem.CanonSmiles("Cc1ccc(Cl)s1")]


@pytest.mark.parametrize("drawn", ["Cc1cc(-c2ccccc2)[nH]n1", "Cc1cc(-c2ccccc2)n[nH]1"])
def test_pyrazole_gives_both_isoxazole_regioisomers_whichever_tautomer_is_drawn(drawn: str) -> None:
    """User decision of 2026-11-08. Written by hand: 3-methyl-5-phenyl and 5-methyl-3-phenyl isoxazole."""
    found = products("pyrazole_to_isoxazole", drawn)
    assert {Chem.CanonSmiles(c.smiles) for c in found} == {
        Chem.CanonSmiles("Cc1cc(-c2ccccc2)on1"),
        Chem.CanonSmiles("Cc1cc(-c2ccccc2)no1"),
    }
    assert sorted(c.rationale.split(" (")[1] for c in found) == ["1 of 2)", "2 of 2)"]


def test_pyrazole_edge_cases() -> None:
    assert [c.smiles for c in products("pyrazole_to_isoxazole", "Cc1cc(C)[nH]n1")] == [Chem.CanonSmiles("Cc1cc(C)no1")]
    assert _matches("pyrazole_to_isoxazole", "Cc1cc(-c2ccccc2)n(C)n1").positions == 0  # N-substituted: no fit
    assert _matches("pyrazole_to_isoxazole", "Cc1cc(-c2ccccc2)on1").positions == 0  # already an isoxazole


def test_a_rule_that_fits_nothing_says_how_to_find_one() -> None:
    result = bio.apply(_target("c1ccccc1O"), [_rule("cooh_to_tetrazole")])
    assert not result.ok and "find_bioisosteres" in result.errors[0]


# ---------------------------------------------------------------- the tools through the chat loop


def _drawn_snapshot(tab: dict[str, Any], smiles: str, selected: list[int] | None = None) -> dict[str, Any]:
    return {
        "active_tab": {
            "id": tab["id"],
            "name": tab["name"],
            "revision": tab["revision"],
            "smiles": [smiles],
            "molfile": _molfile(smiles),
            "selection_atoms": selected or [],
        }
    }


def _sent_back(provider: FakeProvider, turn: int) -> dict[str, Any]:
    """The result of the tool call as the model received it, on its ``turn``-th request."""
    last = provider.calls[turn]["messages"][-1]
    assert last["role"] == "tool"
    return json.loads(last["content"])


async def _ask(client: httpx.AsyncClient, text: str, snapshot: dict[str, Any]) -> list[tuple[str, Any]]:
    conv = (await client.get("/api/workspace")).json()["conversation_id"]
    return await chat(client, conv, text, snapshot)


async def test_compound_three_acid_to_bioisosteres_through_the_chat_loop(client: httpx.AsyncClient) -> None:
    """The acceptance example: "compound 3's carboxylic acid -> bioisosteres" (3 is acetic acid on this tab)."""
    client._transport.app.state.provider = FakeProvider()  # type: ignore[attr-defined]
    events = await _ask(client, "化合物 3 のカルボン酸をバイオアイソスターに", three_snapshot(await first_tab(client)))

    calls = [d for n, d in events if n == "tool.start"]
    assert [c["name"] for c in calls] == ["find_bioisosteres", "apply_bioisosteres"]
    assert calls[0]["arguments"] == {"ref": "3", "family": "carboxylic_acid_isosteres"}
    applied = calls[1]["arguments"]
    assert applied["ref"] == "3" and all(i in {r.id for r in TABLE.active} for i in applied["rule_ids"])
    assert set(applied) == {"rule_ids", "ref", "title"}  # ids only: no SMARTS, no SMILES

    created = next(d for n, d in events if n == "tab.create")
    assert sorted(created["tab"]["smiles_cache"]) == sorted(
        Chem.CanonSmiles(s)
        for s in ("Cc1nn[nH]n1", "CC(=O)NS(C)(=O)=O", "CC(=O)NS(=O)(=O)C1CC1")  # tetrazole, two acyl sulfonamides
    )
    art = next(d["artifact"] for n, d in events if n == "artifact")["payload"]
    assert art["parent"]["smiles"] == "CC(=O)O"
    assert [i["rationale"].split(":")[0] for i in art["items"]] == applied["rule_ids"]
    assert all(": " in i["rationale"] and i["svg"] and i["similarity"] is not None for i in art["items"])
    summaries = [d["summary"] for n, d in events if n == "tool.result"]
    assert summaries == ["9 rule(s) fit", "3 product(s)"]  # nine acid rules fit acetic acid; the fake applies three


async def test_the_model_sees_risks_and_never_confidence(client: httpx.AsyncClient) -> None:
    provider = FakeProvider(
        [
            [{"tool": "find_bioisosteres", "args": {"ref": "3", "family": "carboxylic_acid_isosteres"}}],
            [{"text": "done"}],
        ]
    )
    client._transport.app.state.provider = provider  # type: ignore[attr-defined]
    await _ask(client, "what fits compound 3", three_snapshot(await first_tab(client)))
    result = _sent_back(provider, 1)
    assert result["ok"] and result["molecule"] == "CC(=O)O"
    ids = [r["id"] for r in result["rules"]]
    assert "cooh_to_tetrazole" in ids and not set(ids) & EXCLUDED
    row = next(r for r in result["rules"] if r["id"] == "cooh_to_tetrazole")
    assert row["places"] == 1 and row["products"] == 1 and row["class"] == "classical" and row["name_ja"]
    assert 1 <= len(row["risks"]) <= 2 and all(": " in x for x in row["risks"])
    assert "confidence" not in json.dumps(result)  # the author's self-judgement is not evidence


async def test_an_unknown_or_excluded_id_is_refused_without_a_tab(client: httpx.AsyncClient) -> None:
    for rule_id in ("cooh_to_phosphonic_acid", "made_up_rule"):
        provider = FakeProvider(
            [
                [{"tool": "apply_bioisosteres", "args": {"rule_ids": [rule_id], "ref": "3", "title": "x"}}],
                [{"text": "ok"}],
            ]
        )
        client._transport.app.state.provider = provider  # type: ignore[attr-defined]
        events = await _ask(client, "go", three_snapshot(await first_tab(client)))
        assert not any(n == "tab.create" for n, _ in events)
        result = _sent_back(provider, 1)
        assert result["ok"] is False and f"unknown rule id(s): {rule_id}" in result["errors"][0]
        assert "find_bioisosteres" in result["errors"][0]


async def test_the_selection_restricts_the_rules_through_the_chat_loop(client: httpx.AsyncClient) -> None:
    tab = await first_tab(client)
    provider = FakeProvider(
        [
            [{"tool": "apply_bioisosteres", "args": {"rule_ids": ["cooh_to_tetrazole"], "title": "Tetrazole"}}],
            [{"text": "ok"}],
        ]
    )
    client._transport.app.state.provider = provider  # type: ignore[attr-defined]
    events = await _ask(client, "tetrazole for the CH2COOH", _drawn_snapshot(tab, TWO_ACIDS, [1, 2, 3]))
    created = next(d for n, d in events if n == "tab.create")
    assert created["tab"]["smiles_cache"] == [Chem.CanonSmiles("OC(=O)c1ccc(cc1)Cc1nn[nH]n1")]
    assert _sent_back(provider, 1)["distinct_products"] == 1


async def test_a_selection_that_misses_the_group_is_explained(client: httpx.AsyncClient) -> None:
    tab = await first_tab(client)
    provider = FakeProvider(
        [
            [{"tool": "apply_bioisosteres", "args": {"rule_ids": ["cooh_to_tetrazole"], "title": "x"}}],
            [{"text": "ok"}],
        ]
    )
    client._transport.app.state.provider = provider  # type: ignore[attr-defined]
    events = await _ask(client, "tetrazole", _drawn_snapshot(tab, TWO_ACIDS, [4, 5, 6]))  # the ring, not an acid
    assert not any(n == "tab.create" for n, _ in events)
    result = _sent_back(provider, 1)
    assert result["ok"] is False and "cooh_to_tetrazole fits only outside the selected atoms" in result["errors"][0]
    assert result["rules"][0]["id"] == "cooh_to_tetrazole" and result["rules"][0]["places"] == 0
    assert result["rules"][0]["fits_only_outside_the_selection"] is True


async def test_a_drawn_carboxylate_gives_neutral_products_on_its_own_drawing(client: httpx.AsyncClient) -> None:
    tab = await first_tab(client)
    tetrazole = {"tool": "apply_bioisosteres", "args": {"rule_ids": ["cooh_to_tetrazole"], "title": "Tetrazole"}}
    use_script(client, [[tetrazole], [{"text": "ok"}]])
    events = await _ask(client, "tetrazole", _drawn_snapshot(tab, "[O-]C(=O)c1ccccc1"))
    art = next(d["artifact"] for n, d in events if n == "artifact")["payload"]
    assert art["parent"]["smiles"] == Chem.CanonSmiles("[O-]C(=O)c1ccccc1")  # laid out on the drawing as it was
    assert [i["smiles"] for i in art["items"]] == [Chem.CanonSmiles("c1ccc(cc1)-c1nn[nH]n1")]


async def test_many_products_are_capped_and_the_model_is_told_how_to_narrow(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(bio, "MAX_CANDIDATES", 5)  # the real cap of 20 is tested on the engine above
    tab = await first_tab(client)
    ids = [r.id for r in TABLE.of_family("phenyl_ring_replacements")][:6]
    provider = FakeProvider(
        [
            [{"tool": "apply_bioisosteres", "args": {"rule_ids": ids, "title": "Many"}}],
            [{"text": "ok"}],
        ]
    )
    client._transport.app.state.provider = provider  # type: ignore[attr-defined]
    events = await _ask(client, "everything", _drawn_snapshot(tab, ATORVASTATIN))
    result = _sent_back(provider, 1)
    assert result["ok"] and len(result["accepted"]) == 5
    assert result["not_shown"] == result["distinct_products"] - 5 > 0
    assert "fewer rules" in result["to_see_the_rest"]
    assert len(next(d for n, d in events if n == "tab.create")["tab"]["smiles_cache"]) == 5


async def test_several_molecules_and_no_number_ask_which(client: httpx.AsyncClient) -> None:
    provider = FakeProvider(
        [
            [{"tool": "find_bioisosteres", "args": {}}],
            [{"text": "ok"}],
        ]
    )
    client._transport.app.state.provider = provider  # type: ignore[attr-defined]
    await _ask(client, "bioisosteres", three_snapshot(await first_tab(client)))
    result = _sent_back(provider, 1)
    assert result["ok"] is False and "give `ref`" in result["errors"][0]


def test_the_tool_schemas_carry_no_smarts_or_smiles_for_the_replacement() -> None:
    from chemomni.tools.registry import REGISTRY

    for name in ("find_bioisosteres", "apply_bioisosteres"):
        properties = REGISTRY[name].spec()["function"]["parameters"]["properties"]
        assert not {"smiles", "reaction", "product", "products"} & set(properties)
    assert "rule_ids" in REGISTRY["apply_bioisosteres"].spec()["function"]["parameters"]["properties"]
