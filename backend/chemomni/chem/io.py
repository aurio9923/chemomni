"""Structure files in, tab contents out (M4: import and export).

Ketcher opens one structure per file well, but it stacks the records of a multi-record SDF on top of each
other and its dialog hangs on a multi-line SMILES list. Files that can hold several structures come through
here: they become one tab laid out on a grid. Export writes what Ketcher's "Save as" cannot: one record per
structure with properties and the compound number.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from pathlib import Path

from rdkit import Chem
from rdkit.Chem import rdDepictor

from chemomni.chem.compounds import build_compounds
from chemomni.chem.layout import centred, grid, smiles_or_none, to_ketcher_scale
from chemomni.chem.props import compute_properties, parse_smiles

MAX_STRUCTURES = 200  # one Ketcher canvas stays usable up to about this many structures
SMILES_HEADERS = {"smiles", "smi", "canonical_smiles", "isomeric_smiles", "canonicalsmiles", "isomericsmiles"}
NAME_HEADERS = {"name", "id", "compound", "compound_id", "title", "molecule_chembl_id"}
Z_TOLERANCE = 1e-4


class StructureFileError(ValueError):
    """The file holds nothing that can be imported (the message is shown to the user)."""


@dataclass
class Structure:
    mol: Chem.Mol
    name: str | None = None


@dataclass
class Imported:
    structures: list[Structure]
    skipped: list[str] = field(default_factory=list)
    found: int = 0  # structures in the file before the cap


@dataclass
class TabContent:
    molfile: str  # V3000, Ketcher's scale
    smiles: list[str]


# ---------------------------------------------------------------------------------------- import


def basename(filename: str) -> str:
    """The file name without folders; a Windows path counts too, whatever system the server runs on."""
    return re.split(r"[\\/]", filename)[-1]


def parse_import(filename: str, text: str) -> Imported:
    """Read a structure file: molfile, SDF, a SMILES list (``.smi`` / ``.txt``) or a CSV / TSV table."""
    text = text.replace("\r\n", "\n").replace("\r", "\n").lstrip("﻿")
    if not text.strip():
        raise StructureFileError("the file is empty")
    suffix = Path(basename(filename)).suffix.lower()
    if suffix in {".csv", ".tsv"}:
        found, skipped = _read_table(text, "\t" if suffix == ".tsv" else ",")
    elif suffix in {".sdf", ".sd", ".mol"} or "M  END" in text or "$$$$" in text:
        found, skipped = _read_sdf(text)
    else:
        found, skipped = _read_smiles_lines(text)
    if not found:
        detail = f" ({skipped[0]})" if skipped else ""
        raise StructureFileError(f"no structure could be read from {basename(filename) or 'the file'}{detail}")
    total = len(found)
    if total > MAX_STRUCTURES:
        skipped.append(f"only the first {MAX_STRUCTURES} of {total} structures were imported")
        found = found[:MAX_STRUCTURES]
    return Imported(found, skipped, total)


def _read_sdf(text: str) -> tuple[list[Structure], list[str]]:
    found: list[Structure] = []
    skipped: list[str] = []
    records = [r for r in re.split(r"^\$\$\$\$[^\n]*\n?", text, flags=re.M) if r.strip()]
    for number, record in enumerate(records, start=1):
        name = record.split("\n", 1)[0].strip() or None
        label = f"record {number}" + (f" ({name})" if name else "")
        try:
            mol = Chem.MolFromMolBlock(record, sanitize=False, removeHs=False)
        except Exception:
            mol = None
        if mol is None:
            skipped.append(f"{label}: not a readable molfile")
        elif mol.GetNumAtoms() == 0:
            skipped.append(f"{label}: no atoms")
        else:
            found.append(Structure(mol, name))
    return found, skipped


def _read_smiles_lines(text: str) -> tuple[list[Structure], list[str]]:
    found: list[Structure] = []
    skipped: list[str] = []
    first = True
    for number, line in enumerate(text.split("\n"), start=1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 1)
        smiles, name = parts[0], (parts[1].strip() if len(parts) > 1 else None)
        header = first and smiles.casefold() in SMILES_HEADERS
        first = False
        if header:
            continue
        mol, error = parse_smiles(smiles)
        if mol is None:
            skipped.append(f"line {number}: {error}")
        else:
            found.append(Structure(mol, name))
    return found, skipped


def _read_table(text: str, delimiter: str) -> tuple[list[Structure], list[str]]:
    rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
    header = [c.strip().casefold() for c in rows[0]] if rows else []
    column = next((i for i, c in enumerate(header) if c in SMILES_HEADERS), None)
    if column is None:
        column = next((i for i, c in enumerate(header) if "smiles" in c), None)
    if column is None:
        raise StructureFileError('no SMILES column found: the first row needs a header such as "smiles"')
    name_column = next((i for i, c in enumerate(header) if c in NAME_HEADERS), None)
    found: list[Structure] = []
    skipped: list[str] = []
    for number, row in enumerate(rows[1:], start=2):
        if not any(cell.strip() for cell in row):
            continue
        smiles = row[column].strip() if column < len(row) else ""
        name = row[name_column].strip() if name_column is not None and name_column < len(row) else None
        mol, error = parse_smiles(smiles)
        if mol is None:
            skipped.append(f"row {number}: {error}")
        else:
            found.append(Structure(mol, name or None))
    return found, skipped


def _extent(mol: Chem.Mol) -> tuple[bool, bool]:
    """(flat, placed): no atom has a z coordinate, and the atoms are not all at the origin."""
    if not mol.GetNumConformers():
        return True, False
    conf = mol.GetConformer()
    points = [conf.GetAtomPosition(i) for i in range(mol.GetNumAtoms())]
    flat = all(abs(p.z) <= Z_TOLERANCE for p in points)
    placed = any(abs(p.x) > 1e-6 or abs(p.y) > 1e-6 for p in points)
    return flat, placed


def _drawable(mol: Chem.Mol) -> Chem.Mol:
    """2D coordinates: the file's own when it has flat ones, computed when it has none or is 3D."""
    flat, placed = _extent(mol)
    if flat and placed:
        return Chem.Mol(mol)
    work = Chem.Mol(mol)
    try:
        Chem.SanitizeMol(work)
    except Exception:
        work.UpdatePropertyCache(strict=False)
        Chem.FastFindRings(work)
    if not flat:
        try:
            Chem.AssignStereochemistryFrom3D(work)
            work = Chem.RemoveHs(work, sanitize=False)  # explicit hydrogens of a 3D file only add noise
        except Exception:
            pass
    work.RemoveAllConformers()
    rdDepictor.Compute2DCoords(work)
    return work


