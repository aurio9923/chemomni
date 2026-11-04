from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from chemomni.llm.context import Snapshot, build_messages
from chemomni.llm.fake_provider import FakeProvider
from chemomni.models import Message

ASPIRIN = "CC(=O)Oc1ccccc1C(=O)O"
SNAPSHOT = {
    "active_tab": {"id": "tab_1", "name": "Aspirin", "revision": 3, "smiles": [ASPIRIN], "molfile": None},
    "tabs": [{"id": "tab_1", "name": "Aspirin", "smiles": [ASPIRIN]}],
}


async def chat(client: httpx.AsyncClient, conv: str, text: str, snapshot: dict | None = None) -> list[tuple[str, Any]]:
    events: list[tuple[str, Any]] = []
    body = {"conversation_id": conv, "content": text, "snapshot": snapshot or SNAPSHOT}
    async with client.stream("POST", "/api/chat", json=body) as r:
        assert r.status_code == 200
        name = None
        async for line in r.aiter_lines():
            if line.startswith("event:"):
                name = line.split(":", 1)[1].strip()
            elif line.startswith("data:") and name:
                events.append((name, json.loads(line.split(":", 1)[1])))
                name = None
    return events


@pytest.fixture
async def conv(client: httpx.AsyncClient) -> str:
    return (await client.get("/api/workspace")).json()["conversation_id"]


def use_script(client: httpx.AsyncClient, script: list) -> FakeProvider:
    provider = FakeProvider(script)
    client._transport.app.state.provider = provider  # type: ignore[attr-defined]
    return provider


async def test_default_fake_computes_properties(client: httpx.AsyncClient, conv: str) -> None:
    events = await chat(client, conv, "Properties please")
    names = [n for n, _ in events]
    assert names[0] == "message.start"
    assert names[-1] == "message.done"
    assert "tool.start" in names and "tool.result" in names
    artifact = next(d for n, d in events if n == "artifact")["artifact"]
    assert artifact["type"] == "table"
    row = artifact["payload"]["rows"][0]
    assert row["formula"] == "C9H8O4"
    assert row["svg"].startswith("<?xml") or "<svg" in row["svg"]
    assert "V3000" in row["molfile"]
    text = "".join(d["delta"] for n, d in events if n == "text.delta")
    assert "C9H8O4" in text
    assert events[-1][1]["stop_reason"] == "end_turn"


async def test_history_is_persisted_and_replayable(client: httpx.AsyncClient, conv: str) -> None:
    await chat(client, conv, "Properties please")
    data = (await client.get(f"/api/conversations/{conv}/messages")).json()["messages"]
    assert [m["role"] for m in data] == ["user", "assistant"]
    kinds = [b["type"] for b in data[1]["blocks"]]
    assert kinds == ["tool_use", "tool_result", "text"]
    assert data[1]["artifacts"][0]["payload"]["call_id"] == data[1]["blocks"][0]["id"]

    # second turn: the provider must receive the first turn as valid OpenAI messages
    provider = use_script(client, [[{"text": "ok"}]])
    await chat(client, conv, "thanks")
    sent = provider.calls[0]["messages"]
    roles = [m["role"] for m in sent]
    assert roles == ["system", "user", "assistant", "tool", "assistant", "system", "user"]
    assert sent[2]["tool_calls"][0]["function"]["name"] == "compute_properties"
    assert sent[3]["tool_call_id"] == sent[2]["tool_calls"][0]["id"]
    assert "<active_tab" in sent[5]["content"]


async def test_invalid_tool_input_is_returned_to_model(client: httpx.AsyncClient, conv: str) -> None:
    provider = use_script(
        client,
        [
            [{"tool": "validate_structures", "args": {"smiles": ["C1CC"]}, "id": "c1"}],
            [{"tool": "nonexistent", "args": {}, "id": "c2"}],
            [{"text": "Fixed."}],
        ],
    )
    events = await chat(client, conv, "check")
    results = [d for n, d in events if n == "tool.result"]
    assert results[0]["ok"] is False and results[0]["summary"] == "0/1 valid"
    assert results[1]["ok"] is False and "unknown tool" in results[1]["summary"]
    tool_msg = provider.calls[1]["messages"][-1]
    assert tool_msg["role"] == "tool" and '"ok": false' in tool_msg["content"]


async def test_tool_budget_stops_the_loop(client: httpx.AsyncClient, conv: str) -> None:
    loop_forever = [[{"tool": "validate_structures", "args": {"smiles": ["C"]}, "id": f"c{i}"}] for i in range(12)]
    use_script(client, loop_forever)
    events = await chat(client, conv, "loop")
    assert sum(1 for n, _ in events if n == "tool.start") == 8
    assert events[-1][1]["stop_reason"] == "max_tools"


async def test_similarity_and_substructure_artifacts(client: httpx.AsyncClient, conv: str) -> None:
    use_script(
        client,
        [
            [
                {
                    "tool": "similarity",
                    "args": {"smiles_a": ASPIRIN, "smiles_b": "OC(=O)c1ccccc1O"},
                    "id": "s1",
                },
                {
                    "tool": "substructure_search",
                    "args": {"smiles": [ASPIRIN, "CCO"], "smarts": "C(=O)[OX2H1]"},
                    "id": "s2",
                },
            ],
            [{"text": "done"}],
        ],
    )
    events = await chat(client, conv, "compare")
    arts = [d["artifact"] for n, d in events if n == "artifact"]
    assert [a["type"] for a in arts] == ["structure_set", "structure_set"]
    assert arts[0]["payload"]["title"].startswith("Tanimoto similarity")
    assert len(arts[1]["payload"]["items"]) == 1  # only aspirin has a carboxylic acid


async def test_missing_api_key_reports_error(client: httpx.AsyncClient, conv: str) -> None:
    from chemomni.llm.openai_provider import OpenAIChatProvider

    client._transport.app.state.provider = OpenAIChatProvider(api_key=None, base_url=None, model="m")  # type: ignore[attr-defined]
    events = await chat(client, conv, "hi")
    err = next(d for n, d in events if n == "error")
    assert "OPENAI_API_KEY" in err["message"]
    assert events[-1][1]["stop_reason"] == "error"


async def test_unknown_conversation_404(client: httpx.AsyncClient) -> None:
    r = await client.post("/api/chat", json={"conversation_id": "conv_x", "content": "hi"})
    assert r.status_code == 404


def test_context_trims_old_turns() -> None:
    def msg(role: str, text: str) -> Message:
        return Message(conversation_id="c", role=role, content=[{"type": "text", "text": text}])

    history = [msg("user", "x" * 4000), msg("assistant", "y" * 4000), msg("user", "latest")]
    out = build_messages(history, Snapshot(), budget=1500)
    assert [m["role"] for m in out] == ["system", "system", "user"]
    out = build_messages(history, Snapshot(), budget=10_000)
    assert [m["role"] for m in out] == ["system", "user", "assistant", "system", "user"]
