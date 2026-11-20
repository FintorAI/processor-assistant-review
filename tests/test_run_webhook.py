"""The run-completion webhook carries the thread's values, which hold LangChain messages."""

import json
import urllib.request

from langchain_core.messages import AIMessage, HumanMessage

from api.services import runs
from api.services import state as state_mod


class _Resp:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _fire(monkeypatch, values):
    sent = []
    monkeypatch.setattr(
        state_mod, "get_thread_state", lambda thread_id, assistant_id=None: {"values": values}
    )

    def fake_urlopen(req, timeout=None):
        sent.append(json.loads(req.data))
        return _Resp()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    runs._fire_run_webhook(
        webhook_url="https://example.test/hook?token=t",
        thread_id="t-1",
        run_id="r-1",
        assistant_id="review",
        status="success",
    )
    return sent


def test_webhook_serializes_message_objects(monkeypatch):
    values = {
        "current_step": "COMPLETED",
        "messages": [HumanMessage(content="Review loan 1234567890"), AIMessage(content="Done")],
    }
    sent = _fire(monkeypatch, values)

    assert len(sent) == 1
    body = sent[0]
    assert body["run_id"] == "r-1" and body["status"] == "success"
    assert body["values"]["current_step"] == "COMPLETED"
    assert [m["type"] for m in body["values"]["messages"]] == ["human", "ai"]
    assert body["values"]["messages"][1]["content"] == "Done"


def test_webhook_failure_does_not_raise(monkeypatch):
    class Unserializable:
        pass

    def boom(*a, **k):
        raise RuntimeError("network down")

    monkeypatch.setattr(
        state_mod, "get_thread_state", lambda thread_id, assistant_id=None: {"values": {"x": Unserializable()}}
    )
    monkeypatch.setattr(urllib.request, "urlopen", boom)
    runs._fire_run_webhook(
        webhook_url="https://example.test/hook",
        thread_id="t-1",
        run_id="r-1",
        assistant_id="review",
        status="error",
        error="x",
    )
