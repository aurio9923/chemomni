"""The bioisostere rule table (M5): one YAML file per family under ``chemomni/data/bioisosteres/``.

The files are vendored as they were delivered. Three edits were made here on the user's decision (PLAN Q62,
each marked by a comment in the YAML); any other wrong rule is reported to its author instead. They are read
once into an in-memory table. Only rules with ``status: active`` can be used;
an ``excluded`` rule stays in the table (so tests can prove it is never offered) but ``RuleTable.get``
answers as if it did not exist.

Each rule is one RDKit reaction SMARTS with a single reactant template and a single product template,
written for neutral molecules. The atoms that carry a map number are the attachment points and stay
as they are; every other atom of the reactant template is what the rule replaces. The engine
(``chem.bioisostere``) relies on that convention, so the loader checks it.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from rdkit.Chem import AllChem

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "bioisosteres"

RuleClass = Literal["classical", "nonclassical", "ring_replacement", "substituent", "linker"]
RiskTopic = Literal[
    "ionization",
    "permeability",
    "metabolism",
    "solubility",
    "lipophilicity",
    "potency_risk",
    "synthesis",
    "toxicity_alert",
    "other",
]


class RuleTableError(ValueError):
    """The rule files are not what the engine expects. Raised at startup, never while chatting."""


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class Names(_Frozen):
    en: list[str] = Field(min_length=1)
    ja: list[str] = Field(min_length=1)


class Risk(_Frozen):
    """A qualitative caution. ``confidence`` is the author's own judgement, not evidence: never show it as odds."""

    topic: RiskTopic
    note: str = Field(min_length=1)
    confidence: Literal["high", "medium", "low"]


class Reference(_Frozen):
    authors: str
    year: int
    journal: str
    title: str
    doi: str | None = None
    verified: bool


class PositiveTest(_Frozen):
    parent: str
    products: list[str] = Field(min_length=1)
    note: str


class NegativeTest(_Frozen):
    parent: str
    note: str


class RuleTests(_Frozen):
    positive: list[PositiveTest]
    negative: list[NegativeTest]


class Review(_Frozen):
    decision: Literal["ok", "conditional", "rejected"]
    date: str
    note: str = ""


class Rule(_Frozen):
    id: str = Field(pattern=r"^[a-z_]+$")
    family: str
    names: Names
    reaction: str = Field(min_length=1)
    attachments: int = Field(ge=1)
    rule_class: RuleClass = Field(alias="class")
    context: str
    creates_stereocenter: bool
    net_heavy_atom_change: int
    risks: list[Risk]
    refs: list[Reference]
    tests: RuleTests
    status: Literal["active", "excluded"]
    review: Review

    @property
    def name(self) -> str:
        """The name shown in a candidate's rationale: the first English one."""
        return self.names.en[0]

    @property
    def name_ja(self) -> str:
        return self.names.ja[0]


@dataclass(frozen=True)
class RuleTable:
    everything: tuple[Rule, ...]  # active and excluded, in file order

    @property
    def active(self) -> tuple[Rule, ...]:
        return tuple(r for r in self.everything if r.status == "active")

    @property
    def families(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(r.family for r in self.active))

    def get(self, rule_id: str) -> Rule | None:
        """An active rule by id. An excluded rule is treated as unknown."""
        return next((r for r in self.active if r.id == rule_id), None)

    def of_family(self, family: str) -> tuple[Rule, ...]:
        return tuple(r for r in self.active if r.family == family)


def _check_reaction(rule: Rule) -> str | None:
    """What the engine needs of the SMARTS; None when it is fine."""
    try:
        rxn = AllChem.ReactionFromSmarts(rule.reaction)
    except Exception as exc:
        return f"reaction SMARTS cannot be parsed: {exc}"
    if rxn is None or rxn.GetNumReactantTemplates() != 1 or rxn.GetNumProductTemplates() != 1:
        return "reaction needs exactly one reactant template and one product template"
    reactant, product = rxn.GetReactantTemplate(0), rxn.GetProductTemplate(0)
    kept = {a.GetAtomMapNum() for a in reactant.GetAtoms() if a.GetAtomMapNum()}
    if kept != {a.GetAtomMapNum() for a in product.GetAtoms() if a.GetAtomMapNum()}:
        return "atom maps differ between the reactant and the product template"
    if not any(a.GetAtomMapNum() == 0 for a in reactant.GetAtoms()):
        return "every reactant atom is mapped, so the rule replaces nothing"
    return None


def load_rules(directory: Path = DATA_DIR) -> RuleTable:
    """Read and check every ``*.yaml`` of ``directory`` (each file is a list of rules named after its family)."""
    files = sorted(directory.glob("*.yaml"))
    if not files:
        raise RuleTableError(f"no rule files in {directory}")
    rules: list[Rule] = []
    seen: dict[str, str] = {}
    for path in files:
        try:
            data: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise RuleTableError(f"{path.name}: cannot be read: {exc}") from exc
        if not isinstance(data, list) or not data:
            raise RuleTableError(f"{path.name}: expected a non-empty list of rules")
        for position, entry in enumerate(data, start=1):
            named = f" ({entry['id']})" if isinstance(entry, dict) and "id" in entry else ""
            where = f"{path.name} #{position}{named}"
            try:
                rule = Rule.model_validate(entry)
            except ValidationError as exc:
                first = exc.errors()[0]
                raise RuleTableError(f"{where}: {'.'.join(map(str, first['loc']))}: {first['msg']}") from exc
            if rule.family != path.stem:
                raise RuleTableError(f"{where}: family {rule.family!r} does not match the file name")
            if rule.id in seen:
                raise RuleTableError(f"{where}: id already used in {seen[rule.id]}")
            problem = _check_reaction(rule)
            if problem:
                raise RuleTableError(f"{where}: {problem}")
            seen[rule.id] = path.name
            rules.append(rule)
    return RuleTable(tuple(rules))


@lru_cache(maxsize=1)
def rules() -> RuleTable:
    """The table the application uses, loaded on first use (and at startup, so a broken file stops the server)."""
    return load_rules()


def ids(table: Iterable[Rule]) -> list[str]:
    return [r.id for r in table]
