---
name: rdkit
description: Cheminformatics toolkit for fine-grained molecular control. SMILES/SDF parsing, descriptors (MW, LogP, TPSA), fingerprints, substructure search, 2D/3D generation, similarity, reactions. For standard workflows with simpler interface, use datamol (wrapper around RDKit). Use rdkit for advanced control, custom sanitization, specialized algorithms.
license: BSD-3-Clause license
allowed-tools: Read Write Edit Bash
compatibility: Tested with RDKit 2026.03.6 on Python 3.13; bundled scripts require the rdkit package. No credentials or network are needed after installation. Use conda-forge for the broadest binary support or PyPI package `rdkit` for supported platform wheels; `rdkit-pypi` is the legacy PyPI name.
metadata:
  version: "1.5"
  last-reviewed: "2026-10-01"
  tested-rdkit: "2026.03.6"
  skill-author: K-Dense Inc.
---

# RDKit Cheminformatics Toolkit

## Overview

RDKit is a comprehensive cheminformatics library providing Python APIs for molecular analysis and manipulation. This skill provides guidance for reading/writing molecular structures, calculating descriptors, fingerprinting, substructure searching, chemical reactions, 2D/3D coordinate generation, and molecular visualization. Use this skill for drug discovery, computational chemistry, and cheminformatics research tasks.

**Tested stable baseline (reviewed 2026-10-01):** RDKit **2026.03.6**, distributed as `rdkit==2026.3.6` on PyPI; this is the current stable GitHub/PyPI release. Official installation docs continue to recommend conda-forge for most users, while cross-platform PyPI wheels are published under the `rdkit` package name. `rdkit-pypi` is the old PyPI package name and should only appear when maintaining legacy environments.

## Installation and Setup

In this repository RDKit is a dependency of `backend/` and is managed with uv
(`uv add rdkit` / `uv sync` inside `backend/`). Use the version pinned in
`backend/pyproject.toml` / `uv.lock`; do not install a different version ad hoc.
The upstream review below was done against 2026.03.6; check
`references/workflows_and_best_practices.md` for version-sensitive notes when the
project's version differs.

## Core Capabilities

Twelve capability areas, each with worked code, are documented in
[references/core_capabilities.md](references/core_capabilities.md):

| # | Area | Covers |
| --- | --- | --- |
| 1 | Molecular I/O and creation | SMILES, MOL files and blocks, InChI, SDF and SMILES suppliers, multithreaded reading, writers |
| 2 | Sanitization and validation | disabling automatic sanitization, manual and partial sanitization, detecting problems first |
| 3 | Analysis and properties | atom and bond iteration, ring information and SSSR, chirality and stereochemistry, fragments |
| 4 | Descriptors | MW, LogP, TPSA, H-bond donors/acceptors, rotatable bonds, aromatic rings, bulk calculation, drug-likeness |
| 5 | Fingerprints and similarity | topological, Morgan/ECFP via `rdFingerprintGenerator`, MACCS, atom pair, torsion, Avalon; Tanimoto and other metrics; Butina clustering |
| 6 | Substructure searching | SMARTS queries, match retrieval, and a library of common patterns |
| 7 | Chemical reactions | reaction SMARTS, applying reactions, reaction fingerprints |
| 8 | 2D and 3D coordinates | depiction, template alignment, ETKDG embedding, force-field optimization, RMSD, constrained embedding |
| 9 | Visualization | single and grid images, substructure highlighting, custom drawer options, Jupyter integration, fingerprint bit environments |
| 10 | Molecular modification | explicit hydrogens, Kekulization, aromaticity, substructure replacement, charge neutralization |
| 11 | Hashes and standardization | Murcko scaffold and canonical hashes, regioisomer hashes, randomized SMILES for augmentation |
| 12 | Pharmacophore and 3D features | feature factories and feature extraction |

Worked workflows and the performance, thread-safety, and version-sensitivity notes are in
[references/workflows_and_best_practices.md](references/workflows_and_best_practices.md).

Prefer portable exchange formats (SMILES, SDF) for shared data; for local caches RDKit's
binary molecule representation avoids generic pickle.

## Preserve chemical meaning

Canonical isomeric SMILES records one molecular graph under a particular RDKit
version. It does not merge tautomers, choose a pH-dependent protonation state,
remove salts, or resolve unspecified stereo. Preserve the original structure and
source ID before any explicit standardization policy. Similarity 1.0 is not an
identity test: fingerprints collide and the bundled search ignores chirality
unless `--chirality` is selected. See the tested identity/standardization example
in [references/workflows_and_best_practices.md](references/workflows_and_best_practices.md).

The helpers retain one-based source IDs through invalid-record filtering, reject
empty molecules, and perform no implicit salt stripping or neutralization.
SMILES files have no header; RDKit parses a SMILES/CXSMILES structure followed
by an optional name. Text outputs retain enhanced stereo groups as CXSMILES;
other CX annotations (such as drawing coordinates) are not an archive format.
The search accepts one valid query molecule, not a query batch. MACCS always has
167 positions (166 keys plus unused bit 0); `--bits` applies to other methods.
The substructure helper's historical `pains` option is only five illustrative
motifs, not the published PAINS catalogue. Invalid requested patterns abort.
Descriptor cutoffs, QED, and alerts are research heuristics, not evidence of
safety, potency, bioavailability, or synthetic feasibility.

## Common Pitfalls

1. **Forgetting to check for None:** Always validate molecules after parsing
2. **Sanitization failures:** Use `DetectChemistryProblems()` to debug
3. **Hydrogen representation:** Most 2D descriptors account for implicit H. Use `AddHs()` before embedding; it does not choose protonation at a specified pH.
4. **2D vs 3D:** Generate appropriate coordinates before visualization or 3D analysis. Check the conformer ID returned by `EmbedMolecule` before accessing coordinates: `-1` means embedding failed. For difficult molecules, enable `EmbedParameters.trackFailures` and inspect `GetFailureCounts()`; do not send a failed embedding into force-field optimization.
5. **SMARTS matching rules:** Remember that unspecified properties match anything
6. **Thread safety with MolSuppliers:** Don't share supplier objects across threads

## Resources

### references/

This skill includes detailed API reference documentation:

- `api_reference.md` - Comprehensive listing of RDKit modules, functions, and classes organized by functionality
- `descriptors_reference.md` - Selected list of available molecular descriptors with descriptions
- `smarts_patterns.md` - Common SMARTS patterns for functional groups and structural features

Load these references when needing specific API details, parameter information, or pattern examples.

Only the files listed in `references/` and `scripts/` are bundled local resources. Names such as `rdkit`, `datamol`, `scipy`, and `sklearn` refer to installable Python packages, not local files in this skill.

### scripts/

Example scripts for common RDKit workflows:

- `molecular_properties.py` - Calculate comprehensive molecular properties and descriptors
- `similarity_search.py` - Perform fingerprint-based similarity screening
- `substructure_filter.py` - Filter molecules by substructure patterns

These scripts can be executed directly or used as templates for custom workflows.

## Review evidence

Release, official API/source links, runtime coverage, and explicitly illustrative
examples are recorded in [references/review.md](references/review.md).
