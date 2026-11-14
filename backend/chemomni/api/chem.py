from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from chemomni.chem.compounds import number_compounds
from chemomni.chem.props import canonical_smiles, compute_light, compute_properties, parse_smiles

router = APIRouter(prefix="/api/chem", tags=["chem"])


class SmilesBatch(BaseModel):
    smiles: list[str] = Field(..., max_length=200)
    light: bool = False


@router.post("/properties")
async def properties(body: SmilesBatch) -> dict[str, Any]:
    fn = compute_light if body.light else compute_properties
    rows = await run_in_threadpool(lambda: [fn(s) for s in body.smiles])
    return {"rows": rows}


@router.post("/validate")
async def validate(body: SmilesBatch) -> dict[str, Any]:
    def _one(s: str) -> dict[str, Any]:
        mol, error = parse_smiles(s)
        if mol is None:
            return {"input": s, "ok": False, "canonical_smiles": None, "errors": [error]}
        return {"input": s, "ok": True, "canonical_smiles": canonical_smiles(mol), "errors": []}

    return {"rows": await run_in_threadpool(lambda: [_one(s) for s in body.smiles])}


class NumberRequest(BaseModel):
    ket: str = Field(..., max_length=5_000_000, description="The editor document (KET)")


@router.post("/number-compounds")
async def number_compounds_endpoint(body: NumberRequest) -> dict[str, Any]:
    """Write a number under every structure that has none (the editor's "Number compounds" button)."""
    ket, added = await run_in_threadpool(number_compounds, body.ket)
    return {"ket": ket, "added": added}
