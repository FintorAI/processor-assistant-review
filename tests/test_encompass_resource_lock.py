"""Tests for the Encompass resource-lock primitives in encompass_client.py.

See processor-assistant-orchestrator/docs/encompass_resource_locking_plan.md
for the design this implements: fail-fast on a 409 (never steal a foreign
lock), always release in a `finally`.
"""
from unittest.mock import MagicMock

import pytest
import requests

import encompass_client
from encompass_client import LoanLockedError, lock_resource, loan_lock, unlock_resource

LOAN_ID = "12345678-aaaa-bbbb-cccc-1234567890ab"
LOCK_ID = "lock-guid-0001"


class _FakeResponse:
    """Minimal stand-in for requests.Response."""

    def __init__(self, status_code, json_body=None, text="", headers=None):
        self.status_code = status_code
        self._json_body = json_body
        self.text = text
        self.headers = headers or {}

    def json(self):
        if self._json_body is None:
            raise ValueError("no JSON body")
        return self._json_body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


@pytest.fixture
def fake_client(monkeypatch):
    """Patch get_encompass_client() to return a stub EncompassConnect client."""
    client = MagicMock()
    client.api_base_url = "https://api.elliemae.com"
    client.access_token = "test-access-token"
    monkeypatch.setattr(encompass_client, "get_encompass_client", lambda **kwargs: client)
    return client


# ═══════════════════════════════════════════════════════════════════════
# lock_resource
# ═══════════════════════════════════════════════════════════════════════

def test_lock_resource_happy_path_returns_lock_id(monkeypatch, fake_client):
    monkeypatch.setattr(
        "requests.post",
        lambda *a, **k: _FakeResponse(201, json_body={"id": LOCK_ID}),
    )
    lock_id = lock_resource(LOAN_ID, state={})
    assert lock_id == LOCK_ID


def test_lock_resource_happy_path_falls_back_to_location_header(monkeypatch, fake_client):
    monkeypatch.setattr(
        "requests.post",
        lambda *a, **k: _FakeResponse(
            201, json_body={}, headers={"Location": f".../resourceLocks/{LOCK_ID}"}
        ),
    )
    lock_id = lock_resource(LOAN_ID, state={})
    assert lock_id == LOCK_ID


def test_lock_resource_409_raises_loan_locked_error_with_holder_info(monkeypatch, fake_client):
    def fake_post(url, json=None, headers=None, timeout=None):
        return _FakeResponse(409, json_body={"id": "their-lock-id"})

    def fake_get(url, headers=None, params=None, timeout=None):
        return _FakeResponse(
            200,
            json_body=[{
                "id": "their-lock-id",
                "userId": "testadmin",
                "fullName": "Test Admin",
            }],
        )

    monkeypatch.setattr("requests.post", fake_post)
    monkeypatch.setattr("requests.get", fake_get)

    with pytest.raises(LoanLockedError) as excinfo:
        lock_resource(LOAN_ID, state={})

    err = excinfo.value
    assert err.holder_user_id == "testadmin"
    assert err.holder_full_name == "Test Admin"
    assert err.lock_id == "their-lock-id"
    assert "Test Admin" in str(err)


def test_lock_resource_409_without_holder_info_still_raises(monkeypatch, fake_client):
    """If the follow-up GET fails/returns nothing, still fail fast (just
    without an enriched holder name) rather than raising a different error."""
    monkeypatch.setattr("requests.post", lambda *a, **k: _FakeResponse(409, json_body={}))

    def fake_get(*a, **k):
        raise requests.exceptions.RequestException("network blip")

    monkeypatch.setattr("requests.get", fake_get)

    with pytest.raises(LoanLockedError) as excinfo:
        lock_resource(LOAN_ID, state={})
    assert excinfo.value.holder_full_name is None


def test_lock_resource_never_deletes_on_409(monkeypatch, fake_client):
    """Fail-fast means never stealing — no DELETE call should ever be made
    from lock_resource itself."""
    delete_calls = []
    monkeypatch.setattr("requests.post", lambda *a, **k: _FakeResponse(409, json_body={}))
    monkeypatch.setattr("requests.get", lambda *a, **k: _FakeResponse(200, json_body=[]))
    monkeypatch.setattr("requests.delete", lambda *a, **k: delete_calls.append(1))

    with pytest.raises(LoanLockedError):
        lock_resource(LOAN_ID, state={})
    assert delete_calls == []


