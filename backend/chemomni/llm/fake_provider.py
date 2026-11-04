"""Deterministic provider for tests and E2E (no network).

Two modes:
- scripted: a list of turns, each a list of events, replayed in order (``FakeProvider(script)``)
- default: a tiny rule-based assistant that calls ``compute_properties`` on the active tab
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from typing import Any

from chemomni.llm.provider import Done, LLMEvent, Message, TextDelta, ToolCall, ToolSpecJSON

ScriptTurn = list[dict[str, Any]]


class FakeProvider:
    model = "fake"

    def __init__(self, script: list[ScriptTurn] | None = None) -> None:
        self._script = list(script) if script is not None else None
        self.calls: list[dict[str, Any]] = []  # what the loop sent, for assertions

    async def stream(self, messages: list[Message], tools: list[ToolSpecJSON]) -> AsyncIterator[LLMEvent]:
        self.calls.append({"messages": messages, "tools": tools})
        events = self._next_scripted() if self._script is not None else self._rule_based(messages)
        for ev in events:
            if "text" in ev:
                # stream in small pieces like a real model
                for piece in re.findall(r".{1,12}", ev["text"], flags=re.S):
                    yield TextDelta(piece)
            elif "tool" in ev:
                yield ToolCall(ev.get("id", f"call_{len(self.calls)}"), ev["tool"], json.dumps(ev["args"]))
        yield Done(finish_reason="stop", usage={"prompt_tokens": 0, "completion_tokens": 0})

    def _next_scripted(self) -> ScriptTurn:
        assert self._script is not None
        if not self._script:
            return [{"text": "(fake script exhausted)"}]
        return self._script.pop(0)

    @staticmethod
    def _rule_based(messages: list[Message]) -> ScriptTurn:
        last = messages[-1]
        if last["role"] == "tool":
            result = json.loads(last["content"])
            rows = [r for r in result.get("rows", []) if r.get("ok")]
            if not rows:
                return [{"text": "I could not compute properties for that structure."}]
            first = rows[0]
            return [
                {
                    "text": (
                        f"Computed properties for {len(rows)} molecule(s). "
                        f"The first one, **{first['formula']}**, has MW {first['mw']} g/mol "
                        f"and cLogP {first['clogp']} (Crippen)."
                    )
                }
            ]
        context = "\n".join(m["content"] for m in messages if m["role"] == "system")
        match = re.search(r"^SMILES:\n((?:- .+\n?)+)", context, flags=re.M)
        if not match:
            return [{"text": "Draw a structure on the active tab first, then ask again."}]
        smiles = [line[2:].strip() for line in match.group(1).strip().splitlines()]
        return [{"tool": "compute_properties", "args": {"smiles": smiles}}]
