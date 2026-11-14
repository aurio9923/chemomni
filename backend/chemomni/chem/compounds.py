"""Compounds on a tab, addressed by the numbers the user wrote next to them (PLAN Q57).

A number is an ordinary Ketcher text object ("1", "2", "1a"). The KET document gives every molecule's
bounding box and every text's position; a label belongs to the structure it is closest to. The
chemistry comes from the editor's V3000 molfile, matched to the KET molecules by atom coordinates
(never by order). Everything here is pure RDKit/JSON, so it is tested without a browser.

KET units: bond length 1.0, y axis points up (a smaller y is lower on the screen). The molfile
Ketcher exports uses the same coordinates.
"""

from __future__ import annotations

import json
import re
from contextlib import suppress
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from rdkit import Chem

from chemomni.chem.props import canonical_smiles

Box = tuple[float, float, float, float]  # xmin, ymin, xmax, ymax

LABEL_RE = re.compile(r"^\(?\s*(\d{1,3}[A-Za-z]{0,2})\s*\)?$")
REF_TOKEN_RE = re.compile(r"\d{1,3}[A-Za-z]{0,2}")
MAX_LABEL_GAP = 4.0  # a label further than this from every structure belongs to none
AMBIGUITY_RATIO = 1.5  # the runner-up must be this much further away (plus AMBIGUITY_SLACK)
AMBIGUITY_SLACK = 0.25
ATOM_TOLERANCE = 1e-3  # KET and molfile coordinates are the same numbers, rounded
ROW_TOLERANCE = 2.0  # structures whose centres are this close in y share a row when numbering
LABEL_GAP_BELOW = 0.8  # where a generated label goes, below the lowest atom
CHAR_WIDTH = 0.18  # width of one character of Ketcher's default text, in bond lengths
LABEL_HEIGHT = 0.375


@dataclass(frozen=True)
class KetMolecule:
    atoms: tuple[tuple[float, float], ...]
    box: Box


@dataclass(frozen=True)
class KetText:
    text: str
    box: Box

    @property
    def label(self) -> str | None:
        m = LABEL_RE.match(self.text.strip())
        return m.group(1) if m else None


def _box(points: list[tuple[float, float]]) -> Box:
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return (min(xs), min(ys), max(xs), max(ys))


def _load(ket: str | None) -> dict[str, Any] | None:
    if not ket:
        return None
    try:
        doc = json.loads(ket)
    except json.JSONDecodeError:
        return None
    return doc if isinstance(doc, dict) and isinstance(doc.get("root"), dict) else None


def read_ket(ket: str | None) -> tuple[list[KetMolecule], list[KetText]]:
    """Molecules (with bounding boxes) and texts of a KET document; empty for anything unreadable."""
    doc = _load(ket)
    if doc is None:
        return [], []
    molecules: list[KetMolecule] = []
    texts: list[KetText] = []
    for node in doc["root"].get("nodes", []):
        if "$ref" in node:
            body = doc.get(node["$ref"])
            if not isinstance(body, dict) or body.get("type") != "molecule":
                continue
            points = [
                (float(a["location"][0]), float(a["location"][1]))
                for a in body.get("atoms", [])
                if isinstance(a.get("location"), list) and len(a["location"]) >= 2
            ]
            if points:
                molecules.append(KetMolecule(tuple(points), _box(points)))
        elif node.get("type") == "text":
            bb = node.get("boundingBox") or {}
            paragraphs = [
                "".join(p.get("text", "") for p in para.get("parts", [])) for para in node.get("paragraphs", [])
            ]
            text = "\n".join(paragraphs).strip()
            if text and {"x", "y", "width", "height"} <= bb.keys():
                texts.append(KetText(text, (bb["x"], bb["y"], bb["x"] + bb["width"], bb["y"] + bb["height"])))
    return molecules, texts


def box_gap(a: Box, b: Box) -> float:
    """Shortest distance between two rectangles (0 when they overlap)."""
    dx = max(a[0] - b[2], b[0] - a[2], 0.0)
    dy = max(a[1] - b[3], b[1] - a[3], 0.0)
    return (dx * dx + dy * dy) ** 0.5


