"""Name -> structure (PLAN §7): OPSIN when installed (offline), then PubChem PUG REST.

OPSIN needs Java and the optional ``py2opsin`` package (``uv sync --extra opsin``). PubChem is
an online lookup; set ``CHEMOMNI_PUBCHEM=false`` to keep compound names from leaving the machine.
"""

from __future__ import annotations

import asyncio
import shutil
import time
import warnings
from collections import OrderedDict
from typing import Any
from urllib.parse import quote

import httpx
from starlette.concurrency import run_in_threadpool

from chemomni.chem.props import canonical_smiles, parse_smiles

PUBCHEM_URL = "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/{name}/property/SMILES,IUPACName/JSON"
PUBCHEM_MIN_INTERVAL_S = 0.2  # PubChem asks for at most 5 requests per second
PUBCHEM_TIMEOUT_S = 10.0
CACHE_SIZE = 256


class NameResolver:
    def __init__(self, *, pubchem: bool = True, client: httpx.AsyncClient | None = None) -> None:
        self.pubchem = pubchem
        self._client = client
        self._lock = asyncio.Lock()
        self._last_request = 0.0
        self._cache: OrderedDict[str, dict[str, Any]] = OrderedDict()

    # ------------------------------------------------------------ public

    async def resolve(self, name: str) -> dict[str, Any]:
        """``{"ok": True, "smiles", "source", ...}`` or ``{"ok": False, "errors": [...]}``."""
        key = name.strip().lower()
        if not key:
            return {"ok": False, "errors": ["empty name"]}
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        result = await self._resolve(name.strip())
        if result["ok"]:  # failures may be transient (network); do not cache them
            self._cache[key] = result
            if len(self._cache) > CACHE_SIZE:
                self._cache.popitem(last=False)
        return result

    # ------------------------------------------------------------ internals

    async def _resolve(self, name: str) -> dict[str, Any]:
        errors: list[str] = []
        if opsin_available():
            smiles = await run_in_threadpool(_opsin, name)
            if smiles:
                return _result(name, smiles, "opsin")
            errors.append("OPSIN could not parse the name")
        else:
            errors.append("OPSIN is not installed")
        if not self.pubchem:
            errors.append("PubChem lookup is disabled (CHEMOMNI_PUBCHEM=false)")
            return _not_found(name, errors)
        try:
            found = await self._pubchem(name)
        except httpx.HTTPError as exc:
            errors.append(f"PubChem request failed: {type(exc).__name__}")
            return _not_found(name, errors)
        if found is None:
            errors.append("PubChem has no compound with this name")
            return _not_found(name, errors)
        return _result(name, found["smiles"], "pubchem", cid=found.get("cid"), iupac_name=found.get("iupac_name"))

    async def _pubchem(self, name: str) -> dict[str, Any] | None:
        async with self._lock:  # one request at a time, spaced out
            wait = self._last_request + PUBCHEM_MIN_INTERVAL_S - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            client = self._client or httpx.AsyncClient(timeout=PUBCHEM_TIMEOUT_S)
            try:
                response = await client.get(PUBCHEM_URL.format(name=quote(name, safe="")))
            finally:
                self._last_request = time.monotonic()
                if self._client is None:
                    await client.aclose()
        if response.status_code == 404:
            return None
        response.raise_for_status()
        props = (response.json().get("PropertyTable") or {}).get("Properties") or []
        if not props:
            return None
        first = props[0]
        # "SMILES" is isomeric since PubChem's 2025 rename; older responses used "IsomericSMILES"
        smiles = first.get("SMILES") or first.get("IsomericSMILES") or first.get("CanonicalSMILES")
        if not smiles:
            return None
        return {"smiles": smiles, "cid": first.get("CID"), "iupac_name": first.get("IUPACName")}


def opsin_available() -> bool:
    try:
        import py2opsin  # noqa: F401
    except ImportError:
        return False
    return shutil.which("java") is not None


def _opsin(name: str) -> str | None:
    from py2opsin import py2opsin

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # py2opsin warns (with Java's stderr) on every failure
        try:
            smiles = py2opsin(name)
        except Exception:
            return None
    return smiles or None


def _not_found(name: str, details: list[str]) -> dict[str, Any]:
    return {"ok": False, "name": name, "errors": [f"no structure found for '{name}'", *details]}


def _result(name: str, smiles: str, source: str, **extra: Any) -> dict[str, Any]:
    mol, err = parse_smiles(smiles)
    if mol is None:
        return {"ok": False, "name": name, "errors": [f"{source} returned an invalid structure: {err}"]}
    return {"ok": True, "name": name, "smiles": canonical_smiles(mol), "source": source, **extra}
