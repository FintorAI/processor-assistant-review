"""DELETE /threads/{id} removes the thread record, its runs and its checkpoints."""

import pytest
from fastapi.testclient import TestClient

from api import checkpointer as checkpointer_mod
from api import registry
from api import thread_store as thread_store_mod


@pytest.fixture
def client(monkeypatch):
    from api.main import app

    monkeypatch.delenv("CHECKPOINT_TABLE_NAME", raising=False)
    monkeypatch.setenv("API_KEY", "test-key")
    monkeypatch.setenv("CHECKPOINT_IN_MEMORY", "1")
    monkeypatch.setattr(thread_store_mod, "_thread_store", None)
    checkpointer_mod.get_checkpointer.cache_clear()
    registry._checkpointed_review.cache_clear()

    yield TestClient(app, headers={"x-api-key": "test-key"})
    checkpointer_mod.get_checkpointer.cache_clear()
    registry._checkpointed_review.cache_clear()


def _import(client, thread_id):
    body = {
        "thread_id": thread_id,
        "metadata": {"graph_id": "review"},
        "status": "idle",
        "next": [],
        "values": {"messages": [{"type": "human", "id": "m1", "content": "Review loan 1234567890"}]},
    }
    resp = client.post("/threads/import", json=body)
    assert resp.json()["state_written"] is True


def test_delete_removes_record_runs_and_checkpoints(client):
    _import(client, "t-del")
    thread_store_mod.get_thread_store().create_run("t-del", assistant_id="review")

    assert client.delete("/threads/t-del").status_code == 204

    assert client.get("/threads/t-del").status_code == 404
    assert thread_store_mod.get_thread_store().list_runs("t-del") == []
    config = {"configurable": {"thread_id": "t-del", "checkpoint_ns": ""}}
    assert checkpointer_mod.get_checkpointer().get_tuple(config) is None


def test_delete_leaves_other_threads(client):
    _import(client, "t-keep")
    _import(client, "t-drop")
    client.delete("/threads/t-drop")
    assert client.get("/threads/t-keep/state").json()["values"]["messages"]


def test_delete_unknown_thread_is_404(client):
    assert client.delete("/threads/nope").status_code == 404


def test_delete_busy_thread_is_refused(client):
    _import(client, "t-busy")
    thread_store_mod.get_thread_store().set_status("t-busy", "busy")
    assert client.delete("/threads/t-busy").status_code == 409
    assert client.get("/threads/t-busy").status_code == 200
