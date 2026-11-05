"""POST /threads/import writes a Cloud thread's head state faithfully (synthetic state, in-memory checkpointer)."""

import pytest

from api import checkpointer as checkpointer_mod
from api import registry


@pytest.fixture
def import_state(monkeypatch):
    monkeypatch.delenv("CHECKPOINT_TABLE_NAME", raising=False)
    monkeypatch.setenv("CHECKPOINT_IN_MEMORY", "1")
    checkpointer_mod.get_checkpointer.cache_clear()
    registry._checkpointed_review.cache_clear()
    from api.services.state import import_thread_state

    yield import_thread_state
    checkpointer_mod.get_checkpointer.cache_clear()
    registry._checkpointed_review.cache_clear()


def _cloud_values(*, ai_tool_calls=None):
    return {
        "messages": [
            {"type": "human", "id": "m1", "content": "Review loan 1234567890", "additional_kwargs": {},
             "response_metadata": {}, "name": None, "example": False},
            {"type": "ai", "id": "m2", "content": "Done." if not ai_tool_calls else "",
             "tool_calls": ai_tool_calls or [], "invalid_tool_calls": [], "additional_kwargs": {},
             "response_metadata": {}, "name": None, "example": False,
             "usage_metadata": {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12}},
        ],
        "field_writes_ledger": [{"field": "4000", "value": "1", "status": "written"}],
    }


def test_finished_thread_has_no_next_and_keeps_usage_metadata(import_state):
    snap = import_state("t-finished", _cloud_values(), assistant_id="review", next_nodes=[])
    assert list(snap["next"]) == []
    ai = snap["values"]["messages"][1]
    assert ai.usage_metadata == {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12}
    assert "usage_metadata" not in ai.additional_kwargs


def test_interrupted_thread_reproduces_cloud_next(import_state):
    calls = [{"name": "noop", "args": {}, "id": "call-1", "type": "tool_call"}]
    snap = import_state("t-tools", _cloud_values(ai_tool_calls=calls), assistant_id="review", next_nodes=["tools"])
    assert list(snap["next"]) == ["tools"]


def test_reimport_with_replace_does_not_double_append_only_fields(import_state):
    import_state("t-again", _cloud_values(), assistant_id="review", next_nodes=[])
    snap = import_state("t-again", _cloud_values(), assistant_id="review", next_nodes=[], replace=True)
    assert len(snap["values"]["field_writes_ledger"]) == 1
    assert len(snap["values"]["messages"]) == 2


def test_reducers_do_not_rewrite_imported_values(import_state):
    flag = {"substep": "1.1", "title": "Missing doc", "resolved": False, "timestamp": "2026-08-01T00:00:00+00:00"}
    snap = import_state("t-flags", {**_cloud_values(), "flags": [dict(flag)]}, assistant_id="review", next_nodes=[])
    assert snap["values"]["flags"] == [flag]


def test_unreachable_next_is_an_error(import_state):
    with pytest.raises(ValueError, match="no node reproduces"):
        import_state("t-bad", _cloud_values(), assistant_id="review", next_nodes=["not-a-node"])