@dataclass
class Assignment:
    labels: list[str | None]  # per molecule, in KET order
    ambiguous: list[str] = field(default_factory=list)  # labels that may belong to a neighbour
    unattached: list[str] = field(default_factory=list)  # label-like texts that no structure claimed
    duplicated: list[str] = field(default_factory=list)  # labels written next to several structures


def assign_labels(molecules: list[KetMolecule], texts: list[KetText]) -> Assignment:
    """Give every structure at most one label and every label at most one structure, nearest pairs first."""
    candidates = sorted(
        (box_gap(t.box, m.box), ti, mi) for ti, t in enumerate(texts) if t.label for mi, m in enumerate(molecules)
    )
    labels: list[str | None] = [None] * len(molecules)
    chosen: dict[int, tuple[int, float]] = {}  # text -> (molecule, gap)
    used_molecules: set[int] = set()
    for gap, ti, mi in candidates:
        if gap > MAX_LABEL_GAP or ti in chosen or mi in used_molecules:
            continue
        chosen[ti] = (mi, gap)
        used_molecules.add(mi)
        labels[mi] = texts[ti].label
    result = Assignment(labels)
    for ti, t in enumerate(texts):
        if not t.label:
            continue
        if ti not in chosen:
            result.unattached.append(t.label)
            continue
        mi, gap = chosen[ti]
        others = [g for g, tj, mj in candidates if tj == ti and mj != mi and mj not in used_molecules]
        if others and min(others) <= gap * AMBIGUITY_RATIO + AMBIGUITY_SLACK:
            result.ambiguous.append(t.label)
    seen: set[str] = set()
    for label in (x for x in labels if x):
        if label.casefold() in seen and label not in result.duplicated:
            result.duplicated.append(label)
        seen.add(label.casefold())
    return result


# ------------------------------------------------------------------------------------ the table


@dataclass
class Compound:
    index: int  # 1-based position on the tab
    label: str | None
    mol: Chem.Mol  # hydrogens removed when possible, 2D coordinates kept
    smiles: str | None  # None for a query (atom lists, any atom, ...)
    smarts: str | None  # only for a query
    is_query: bool

    @property
    def shown(self) -> str:
        return self.smiles or f"query {self.smarts}"


@dataclass
class CompoundTable:
    compounds: list[Compound] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def labelled(self) -> list[Compound]:
        return [c for c in self.compounds if c.label]

    def resolve(self, ref: str) -> tuple[Compound | None, str | None]:
        """The compound a user's number points at, or an error message the model can act on."""
        tokens = REF_TOKEN_RE.findall(ref or "")
        if len(tokens) != 1:
            return None, f"'{ref}' is not a compound number"
        wanted = tokens[0].casefold()
        hits = [c for c in self.compounds if c.label and c.label.casefold() == wanted]
        if len(hits) == 1:
            return hits[0], None
        known = ", ".join(c.label for c in self.labelled if c.label)
        if len(hits) > 1:
            return None, f"number {tokens[0]} is written next to several structures; ask the user which one"
        if known:
            return None, f"no structure is numbered {tokens[0]} on the active tab (numbered: {known})"
        return None, (
            f"no structure is numbered {tokens[0]}: the active tab has no numbers. Ask the user to write a "
            "number next to each structure with the text tool, or to press 'Number compounds'"
        )


def _is_query(mol: Chem.Mol) -> bool:
    return any(a.HasQuery() for a in mol.GetAtoms()) or any(b.HasQuery() for b in mol.GetBonds())


def _prepare(frag: Chem.Mol, index: int, label: str | None) -> Compound:
    query = _is_query(frag)
    work = Chem.Mol(frag)
    try:
        Chem.SanitizeMol(work)
    except Exception:
        work.UpdatePropertyCache(strict=False)
        Chem.FastFindRings(work)
    with suppress(Exception):
        work = Chem.RemoveHs(work, sanitize=False)
    if query:
        return Compound(index, label, work, None, Chem.MolToSmarts(work), True)
    try:
        smiles: str | None = canonical_smiles(work)
    except Exception:
        smiles = None
    return Compound(index, label, work, smiles, None, False)


def _atoms_of(mol: Chem.Mol) -> list[tuple[float, float]]:
    conf = mol.GetConformer() if mol.GetNumConformers() else None
    if conf is None:
        return []
    return [(conf.GetAtomPosition(i).x, conf.GetAtomPosition(i).y) for i in range(mol.GetNumAtoms())]


