from __future__ import annotations

import httpx


async def test_workspace_starts_with_one_tab(client: httpx.AsyncClient) -> None:
    r = await client.get("/api/workspace")
    assert r.status_code == 200
    body = r.json()
    assert body["conversation_id"].startswith("conv_")
    assert [t["name"] for t in body["tabs"]] == ["Untitled 1"]

    again = (await client.get("/api/workspace")).json()
    assert again["id"] == body["id"]
    assert len(again["tabs"]) == 1


async def test_create_update_with_revision(client: httpx.AsyncClient) -> None:
    await client.get("/api/workspace")
    tab = (await client.post("/api/tabs", json={"name": "Aspirin"})).json()
    assert tab["revision"] == 0 and tab["position"] == 2

    r = await client.patch(
        f"/api/tabs/{tab['id']}",
        json={"revision": 0, "content": "{}", "content_format": "ket", "smiles": ["CCO"]},
    )
    assert r.status_code == 200
    updated = r.json()
    assert updated["revision"] == 1
    assert updated["smiles_cache"] == ["CCO"]

    stale = await client.patch(f"/api/tabs/{tab['id']}", json={"revision": 0, "name": "x"})
    assert stale.status_code == 409
    assert stale.json()["detail"]["current_revision"] == 1


async def test_soft_delete_restore_duplicate(client: httpx.AsyncClient) -> None:
    ws = (await client.get("/api/workspace")).json()
    first = ws["tabs"][0]
    second = (await client.post("/api/tabs", json={})).json()
    assert second["name"] == "Untitled 2"

    assert (await client.delete(f"/api/tabs/{first['id']}")).status_code == 204
    names = [t["name"] for t in (await client.get("/api/workspace")).json()["tabs"]]
    assert names == ["Untitled 2"]

    closed = (await client.get("/api/tabs/closed")).json()
    assert [t["id"] for t in closed] == [first["id"]]

    restored = (await client.post(f"/api/tabs/{first['id']}/restore")).json()
    assert restored["is_deleted"] is False
    assert restored["position"] > second["position"]

    dup = (await client.post(f"/api/tabs/{second['id']}/duplicate")).json()
    assert dup["name"] == "Untitled 2 (copy)"
    assert dup["id"] != second["id"]


async def test_unknown_tab_is_404(client: httpx.AsyncClient) -> None:
    assert (await client.get("/api/tabs/tab_missing")).status_code == 404