def _v3000(mol: Chem.Mol) -> str:
    try:
        return Chem.MolToV3KMolBlock(mol)
    except Exception:  # aromatic flags that cannot be kekulized: keep the bonds as they are
        return Chem.MolToV3KMolBlock(mol, kekulize=False)


def tab_content(imported: Imported) -> TabContent:
    """One molfile for the tab: a single structure as it is drawn, several on a grid (reading order)."""
    mols = [centred(to_ketcher_scale(_drawable(s.mol))) for s in imported.structures]
    molfile = _v3000(mols[0]) if len(mols) == 1 else _v3000(grid(mols))
    smiles = [s for s in (smiles_or_none(m) for m in mols) if s]
    return TabContent(molfile, smiles)


# ---------------------------------------------------------------------------------------- export

EXPORT_FORMATS = {
    "sdf": ("chemical/x-mdl-sdfile", "sdf"),
    "csv": ("text/csv", "csv"),
    "smi": ("chemical/x-daylight-smiles", "smi"),
}
COLUMNS = (
    "smiles",
    "formula",
    "mw",
    "clogp",
    "tpsa",
    "hbd",
    "hba",
    "rotatable_bonds",
    "qed",
    "sa_score",
    "lipinski_violations",
    "pains_alerts",
    "brenk_alerts",
    "inchikey",
)


@dataclass
class Exported:
    content: str
    extension: str
    mime: str
    count: int
    skipped: list[str] = field(default_factory=list)


def _cell(value: object) -> str:
    if isinstance(value, list):
        return "; ".join(str(v) for v in value)
    return "" if value is None else str(value)


def export_structures(fmt: str, molfile: str, ket: str | None = None) -> Exported:
    """The structures of a tab as SDF (with properties), CSV or a SMILES list. Numbers come from the KET."""
    if fmt not in EXPORT_FORMATS:
        raise ValueError(f"unknown format {fmt!r}; use one of {', '.join(EXPORT_FORMATS)}")
    mime, extension = EXPORT_FORMATS[fmt]
    table = build_compounds(ket, molfile)
    skipped: list[str] = []
    rows: list[tuple[str, Chem.Mol, dict[str, object]]] = []
    for c in table.compounds:
        if c.is_query:
            skipped.append(f"structure {c.index} is a query (any atom or atom list) and was not exported")
        elif c.smiles is None:
            skipped.append(f"structure {c.index} could not be read as a molecule and was not exported")
        else:
            row = compute_properties(c.smiles)
            values = {k: row.get(k) for k in COLUMNS} if row["ok"] else {"smiles": c.smiles}
            rows.append((c.label or "", c.mol, values))
    if not rows:
        raise ValueError("the tab holds no structure that can be exported")

    buf = io.StringIO()
    if fmt == "sdf":
        for label, mol, values in rows:
            work = Chem.Mol(mol)
            work.SetProp("_Name", label)
            try:
                block = Chem.MolToMolBlock(work)
            except Exception:
                block = Chem.MolToMolBlock(work, kekulize=False)
            buf.write(block)
            fields = {"Number": label, **{k: v for k, v in values.items() if v not in (None, "", [])}}
            for key, value in fields.items():
                if _cell(value):
                    buf.write(f"> <{key}>\n{_cell(value)}\n\n")
            buf.write("$$$$\n")
    elif fmt == "csv":
        writer = csv.writer(buf, lineterminator="\n")
        writer.writerow(["number", *COLUMNS])
        for label, _, values in rows:
            writer.writerow([label, *(_cell(values.get(k)) for k in COLUMNS)])
    else:
        for index, (label, _, values) in enumerate(rows, start=1):
            buf.write(f"{values['smiles']} {label or index}\n")
    return Exported(buf.getvalue(), extension, mime, len(rows), skipped)