def _same_atoms(ket_atoms: tuple[tuple[float, float], ...], frag_atoms: list[tuple[float, float]]) -> bool:
    if len(ket_atoms) != len(frag_atoms):
        return False
    free = list(frag_atoms)
    for x, y in ket_atoms:
        for i, (fx, fy) in enumerate(free):
            if abs(fx - x) <= ATOM_TOLERANCE and abs(fy - y) <= ATOM_TOLERANCE:
                del free[i]
                break
        else:
            return False
    return True


@lru_cache(maxsize=16)
def build_compounds(ket: str | None, molfile: str | None) -> CompoundTable:
    """Every structure of the tab with its number, if it has one. Read-only: the result is cached."""
    table = CompoundTable()
    if not molfile:
        return table
    mol = Chem.MolFromMolBlock(molfile, sanitize=False, removeHs=False)
    if mol is None or mol.GetNumAtoms() == 0:
        return table
    frags = list(Chem.GetMolFrags(mol, asMols=True, sanitizeFrags=False))
    frag_atoms = [_atoms_of(f) for f in frags]
    molecules, texts = read_ket(ket)
    assignment = assign_labels(molecules, texts)

    placed: dict[int, str | None] = {}
    order: list[int] = []
    for km, label in zip(molecules, assignment.labels, strict=True):
        match = next((i for i, fa in enumerate(frag_atoms) if i not in placed and _same_atoms(km.atoms, fa)), None)
        if match is not None:
            placed[match] = label
            order.append(match)
    order += [i for i in range(len(frags)) if i not in placed]
    for position, i in enumerate(order, start=1):
        table.compounds.append(_prepare(frags[i], position, placed.get(i)))

    if molecules and len(order) > len(placed):
        table.notes.append("some structures could not be matched to the editor drawing, so they have no number")
    for label in assignment.ambiguous:
        table.notes.append(f"the number {label} lies between two structures; the nearest one was taken")
    for label in assignment.duplicated:
        table.notes.append(f"the number {label} is written next to several structures")
    for label in assignment.unattached:
        table.notes.append(f"the number {label} is not next to any unnumbered structure")
    return table


# ------------------------------------------------------------------------- "Number compounds"


def _reading_order(molecules: list[KetMolecule]) -> list[int]:
    """Top to bottom, then left to right (KET's y axis points up)."""
    rows: list[tuple[float, list[int]]] = []
    for i in sorted(range(len(molecules)), key=lambda i: -(molecules[i].box[1] + molecules[i].box[3])):
        cy = (molecules[i].box[1] + molecules[i].box[3]) / 2
        for row in rows:
            if abs(row[0] - cy) <= ROW_TOLERANCE:
                row[1].append(i)
                break
        else:
            rows.append((cy, [i]))
    return [i for _, members in rows for i in sorted(members, key=lambda i: molecules[i].box[0])]


def _text_node(text: str, x: float, y: float) -> dict[str, Any]:
    return {
        "type": "text",
        "boundingBox": {"x": x, "y": y, "width": CHAR_WIDTH * len(text), "height": LABEL_HEIGHT},
        "paragraphs": [{"parts": [{"text": text}], "indent": 0}],
        "indent": 0,
    }


def number_compounds(ket: str) -> tuple[str, list[str]]:
    """Add a number under every structure that has none, in reading order. Returns (KET, added labels).

    Existing numbers stay; new ones continue after the highest integer in use.
    """
    doc = _load(ket)
    if doc is None:
        return ket, []
    molecules, texts = read_ket(ket)
    assignment = assign_labels(molecules, texts)
    used = {t.label.casefold() for t in texts if t.label}
    top = max((int(re.match(r"\d+", t.label).group()) for t in texts if t.label), default=0)  # type: ignore[union-attr]
    added: list[str] = []
    for i in _reading_order(molecules):
        if assignment.labels[i]:
            continue
        top += 1
        while str(top) in used:
            top += 1
        label = str(top)
        used.add(label)
        x0, y0, x1, _ = molecules[i].box
        doc["root"]["nodes"].append(
            _text_node(label, (x0 + x1) / 2 - CHAR_WIDTH * len(label) / 2, y0 - LABEL_GAP_BELOW)
        )
        added.append(label)
    return json.dumps(doc), added
