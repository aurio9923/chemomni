"""The bioisostere rule table (M5): the checks the rule authors' validator makes, run on the vendored files.

``validate_rules.py`` came with the table; its checks live here so a rule that is edited later is caught by CI.
Each rule's own examples are also run through the engine (``chem.bioisostere``), which must give exactly the
molecules the author expected, once duplicates and tautomers are merged. The independent expectations (molecules
written down by hand, not taken from the rules) are in ``test_bioisostere.py``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml
from rdkit import Chem
from rdkit.Chem import AllChem
from rdkit.Chem.MolStandardize import rdMolStandardize

from chemomni.chem import bioisostere as bio
from chemomni.chem.bioisostere_rules import DATA_DIR, Rule, RuleTableError, load_rules, rules

EXCLUDED = {"cooh_to_phosphonic_acid", "amide_to_trans_alkene", "amide_to_fluoroalkene", "para_phenylene_to_cubane"}
ALL = rules().everything

# prose of a rule must stay qualitative: no digits, units or pKa/logP/IC50 claims (from validate_rules.py)
NUMERIC_CLAIM = re.compile(
    r"(?i)(\bpka\b|\blogp\b|\bclogp\b|\blogd\b|\bic50\b|\bec50\b|\bki\b|\bkd\b|\bnm\b|\bum\b|\bmm\b|μm|%|\uff05|倍|"
    r"[+-]?\d+(\.\d+)?\s*(log|unit|fold|kcal|kj|å|nm|um|mm|mg|g|ml|l|h|min|sec|度|割)?|\d)"
)
PLACEHOLDER = re.compile(r"(?i)\b(todo|fixme|dummy|tbd|xxx)\b")

ENUMERATOR = rdMolStandardize.TautomerEnumerator()


def _tautomer(smiles_or_mol: str | Chem.Mol) -> str:
    mol = Chem.MolFromSmiles(smiles_or_mol) if isinstance(smiles_or_mol, str) else Chem.Mol(smiles_or_mol)
    assert mol is not None, smiles_or_mol
    for atom in mol.GetAtoms():
        atom.SetAtomMapNum(0)
    Chem.SanitizeMol(mol)
    return Chem.MolToSmiles(ENUMERATOR.Canonicalize(mol))


def _charge(mol: Chem.Mol) -> int:
    return sum(abs(a.GetFormalCharge()) for a in mol.GetAtoms())


def _radicals(mol: Chem.Mol) -> int:
    return sum(a.GetNumRadicalElectrons() for a in mol.GetAtoms())


# ---------------------------------------------------------------- the table as a whole


def test_the_table_has_84_rules_of_which_80_are_active() -> None:
    table = rules()
    assert len(table.everything) == 84 and len(table.active) == 80
    assert {r.id for r in table.everything if r.status == "excluded"} == EXCLUDED
    assert len(table.families) == 8


def test_an_excluded_rule_is_unknown_to_everything_that_looks_rules_up() -> None:
    table = rules()
    for rule_id in EXCLUDED:
        assert table.get(rule_id) is None
        assert rule_id not in {r.id for r in table.active}
        assert all(r.id != rule_id for family in table.families for r in table.of_family(family))
    assert table.get("cooh_to_tetrazole") is not None


def test_ids_are_unique_and_each_file_is_its_family() -> None:
    assert len({r.id for r in ALL}) == len(ALL)
    assert {r.family for r in ALL} == {p.stem for p in DATA_DIR.glob("*.yaml")}


# ---------------------------------------------------------------- the loader refuses what the engine cannot use


def _write(tmp_path: Path, rule_changes: dict[str, object], name: str = "linkers") -> Path:
    base = next(r for r in yaml.safe_load((DATA_DIR / "linkers.yaml").read_text(encoding="utf-8")))
    (tmp_path / f"{name}.yaml").write_text(yaml.safe_dump([{**base, **rule_changes}]), encoding="utf-8")
    return tmp_path


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"reaction": "[C:1]>>[C:1]F"}, "replaces nothing"),
        ({"reaction": "[C:1]O>>[C:2]F"}, "atom maps differ"),
        ({"reaction": "[C:1].[N:2]>>[C:1][N:2]"}, "one reactant template"),
        ({"reaction": "not smarts ((("}, "cannot be parsed"),
        ({"family": "linker_x"}, "does not match the file name"),
        ({"class": "magic"}, "class"),
        ({"surprise": 1}, "surprise"),
        ({"id": "Bad-Id"}, "id"),
    ],
)
def test_the_loader_names_the_problem(tmp_path: Path, changes: dict[str, object], message: str) -> None:
    with pytest.raises(RuleTableError, match=message):
        load_rules(_write(tmp_path, changes))


def test_the_loader_refuses_an_empty_folder_and_duplicate_ids(tmp_path: Path) -> None:
    with pytest.raises(RuleTableError, match="no rule files"):
        load_rules(tmp_path)
    entry = yaml.safe_load((DATA_DIR / "linkers.yaml").read_text(encoding="utf-8"))[0]
    for stem in ("linkers", "substituent_swaps"):
        (tmp_path / f"{stem}.yaml").write_text(yaml.safe_dump([{**entry, "family": stem}]), encoding="utf-8")
    with pytest.raises(RuleTableError, match="already used"):
        load_rules(tmp_path)


# ---------------------------------------------------------------- every rule, as the authors' validator checks it


@pytest.mark.parametrize("rule", ALL, ids=lambda r: r.id)
def test_rule_data(rule: Rule) -> None:
    assert not NUMERIC_CLAIM.search(rule.context), rule.context
    assert not PLACEHOLDER.search(rule.context)
    for risk in rule.risks:
        assert not NUMERIC_CLAIM.search(risk.note), risk.note
        assert not PLACEHOLDER.search(risk.note)
    assert rule.risks and rule.refs
    for ref in rule.refs:
        assert 1900 <= ref.year <= 2027
        assert not ref.verified or ref.doi, "verified=true needs a doi"
    review = rule.review
    assert review.decision == "ok" or review.note.strip(), "a review other than ok needs a note"
    assert (rule.status == "excluded") == (review.decision == "rejected" or rule.id in EXCLUDED)
    assert len(rule.tests.positive) >= 2 and len(rule.tests.negative) >= 1


@pytest.mark.parametrize("rule", ALL, ids=lambda r: r.id)
def test_attachments_are_the_mapped_atoms(rule: Rule) -> None:
    rxn = AllChem.ReactionFromSmarts(rule.reaction)  # keep it: a template is only valid while its reaction lives
    template = rxn.GetReactantTemplate(0)
    assert rule.attachments == len({a.GetAtomMapNum() for a in template.GetAtoms() if a.GetAtomMapNum()})


@pytest.mark.parametrize("rule", ALL, ids=lambda r: r.id)
def test_examples_with_plain_rdkit(rule: Rule) -> None:
    """The reaction alone, without the engine: expected products, heavy-atom change, no new charge, negatives."""
    rxn = AllChem.ReactionFromSmarts(rule.reaction)
    multiple = False
    for example in rule.tests.positive:
        parent = Chem.MolFromSmiles(example.parent)
        assert parent is not None, example.parent
        outcomes = rxn.RunReactants((parent,))
        assert outcomes, f"{example.parent} gives nothing"
        multiple |= len(outcomes) >= 2
        actual = set()
        for (product,) in outcomes:
            product = Chem.Mol(product)
            for atom in product.GetAtoms():
                atom.SetAtomMapNum(0)
            Chem.SanitizeMol(product)
            assert product.GetNumHeavyAtoms() - parent.GetNumHeavyAtoms() == rule.net_heavy_atom_change
            assert _charge(product) <= _charge(parent) and _radicals(product) <= _radicals(parent)
            actual.add(_tautomer(product))
        assert actual == {_tautomer(s) for s in example.products}, example.parent
    assert multiple, "one positive example should fit in several places"
    for example in rule.tests.negative:
        parent = Chem.MolFromSmiles(example.parent)
        assert parent is not None and not rxn.RunReactants((parent,)), example.parent


@pytest.mark.parametrize("rule", ALL, ids=lambda r: r.id)
def test_examples_through_the_engine(rule: Rule) -> None:
    """The engine finds every product the author listed, no more, and none for the negative examples."""
    for example in rule.tests.positive:
        target, err = bio.whole_molecule(example.parent)
        assert target is not None, err
        work = bio.neutralised(target.mol)
        run = bio.run_rule(rule, target, work, ENUMERATOR, parent_key=bio._tautomer_key(work, ENUMERATOR))
        assert run.positions >= 1 and not run.skipped, example.parent
        assert {p.key for p in run.products} == {_tautomer(s) for s in example.products}, example.parent
        assert len({p.key for p in run.products}) == len(run.products)  # no duplicates left
    for example in rule.tests.negative:
        target, err = bio.whole_molecule(example.parent)
        assert target is not None, err
        work = bio.neutralised(target.mol)
        run = bio.run_rule(rule, target, work, ENUMERATOR)
        assert run.positions == 0 and run.products == [], example.parent