def test_lock_resource_refreshes_token_on_401(monkeypatch, fake_client):
    calls = {"count": 0}

    def fake_post(url, json=None, headers=None, timeout=None):
        calls["count"] += 1
        if calls["count"] == 1:
            return _FakeResponse(401)
        assert headers["Authorization"] == "Bearer refreshed-token"
        return _FakeResponse(201, json_body={"id": LOCK_ID})

    def fake_refresh():
        fake_client.access_token = "refreshed-token"

    fake_client.refresh_token = fake_refresh
    monkeypatch.setattr("requests.post", fake_post)

    lock_id = lock_resource(LOAN_ID, state={})
    assert lock_id == LOCK_ID
    assert calls["count"] == 2


# ═══════════════════════════════════════════════════════════════════════
# unlock_resource — best-effort, must never raise
# ═══════════════════════════════════════════════════════════════════════

def test_unlock_resource_success(monkeypatch, fake_client):
    monkeypatch.setattr("requests.delete", lambda *a, **k: _FakeResponse(204))
    unlock_resource(LOCK_ID, LOAN_ID, state={})  # should not raise


def test_unlock_resource_404_is_treated_as_ok(monkeypatch, fake_client):
    monkeypatch.setattr("requests.delete", lambda *a, **k: _FakeResponse(404))
    unlock_resource(LOCK_ID, LOAN_ID, state={})  # should not raise


def test_unlock_resource_swallows_network_error(monkeypatch, fake_client):
    def fake_delete(*a, **k):
        raise requests.exceptions.RequestException("boom")

    monkeypatch.setattr("requests.delete", fake_delete)
    unlock_resource(LOCK_ID, LOAN_ID, state={})  # must not raise


def test_unlock_resource_swallows_unexpected_status(monkeypatch, fake_client):
    monkeypatch.setattr("requests.delete", lambda *a, **k: _FakeResponse(500, text="server error"))
    unlock_resource(LOCK_ID, LOAN_ID, state={})  # must not raise, just logs a warning


# ═══════════════════════════════════════════════════════════════════════
# loan_lock — context manager
# ═══════════════════════════════════════════════════════════════════════

def test_loan_lock_acquires_yields_and_releases(monkeypatch):
    calls = []
    monkeypatch.setattr(encompass_client, "lock_resource", lambda loan_id, state=None, lock_type="Exclusive": (calls.append("lock"), LOCK_ID)[1])
    monkeypatch.setattr(encompass_client, "unlock_resource", lambda lock_id, loan_id, state=None: calls.append("unlock"))

    with loan_lock(LOAN_ID, state={}) as lock_id:
        assert lock_id == LOCK_ID
        calls.append("body")

    assert calls == ["lock", "body", "unlock"]


def test_loan_lock_releases_even_when_body_raises(monkeypatch):
    calls = []
    monkeypatch.setattr(encompass_client, "lock_resource", lambda loan_id, state=None, lock_type="Exclusive": (calls.append("lock"), LOCK_ID)[1])
    monkeypatch.setattr(encompass_client, "unlock_resource", lambda lock_id, loan_id, state=None: calls.append("unlock"))

    with pytest.raises(RuntimeError):
        with loan_lock(LOAN_ID, state={}):
            calls.append("body")
            raise RuntimeError("write failed mid-span")

    assert calls == ["lock", "body", "unlock"]


def test_loan_lock_never_calls_unlock_when_lock_fails(monkeypatch):
    """If lock_resource itself raises (e.g. LoanLockedError on 409), there is
    no lock_id to release — unlock_resource must not be called at all (that
    would DELETE a foreign lock, which is exactly what fail-fast forbids)."""
    calls = []

    def fake_lock_resource(loan_id, state=None, lock_type="Exclusive"):
        raise LoanLockedError("Loan is already locked by Test Admin.", holder_full_name="Test Admin")

    monkeypatch.setattr(encompass_client, "lock_resource", fake_lock_resource)
    monkeypatch.setattr(encompass_client, "unlock_resource", lambda *a, **k: calls.append("unlock"))

    with pytest.raises(LoanLockedError):
        with loan_lock(LOAN_ID, state={}):
            calls.append("body")  # should never run

    assert calls == []
