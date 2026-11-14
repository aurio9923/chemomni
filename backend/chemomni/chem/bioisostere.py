"""Bioisostere replacements, enumerated exhaustively by rule (M5, decision Q60).

The LLM only chooses *which* rules to try. This module finds every place a rule fits, builds each
product with RDKit, and merges the ones that are the same molecule, so nothing depends on the model's
memory of SMARTS or SMILES.

How a rule is read: its reactant template has attachment atoms (they carry a map number and stay) and
the other atoms, which the rule replaces. A *position* is one set of replaced atoms. With a selection,
only positions whose replaced atoms all lie inside the selection are used, so "the carboxylic acid"
means the acid the user selected.

Three things that look like details and are not:
- The rules are written for neutral molecules, so the input is neutralised first (a carboxylate is
  an acid, an ammonium is an amine). Atom order is kept, so atom numbers still mean what the user drew;
  the grid is still laid out on the drawing as it was, and the products are the neutral forms.
- Duplicates are found by the canonical *tautomer*: an NH-tetrazole reached from two directions is
  one molecule even if RDKit writes the hydrogen on different nitrogens.
- A ring template matches the same ring 6 or 12 times (rotations, mirror images); positions are the
  distinct sets of replaced atoms, not the raw matches.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from itertools import zip_longest

from rdkit import Chem
from rdkit.Chem import AllChem
from rdkit.Chem.MolStandardize import rdMolStandardize

from chemomni.chem.bioisostere_rules import Rule
from chemomni.chem.props import canonical_smiles, parse_smiles
from chemomni.chem.scan import Target

MAX_CANDIDATES = 20  # one grid; more are counted and reported
MAX_OUTCOMES = 2000  # RunReactants matches per rule (a ring template matches each ring many times)
MAX_RATIONALE = 160
REACT_IDX = "react_atom_idx"  # set by RDKit on a product atom: the reactant atom it came from


@dataclass
class Product:
    smiles: str  # canonical SMILES of the product as built
    key: str  # canonical tautomer, to recognise the same molecule
    positions: int = 1  # how many positions gave this molecule (more than one: they are symmetric)


@dataclass
class RuleRun:
    """What one rule does to one target."""

    rule: Rule
    positions: int = 0  # distinct places inside the target atoms
    outside: int = 0  # places that exist but lie (partly) outside the target atoms
    products: list[Product] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)  # products that could not be built, with the reason

    @property
    def matches(self) -> bool:
        return self.positions > 0


@dataclass
class Candidate:
    smiles: str
    rationale: str
    rule_ids: list[str]


@dataclass
class Applied:
    ok: bool
    errors: list[str] = field(default_factory=list)
    candidates: list[Candidate] = field(default_factory=list)  # at most MAX_CANDIDATES, interleaved by rule
    total: int = 0  # distinct products before the cap
    runs: list[RuleRun] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)


# ---------------------------------------------------------------- targets


def whole_molecule(smiles: str) -> tuple[Target | None, str | None]:
    """A target made of a whole molecule given as SMILES (every atom may change)."""
    mol, err = parse_smiles(smiles)
    if mol is None:
        return None, err
    if len(Chem.GetMolFrags(mol)) > 1:
        return None, "it has several components (a salt or a mixture); select the atoms of the one to change"
    return Target(mol, list(range(mol.GetNumAtoms())), {i: i + 1 for i in range(mol.GetNumAtoms())}), None


def neutralised(mol: Chem.Mol) -> Chem.Mol:
    """The neutral form the rules are written for. Atom order is unchanged; the input is not modified."""
    try:
        work = rdMolStandardize.Uncharger(canonicalOrder=True).uncharge(mol)
        Chem.SanitizeMol(work)
        return work
    except Exception:
        return Chem.Mol(mol)


# ---------------------------------------------------------------- one rule


def _tautomer_key(mol: Chem.Mol, enumerator: rdMolStandardize.TautomerEnumerator) -> str:
    try:
        return Chem.MolToSmiles(enumerator.Canonicalize(mol))
    except Exception:
        return Chem.MolToSmiles(mol)


def _first_line(exc: Exception) -> str:
    text = str(exc).strip().splitlines()
    return text[0] if text else type(exc).__name__


def run_rule(
    rule: Rule,
    target: Target,
    work: Chem.Mol,
    enumerator: rdMolStandardize.TautomerEnumerator,
    *,
    parent_key: str | None = None,
) -> RuleRun:
    """Every distinct product of ``rule`` on ``work`` (the neutralised ``target.mol``), inside ``target.atoms``."""
    run = RuleRun(rule)
    rxn = AllChem.ReactionFromSmarts(rule.reaction)
    allowed = set(target.atoms)
    everything = set(range(work.GetNumAtoms()))
    by_position: dict[frozenset[int], list[Chem.Mol]] = {}
    for outcome in rxn.RunReactants((work,), maxProducts=MAX_OUTCOMES):
        product = outcome[0]
        kept = {a.GetIntProp(REACT_IDX) for a in product.GetAtoms() if a.HasProp(REACT_IDX)}
        by_position.setdefault(frozenset(everything - kept), []).append(product)

    products: dict[str, Product] = {}
    for removed in sorted(by_position, key=sorted):
        if not removed <= allowed:
            run.outside += 1
            continue
        run.positions += 1
        seen_here: set[str] = set()
        for raw in by_position[removed]:
            built = Chem.Mol(raw)
            for atom in built.GetAtoms():
                atom.SetAtomMapNum(0)
            try:
                Chem.SanitizeMol(built)
                smiles = canonical_smiles(built)
            except Exception as exc:
                reason = _first_line(exc)
                if "kekulize" in reason.lower():
                    reason = "would break an aromatic ring"
                run.skipped.append(reason)
                continue
            if "." in smiles:
                run.skipped.append("the product fell into several pieces")
                continue
            key = _tautomer_key(built, enumerator)
            if key == parent_key or key in seen_here:
                continue
            seen_here.add(key)
            if key in products:
                products[key].positions += 1
            else:
                products[key] = Product(smiles, key)
    run.products = list(products.values())
    return run


# ---------------------------------------------------------------- many rules


def _parent_key(work: Chem.Mol, enumerator: rdMolStandardize.TautomerEnumerator) -> str:
    return _tautomer_key(work, enumerator)


def find(target: Target, rules: Iterable[Rule]) -> list[RuleRun]:
    """The rules that fit the target, with how often (rules that do not match are left out)."""
    work = neutralised(target.mol)
    enumerator = rdMolStandardize.TautomerEnumerator()
    parent_key = _parent_key(work, enumerator)
    runs = [run_rule(rule, target, work, enumerator, parent_key=parent_key) for rule in rules]
    return [r for r in runs if r.matches or r.outside]


def rationale(rule: Rule, index: int, of: int, positions: int, also: Sequence[Rule] = ()) -> str:
    """One line for the grid: the rule id and name, which variant it is, and what to keep in mind."""
    parts = [f"{index} of {of}"] if of > 1 else []
    if positions > 1:
        parts.append(f"{positions} equivalent sites")
    if rule.creates_stereocenter:
        parts.append("new stereocentre, racemic")
    if also:
        parts.append("also " + ", ".join(r.id for r in also))
    text = f"{rule.id}: {rule.name}" + (f" ({'; '.join(parts)})" if parts else "")
    return text if len(text) <= MAX_RATIONALE else text[: MAX_RATIONALE - 3] + "..."


def apply(target: Target, rules: Sequence[Rule]) -> Applied:
    """Apply ``rules`` at every position inside the target and gather the distinct products."""
    result = Applied(ok=False)
    if not rules:
        result.errors = ["no rules given"]
        return result
    work = neutralised(target.mol)
    enumerator = rdMolStandardize.TautomerEnumerator()
    parent_key = _parent_key(work, enumerator)
    result.runs = [run_rule(rule, target, work, enumerator, parent_key=parent_key) for rule in rules]
    for run in result.runs:
        for reason in dict.fromkeys(run.skipped):
            result.skipped.append({"smiles": run.rule.id, "reason": reason})

    # one molecule reached by several rules is shown once, under the first rule
    first: dict[str, tuple[RuleRun, int]] = {}
    also: dict[str, list[Rule]] = {}
    per_rule: list[list[str]] = []
    for run in result.runs:
        keys: list[str] = []
        for i, product in enumerate(run.products):
            if product.key in first:
                also.setdefault(product.key, []).append(run.rule)
                continue
            first[product.key] = (run, i)
            keys.append(product.key)
        per_rule.append(keys)
    ordered = [key for group in zip_longest(*per_rule) for key in group if key is not None]  # round robin by rule
    result.total = len(ordered)
    if not ordered:
        result.errors = [_nothing_to_show(result.runs)]
        return result
    for key in ordered[:MAX_CANDIDATES]:
        run, i = first[key]
        product = run.products[i]
        others = also.get(key, [])
        result.candidates.append(
            Candidate(
                product.smiles,
                rationale(run.rule, i + 1, len(run.products), product.positions, others),
                [run.rule.id, *(r.id for r in others)],
            )
        )
    result.ok = True
    return result


def _nothing_to_show(runs: Sequence[RuleRun]) -> str:
    inside = [r.rule.id for r in runs if r.matches]
    outside = [r.rule.id for r in runs if not r.matches and r.outside]
    if inside:
        return f"{', '.join(inside)} matched, but every product equals the molecule or could not be built"
    if outside:
        verb = "fits" if len(outside) == 1 else "fit"
        return (
            f"{', '.join(outside)} {verb} only outside the selected atoms; select the whole part the rule replaces "
            "(for a carboxylic acid: the carbon and both oxygens), or select nothing to use the whole molecule"
        )
    return "none of these rules fits this molecule; call find_bioisosteres to see which ones do"
