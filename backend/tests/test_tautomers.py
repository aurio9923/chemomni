"""Tautomer enumeration and the canonical tautomer (decisions Q63 and Q64): the engine, the highlight and the tool.

Expected structures are written down by hand (a pyrazole and its other N-H form, 2-pyridone and 2-hydroxypyridine,
the two N-H forms of a 5-substituted tetrazole), never taken from the code under test. The stereo cases are the
reason for the engine's two non-default settings: each has a control that shows RDKit's default losing the
stereocentre, so a future RDKit that fixes the default will announce itself.

The unfavoured forms (Q64) are written down by hand as well: the lactim of oxindole that started the rule, the
same change on other acid derivatives, and next to each a form that must stay (a hydroxy-heteroarene, the enol
of a ketone, the enol of a beta-dicarbonyl).
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from rdkit import Chem
from rdkit.Chem import rdDepictor
from rdkit.Chem.MolStandardize import rdMolStandardize

from chemomni.chem import tautomers as taut
from chemomni.chem.edit import align
from chemomni.chem.generate import evaluate
from chemomni.tools.tautomer_tools import MAX_SHOWN, TautomersIn
from tests.test_bioisostere import _ask, _drawn_snapshot, _sent_back
from tests.test_chat import use_script
from tests.test_compounds import _snapshot as three_snapshot
from tests.test_compounds import _tab as first_tab

PYRAZOLE_A = "Cc1cc(-c2ccccc2)n[nH]1"  # 3-phenyl-5-methylpyrazole, N-H next to the methyl carbon
PYRAZOLE_B = "Cc1cc(-c2ccccc2)[nH]n1"  # the same compound with the N-H on the other nitrogen


def cs(smiles: str) -> str:
    return Chem.CanonSmiles(smiles)


def run(smiles: str, **kwargs: Any) -> taut.TautomerSet:
    mol = Chem.MolFromSmiles(smiles)
    assert mol is not None, smiles
    return taut.tautomers(mol, **kwargs)


# ---------------------------------------------------------------- the engine


def test_a_pyrazole_has_two_tautomers_and_one_canonical_form_however_it_is_drawn() -> None:
    a, b = run(PYRAZOLE_A), run(PYRAZOLE_B)
    assert a.total == b.total == 2 and a.complete and b.complete
    assert a.canonical == b.canonical  # one label for the one compound
    assert a.parent == cs(PYRAZOLE_A) and b.parent == cs(PYRAZOLE_B)
    assert a.others == [cs(PYRAZOLE_B)] and b.others == [cs(PYRAZOLE_A)]  # by hand: just the other N-H form
    assert a.canonical in {cs(PYRAZOLE_A), cs(PYRAZOLE_B)}


def test_the_two_n_h_forms_of_a_tetrazole() -> None:
    found = run("Cc1nn[nH]n1")  # 2H-tetrazole
    assert found.total == 2
    assert {found.parent, *found.others} == {cs("Cc1nn[nH]n1"), cs("Cc1nnn[nH]1")}  # 2H and 1H


def test_2_pyridone_and_2_hydroxypyridine() -> None:
    found = run("Oc1ccccn1")
    assert cs("O=c1cccc[nH]1") in {found.parent, *found.others}
    assert found.canonical == cs("O=c1cccc[nH]1")  # the pyridone: how the compound mostly exists


def test_barbituric_acid_shows_that_canonical_is_not_the_predominant_form() -> None:
    """RDKit prefers the aromatic enol; the compound exists as the triketone. The tool tells the model so."""
    found = run("O=C1CC(=O)NC(=O)N1")
    assert not found.is_canonical
    assert found.canonical == cs("O=c1cc(O)[nH]c(=O)[nH]1")
    assert found.others[0] == found.canonical  # the canonical form comes first in the list


def test_a_molecule_with_no_other_tautomer() -> None:
    found = run("c1ccccc1")
    assert found.others == [] and found.total == 1 and found.is_canonical and found.complete


def test_the_list_never_holds_the_given_form_and_counts_it() -> None:
    for smiles in ("O=c1[nH]cnc2[nH]ncc12", PYRAZOLE_A, "CC(=O)CC(C)=O"):
        found = run(smiles)
        assert found.parent not in found.others
        assert found.total == len(found.others) + 1 and len(set(found.others)) == len(found.others)


@pytest.mark.parametrize(
    "smiles",
    [
        "CC(C)Cc1ccc(cc1)[C@H](C)C(=O)O",  # (S)-ibuprofen
        "C[C@H]([NH3+])C(=O)[O-]",  # L-alanine as a zwitterion: the charge is left alone too
        "C[C@@H]1CCCCC1=O",  # (R)-2-methylcyclohexanone
        "O=C1CC[C@H](N2C(=O)c3ccccc3C2=O)C(=O)N1",  # (S)-thalidomide
    ],
)
def test_stereochemistry_survives_and_the_enantiomers_stay_apart(smiles: str) -> None:
    found = run(smiles)
    assert found.canonical == cs(smiles)
    assert "@" in found.canonical
    mirror = smiles.replace("@@", "!").replace("@", "@@").replace("!", "@")
    assert run(mirror).canonical == cs(mirror) != found.canonical


@pytest.mark.parametrize("smiles", ["CC(C)Cc1ccc(cc1)[C@H](C)C(=O)O", "C[C@H]([NH3+])C(=O)[O-]", "C[C@@H]1CCCCC1=O"])
def test_control_rdkits_default_settings_lose_the_stereocentre(smiles: str) -> None:
    default = rdMolStandardize.TautomerEnumerator()
    assert "@" not in Chem.MolToSmiles(default.Canonicalize(Chem.MolFromSmiles(smiles)))  # why we turn it off


@pytest.mark.parametrize(
    "smiles",
    [PYRAZOLE_A, "O=c1[nH]cnc2[nH]ncc12", "Nc1nc2[nH]cnc2c(=O)[nH]1", "O=C1CC(=O)NC(=O)N1", "CC(=O)CC(C)=O"],
)
def test_the_canonical_form_does_not_depend_on_how_the_molecule_is_written(smiles: str) -> None:
    mol = Chem.MolFromSmiles(smiles)
    assert mol is not None
    written = {Chem.MolToSmiles(mol, doRandom=True) for _ in range(8)}
    assert len({run(w).canonical for w in written}) == 1
    assert run(run(smiles).canonical).canonical == run(smiles).canonical  # and it is stable


def test_a_salt_is_refused_not_guessed_at() -> None:
    assert taut.check_one_molecule(Chem.MolFromSmiles("CC(=O)[O-].[Na+]")) is not None
    assert taut.check_one_molecule(Chem.MolFromSmiles("CC(=O)[O-]")) is None


def test_the_limits_are_reported_as_a_partial_result_not_hidden() -> None:
    pentaglycine = "NCC(=O)NCC(=O)NCC(=O)NCC(=O)NCC(=O)O"  # every amide can be an imidic acid: hundreds of forms
    full = run(pentaglycine)
    assert full.complete and full.total > 100
    few = run(pentaglycine, max_transforms=5)
    assert not few.complete and 1 < few.total < full.total
    late = run(pentaglycine, time_limit_s=0.0)
    assert not late.complete and late.canonical  # still an answer, marked partial


# ---------------------------------------------------------------- unfavoured forms (Q64)

OXINDOLE = "O=C1Cc2ccccc2N1"
OXINDOLE_LACTIM = "OC1=Nc2ccccc2C1"  # 3H-indol-2-ol: the form the user pointed at
HYDROXYINDOLE = "Oc1cc2ccccc2[nH]1"


def test_the_lactim_of_oxindole_is_unfavoured_and_2_hydroxyindole_is_not() -> None:
    found = run(OXINDOLE)
    assert set(found.others) == {cs(OXINDOLE_LACTIM), cs(HYDROXYINDOLE)}
    assert found.unfavoured == {cs(OXINDOLE_LACTIM): ("imidic_acid",)}
    assert found.favoured == [cs(HYDROXYINDOLE)]
    assert found.hidden_by_reason() == {"imidic_acid": 1}


@pytest.mark.parametrize(
    ("drawn", "imidic"),
    [
        ("CC(=O)Nc1ccccc1", "CC(O)=Nc1ccccc1"),  # secondary amide
        ("NC(=O)c1ccccc1", "N=C(O)c1ccccc1"),  # primary amide
        ("O=C1CCCN1", "OC1=NCCC1"),  # lactam
        ("NC(N)=O", "N=C(N)O"),  # urea as isourea
        ("COC(=O)NC", "COC(O)=NC"),  # carbamate
        ("O=C1CCC(=O)N1", "O=C1CCC(O)=N1"),  # imide
        ("O=C1NS(=O)(=O)c2ccccc12", "OC1=NS(=O)(=O)c2ccccc12"),  # saccharin
        ("O=C1NC(=O)C(N1)(c1ccccc1)c1ccccc1", "O=C1N=C(O)C(c2ccccc2)(c2ccccc2)N1"),  # hydantoin (phenytoin)
        ("CC(N)=S", "CC(=N)S"),  # thioamide as the thiol
        ("NC(N)=S", "N=C(N)S"),  # thiourea
        ("CC(=O)NO", "CC(O)=NO"),  # hydroxamic acid
    ],
)
def test_every_acid_derivative_written_as_c_oh_n_is_unfavoured(drawn: str, imidic: str) -> None:
    found = run(drawn)
    assert "imidic_acid" in found.unfavoured[cs(imidic)]
    assert cs(imidic) not in found.favoured


@pytest.mark.parametrize(
    ("drawn", "enol"),
    [
        ("CC(=O)Nc1ccccc1", "C=C(O)Nc1ccccc1"),  # amide
        ("CC(=O)O", "C=C(O)O"),  # acid
        ("CC(C(=O)O)c1ccccc1", "CC(=C(O)O)c1ccccc1"),  # 2-arylpropanoic acid
        ("CC(=O)Oc1ccccc1C(=O)O", "C=C(O)Oc1ccccc1C(=O)O"),  # ester (aspirin)
        ("CC(N)=S", "C=C(N)S"),  # thioamide
    ],
)
def test_the_enol_of_an_amide_acid_or_ester_is_unfavoured(drawn: str, enol: str) -> None:
    found = run(drawn)
    assert "acid_enol" in found.unfavoured[cs(enol)]


@pytest.mark.parametrize(
    ("drawn", "kept"),
    [
        ("O=c1cccc[nH]1", "Oc1ccccn1"),  # the lactim inside an aromatic ring is 2-hydroxypyridine
        ("Oc1ccccn1", "O=c1cccc[nH]1"),
        ("O=c1cc[nH]c(=O)[nH]1", "O=c1cc[nH]c(O)n1"),  # uracil: a hydroxypyrimidinone
        ("O=c1ccc2ccccc2[nH]1", "Oc1ccc2ccccc2n1"),  # 2-quinolone
        ("CC(C)=O", "C=C(C)O"),  # the enol of a ketone
        ("CC(=O)c1ccccc1", "C=C(O)c1ccccc1"),
        ("CC(=O)CC(C)=O", "CC(=O)C=C(C)O"),  # the enol of a beta-diketone
        ("CCOC(=O)CC(C)=O", "CCOC(O)=CC(C)=O"),  # a beta-keto ester enolised towards the ester: conjugated, kept
        ("CC(=O)CC(N)=O", "CC(=O)C=C(N)O"),  # the same for a beta-keto amide
        ("Nc1ccccn1", "N=c1cccc[nH]1"),  # amino / imino
        (PYRAZOLE_A, PYRAZOLE_B),  # the N-H forms of an azole
        ("O=C1CC(=O)CN1", "O=C1C=C(O)CN1"),  # tetramic acid: the enol it exists as
    ],
)
def test_forms_a_chemist_draws_are_not_unfavoured(drawn: str, kept: str) -> None:
    found = run(drawn)
    assert cs(kept) in found.favoured


def test_a_beta_keto_ester_keeps_its_conjugated_enols_and_loses_the_bis_enol() -> None:
    found = run("CCOC(=O)CC(C)=O")
    assert found.unfavoured == {cs("C=C(O)C=C(O)OCC"): ("acid_enol",)}  # an ester enol with no carbonyl left


def test_a_hydroxamic_acid_has_no_favoured_form_and_an_unfavoured_canonical_one() -> None:
    """RDKit scores the oxime-like C(OH)=N-OH highest, so the canonical tautomer itself is an unfavoured form."""
    found = run("CC(=O)NO")
    assert found.canonical == cs("CC(O)=NO") and found.canonical in found.unfavoured
    assert found.unfavoured[cs("CC(O)N=O")] == ("nitroso_carbinol",)
    assert found.favoured == []


@pytest.mark.parametrize(
    ("drawn", "broken"),
    [
        ("Oc1ccccc1", "O=C1C=CC=CC1"),  # phenol as cyclohexa-2,4-dienone
        ("Oc1ccccn1", "O=C1CC=CC=N1"),  # 3H-pyridin-2-one: the form in the user's first screenshot
        ("O=c1cc[nH]cc1", "O=C1C=CN=CC1"),  # 4-pyridone
        ("O=Cc1c[nH]c2ccccc12", "O=CC1C=Nc2ccccc21"),  # 3H-indole
        ("O=c1cc(O)[nH]c(=O)[nH]1", "O=C1CC(=O)NC(=O)N1"),  # the price: an enol drawing hides the triketone
    ],
)
def test_a_form_that_loses_an_aromatic_ring_of_the_drawing_is_unfavoured(drawn: str, broken: str) -> None:
    found = run(drawn)
    assert "lost_aromatic_ring" in found.unfavoured[cs(broken)]


def test_the_drawing_is_the_reference_so_a_keto_drawing_keeps_its_non_aromatic_enols() -> None:
    """Cyclohexane-1,3,5-trione: the canonical form is phloroglucinol, and the enols on the way there have no
    aromatic ring. Nothing aromatic was drawn, so nothing is lost and they all stay."""
    found = run("O=C1CC(=O)CC(=O)C1")
    assert found.canonical == cs("Oc1cc(O)cc(O)c1")
    assert not found.unfavoured
    assert cs("O=C1C=C(O)CC(=O)C1") in found.favoured


def test_control_counting_aromatic_rings_against_the_canonical_form_would_hide_real_enols() -> None:
    """The rule considered first (hide a form with fewer aromatic rings than the canonical one) would hide the
    mono-enol of the trione and the enol tetramic acids exist as. That is why the rule is per ring and against
    the drawing."""
    from rdkit.Chem import rdMolDescriptors

    def rings(smiles: str) -> int:
        return rdMolDescriptors.CalcNumAromaticRings(Chem.MolFromSmiles(smiles))

    for drawn, real in [("O=C1CC(=O)CC(=O)C1", "O=C1C=C(O)CC(=O)C1"), ("O=C1CC(=O)CN1", "O=C1C=C(O)CN1")]:
        found = run(drawn)
        assert rings(real) < rings(found.canonical)  # the first rule would have hidden it
        assert cs(real) in found.favoured


def test_rings_are_compared_one_by_one_not_counted() -> None:
    """An oxindole carrying a pyridone. In this form the pyridone ring has a CH2 and the oxindole ring has become
    an aromatic hydroxyindole: two aromatic rings, as many as in the drawing, and still a ring was lost."""
    found = run("O=C1Cc2cc(-c3cc[nH]c(=O)c3)ccc2N1")
    form = cs("O=C1C=C(c2ccc3[nH]c(O)cc3c2)CC=N1")
    assert found.unfavoured[form] == ("lost_aromatic_ring",)


def test_what_was_drawn_is_never_the_reason_to_hide_something() -> None:
    found = run(OXINDOLE_LACTIM)  # the user drew the lactim: the lactam is simply its tautomer
    assert set(found.favoured) == {cs(OXINDOLE), cs(HYDROXYINDOLE)} and not found.unfavoured


def test_favoured_keeps_the_order_and_others_still_holds_everything() -> None:
    found = run("O=c1cc[nH]c(=O)[nH]1")  # uracil
    assert len(found.others) == found.total - 1
    assert found.favoured == [s for s in found.others if s not in found.unfavoured]
    assert 0 < len(found.unfavoured) < len(found.others)


def test_every_unfavoured_group_has_an_explanation_and_a_valid_pattern() -> None:
    assert set(taut.UNFAVOURED_WHY) == {taut.LOST_AROMATIC_RING, *taut.UNFAVOURED_GROUPS}
    for smarts in taut.UNFAVOURED_GROUPS.values():
        assert Chem.MolFromSmarts(smarts) is not None


# ---------------------------------------------------------------- what is highlighted on the grid


def _changed(parent: str, tautomer: str, *, bond_changes: bool) -> list[int]:
    result = evaluate(parent, [(tautomer, "t")], any_element=True, bond_changes=bond_changes)
    assert result.ok, result.errors
    return sorted(result.accepted[0].changed_atoms)


def test_the_atoms_where_the_proton_moved_are_highlighted() -> None:
    # Oc1ccccn1: atom 0 O, 1 the carbon it is on, 6 the ring nitrogen. The proton went from N to O.
    assert _changed("O=c1cccc[nH]1", "Oc1ccccn1", bond_changes=True) == [0, 1, 6]
    # and the amide/imidic acid pair: C, O and N around the moved proton
    assert _changed("CC(=O)N", "CC(O)=N", bond_changes=True) == [1, 2, 3]


def test_control_comparing_elements_alone_sees_no_change_between_tautomers() -> None:
    assert _changed("O=c1cccc[nH]1", "Oc1ccccn1", bond_changes=False) == []


def test_a_symmetric_group_is_not_marked_when_the_other_mapping_needs_no_change() -> None:
    """The two oxygens of a carboxylic acid can be matched either way round; the way that changes nothing wins."""
    template = Chem.MolFromSmiles("CC(=O)O")
    assert template is not None
    rdDepictor.Compute2DCoords(template)
    _, changed = align(Chem.MolFromSmiles("CC(O)=O"), template, bond_changes=True)
    assert changed == []


# ---------------------------------------------------------------- the tool through the chat loop


def test_the_tool_input_has_no_field_for_a_tautomer_smiles() -> None:
    assert set(TautomersIn.model_fields) == {"ref", "smiles", "show", "all_forms", "title"}


async def test_all_tautomers_of_the_drawn_pyrazole_open_on_a_tab(client: httpx.AsyncClient) -> None:
    provider = use_script(client, [[{"tool": "tautomers", "args": {}}], [{"text": "done"}]])
    tab = (await client.get("/api/workspace")).json()["tabs"][0]
    events = await _ask(client, "tautomers please", _drawn_snapshot(tab, PYRAZOLE_A))

    start = next(d for n, d in events if n == "tool.start")
    assert start["name"] == "tautomers" and start["arguments"] == {}
    created = next(d for n, d in events if n == "tab.create")
    assert created["tab"]["smiles_cache"] == [cs(PYRAZOLE_B)]
    result = _sent_back(provider, 1)
    assert result["ok"] and result["tautomer_count"] == 2 and result["complete"] is True
    assert result["molecule"] == cs(PYRAZOLE_A) and result["is_canonical"] is False
    assert result["canonical"] == cs(PYRAZOLE_B)
    assert [a["note"] for a in result["accepted"]] == ["canonical tautomer"]
    assert "not_shown" not in result


async def test_the_canonical_form_alone_is_opened_only_when_it_differs(client: httpx.AsyncClient) -> None:
    provider = use_script(client, [[{"tool": "tautomers", "args": {"show": "canonical"}}], [{"text": "done"}]])
    tab = (await client.get("/api/workspace")).json()["tabs"][0]
    events = await _ask(client, "canonical tautomer", _drawn_snapshot(tab, PYRAZOLE_A))
    assert next(d for n, d in events if n == "tab.create")["tab"]["smiles_cache"] == [cs(PYRAZOLE_B)]

    provider = use_script(client, [[{"tool": "tautomers", "args": {"show": "canonical"}}], [{"text": "done"}]])
    events = await _ask(client, "canonical tautomer", _drawn_snapshot(tab, PYRAZOLE_B))  # already canonical
    result = _sent_back(provider, 1)
    assert result["ok"] and result["is_canonical"] is True and "accepted" not in result
    assert "already the canonical tautomer" in result["note"]
    assert not any(n == "tab.create" for n, _ in events)


async def test_two_drawings_of_one_compound_are_recognised_without_opening_tabs(client: httpx.AsyncClient) -> None:
    """show='none' reports the canonical form only, so two calls can be compared and nothing is opened."""
    provider = use_script(
        client,
        [
            [{"tool": "tautomers", "args": {"show": "none"}}],
            [{"tool": "tautomers", "args": {"show": "none", "smiles": PYRAZOLE_B}}],
            [{"text": "same compound"}],
        ],
    )
    tab = (await client.get("/api/workspace")).json()["tabs"][0]
    events = await _ask(client, "are these the same?", _drawn_snapshot(tab, PYRAZOLE_A))
    # the model's message list is one shared object, so read every tool result from the final history
    first, second = [json.loads(m["content"]) for m in provider.calls[-1]["messages"] if m["role"] == "tool"]
    assert first["ok"] and second["ok"] and first["canonical"] == second["canonical"]
    assert first["molecule"] != second["molecule"]  # two different drawings
    assert "accepted" not in first and "no tab opened" in first["note"]
    assert not any(n == "tab.create" for n, _ in events)


async def test_a_molecule_with_no_other_tautomer_says_so_and_opens_nothing(client: httpx.AsyncClient) -> None:
    provider = use_script(client, [[{"tool": "tautomers", "args": {}}], [{"text": "done"}]])
    tab = (await client.get("/api/workspace")).json()["tabs"][0]
    events = await _ask(client, "tautomers", _drawn_snapshot(tab, "c1ccccc1"))
    result = _sent_back(provider, 1)
    assert result["ok"] and result["tautomer_count"] == 1 and "no other tautomer" in result["note"]
    assert not any(n == "tab.create" for n, _ in events)


async def test_compound_three_by_number(client: httpx.AsyncClient) -> None:
    """Compound 3 on the fixture tab is acetic acid; its only other form is the enol (ethene-1,1-diol), which is
    unfavoured: nothing opens by default, and all_forms brings it back with its reason."""
    provider = use_script(client, [[{"tool": "tautomers", "args": {"ref": "3"}}], [{"text": "done"}]])
    events = await _ask(client, "tautomers of 3", three_snapshot(await first_tab(client)))
    result = _sent_back(provider, 1)
    assert result["ok"] and result["molecule"] == cs("CC(=O)O") and result["tautomer_count"] == 2
    assert result["hidden"]["count"] == 1 and "no tab opened" in result["note"]
    assert not any(n == "tab.create" for n, _ in events)

    provider = use_script(
        client, [[{"tool": "tautomers", "args": {"ref": "3", "all_forms": True}}], [{"text": "done"}]]
    )
    events = await _ask(client, "every form of 3", three_snapshot(await first_tab(client)))
    result = _sent_back(provider, 1)
    assert next(d for n, d in events if n == "tab.create")["tab"]["smiles_cache"] == [cs("C=C(O)O")]
    assert "hidden" not in result and "unfavoured" in result["accepted"][0]["note"]


async def test_the_selection_picks_the_molecule_on_a_tab_with_two(client: httpx.AsyncClient) -> None:
    provider = use_script(client, [[{"tool": "tautomers", "args": {}}], [{"text": "done"}]])
    tab = (await client.get("/api/workspace")).json()["tabs"][0]
    snapshot = _drawn_snapshot(tab, f"{PYRAZOLE_A}.CCO", selected=[1])  # atom 1 belongs to the pyrazole
    events = await _ask(client, "tautomers of this", snapshot)
    assert _sent_back(provider, 1)["molecule"] == cs(PYRAZOLE_A)
    assert next(d for n, d in events if n == "tab.create")["tab"]["smiles_cache"] == [cs(PYRAZOLE_B)]


async def test_several_molecules_and_no_number_ask_which(client: httpx.AsyncClient) -> None:
    provider = use_script(client, [[{"tool": "tautomers", "args": {}}], [{"text": "done"}]])
    events = await _ask(client, "tautomers", three_snapshot(await first_tab(client)))
    result = _sent_back(provider, 1)
    assert not result["ok"] and "several molecules" in result["errors"][0]
    assert not any(n == "tab.create" for n, _ in events)


async def test_a_salt_is_refused_through_the_chat_loop(client: httpx.AsyncClient) -> None:
    provider = use_script(client, [[{"tool": "tautomers", "args": {}}], [{"text": "done"}]])
    tab = (await client.get("/api/workspace")).json()["tabs"][0]
    events = await _ask(client, "tautomers", _drawn_snapshot(tab, "CC(=O)[O-].[Na+]"))
    result = _sent_back(provider, 1)
    assert not result["ok"] and "several components" in result["errors"][0]
    assert not any(n == "tab.create" for n, _ in events)


async def test_more_than_twenty_tautomers_are_capped_and_counted(client: httpx.AsyncClient) -> None:
    provider = use_script(client, [[{"tool": "tautomers", "args": {"all_forms": True}}], [{"text": "done"}]])
    tab = (await client.get("/api/workspace")).json()["tabs"][0]
    uric_acid = "O=c1[nH]c2[nH]c(=O)[nH]c2c(=O)[nH]1"
    events = await _ask(client, "every form of uric acid", _drawn_snapshot(tab, uric_acid))
    result = _sent_back(provider, 1)
    assert result["ok"] and result["tautomer_count"] > MAX_SHOWN + 1 and "hidden" not in result
    assert len(result["accepted"]) == MAX_SHOWN
    assert result["not_shown"] == result["tautomer_count"] - 1 - MAX_SHOWN
    assert result["is_canonical"] is True  # the drawn uric acid is already canonical: no row carries that label
    assert result["accepted"][0]["note"] == f"tautomer 1 of {result['tautomer_count'] - 1}"
    assert len(next(d for n, d in events if n == "tab.create")["tab"]["smiles_cache"]) == MAX_SHOWN


async def test_the_default_fake_provider_lists_tautomers_and_the_canonical_form(client: httpx.AsyncClient) -> None:
    from chemomni.llm.fake_provider import FakeProvider

    client._transport.app.state.provider = FakeProvider()  # type: ignore[attr-defined]
    tab = (await client.get("/api/workspace")).json()["tabs"][0]
    events = await _ask(client, "互変異性体を出して", _drawn_snapshot(tab, PYRAZOLE_A))
    assert next(d for n, d in events if n == "tool.start")["arguments"] == {}
    assert next(d for n, d in events if n == "tab.create")["tab"]["smiles_cache"] == [cs(PYRAZOLE_B)]
    events = await _ask(client, "canonical tautomer of this", _drawn_snapshot(tab, PYRAZOLE_B))
    assert next(d for n, d in events if n == "tool.start")["arguments"] == {"show": "canonical"}


# ---------------------------------------------------------------- the tool and the unfavoured forms (Q64)


async def test_the_lactim_of_oxindole_is_left_off_the_tab_and_counted(client: httpx.AsyncClient) -> None:
    provider = use_script(client, [[{"tool": "tautomers", "args": {}}], [{"text": "done"}]])
    tab = (await client.get("/api/workspace")).json()["tabs"][0]
    events = await _ask(client, "tautomers", _drawn_snapshot(tab, OXINDOLE))
    result = _sent_back(provider, 1)
    assert next(d for n, d in events if n == "tab.create")["tab"]["smiles_cache"] == [cs(HYDROXYINDOLE)]
    assert result["tautomer_count"] == 3  # the count is of everything RDKit found
    assert [a["note"] for a in result["accepted"]] == ["canonical tautomer"]
    assert result["hidden"]["count"] == 1 and list(result["hidden"]["why"].values()) == [1]
    assert "C(OH)=N" in next(iter(result["hidden"]["why"])) and "all_forms" in result["hidden"]["to_see_them"]
    assert "canonical_is_unfavoured" not in result


async def test_all_forms_brings_the_lactim_back_with_its_reason(client: httpx.AsyncClient) -> None:
    provider = use_script(client, [[{"tool": "tautomers", "args": {"all_forms": True}}], [{"text": "done"}]])
    tab = (await client.get("/api/workspace")).json()["tabs"][0]
    events = await _ask(client, "every form", _drawn_snapshot(tab, OXINDOLE))
    result = _sent_back(provider, 1)
    created = next(d for n, d in events if n == "tab.create")["tab"]["smiles_cache"]
    assert set(created) == {cs(HYDROXYINDOLE), cs(OXINDOLE_LACTIM)} and "hidden" not in result
    notes = {a["smiles"]: a["note"] for a in result["accepted"]}
    assert notes[cs(HYDROXYINDOLE)] == "canonical tautomer"
    assert (
        notes[cs(OXINDOLE_LACTIM)].startswith("tautomer 2 of 2 (unfavoured: ")
        and "lactim" in notes[cs(OXINDOLE_LACTIM)]
    )


async def test_a_molecule_with_only_unfavoured_forms_opens_nothing_and_says_why(client: httpx.AsyncClient) -> None:
    provider = use_script(client, [[{"tool": "tautomers", "args": {}}], [{"text": "done"}]])
    tab = (await client.get("/api/workspace")).json()["tabs"][0]
    events = await _ask(client, "tautomers", _drawn_snapshot(tab, "CC(=O)Nc1ccccc1"))  # acetanilide
    result = _sent_back(provider, 1)
    assert result["ok"] and result["tautomer_count"] == 3 and "accepted" not in result
    assert result["hidden"]["count"] == 2 and sorted(result["hidden"]["why"].values()) == [1, 1]
    assert "unfavoured" in result["note"]
    assert not any(n == "tab.create" for n, _ in events)


async def test_the_shown_and_the_hidden_forms_add_up(client: httpx.AsyncClient) -> None:
    provider = use_script(client, [[{"tool": "tautomers", "args": {}}], [{"text": "done"}]])
    tab = (await client.get("/api/workspace")).json()["tabs"][0]
    await _ask(client, "tautomers of uric acid", _drawn_snapshot(tab, "O=c1[nH]c2[nH]c(=O)[nH]c2c(=O)[nH]1"))
    result = _sent_back(provider, 1)
    assert 0 < len(result["accepted"]) < MAX_SHOWN and "not_shown" not in result
    assert len(result["accepted"]) + result["hidden"]["count"] == result["tautomer_count"] - 1
    assert result["accepted"][0]["note"] == f"tautomer 1 of {len(result['accepted'])}"


async def test_the_canonical_form_is_opened_when_asked_for_even_if_unfavoured(client: httpx.AsyncClient) -> None:
    provider = use_script(client, [[{"tool": "tautomers", "args": {"show": "canonical"}}], [{"text": "done"}]])
    tab = (await client.get("/api/workspace")).json()["tabs"][0]
    events = await _ask(client, "canonical tautomer", _drawn_snapshot(tab, "CC(=O)NO"))  # acetohydroxamic acid
    result = _sent_back(provider, 1)
    assert next(d for n, d in events if n == "tab.create")["tab"]["smiles_cache"] == [cs("CC(O)=NO")]
    assert "C(OH)=N" in result["canonical_is_unfavoured"] and "hidden" not in result
    assert result["accepted"][0]["note"].startswith("canonical tautomer (unfavoured: ")


async def test_the_default_fake_provider_asks_for_every_form(client: httpx.AsyncClient) -> None:
    from chemomni.llm.fake_provider import FakeProvider

    client._transport.app.state.provider = FakeProvider()  # type: ignore[attr-defined]
    tab = (await client.get("/api/workspace")).json()["tabs"][0]
    events = await _ask(client, "show every form of this tautomer set", _drawn_snapshot(tab, OXINDOLE))
    assert next(d for n, d in events if n == "tool.start")["arguments"] == {"all_forms": True}
    assert len(next(d for n, d in events if n == "tab.create")["tab"]["smiles_cache"]) == 2
