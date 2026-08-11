"""Tests that the dashboard's stateless write graphs (write_los_fields,
write_los_collections — registered in langgraph.json) hold one Encompass
lock across their whole write span and surface LoanLockedError as a normal
`results.error` instead of crashing the graph node. See
processor-assistant-orchestrator/docs/encompass_resource_locking_plan.md.
"""
import os
import sys
from contextlib import contextmanager

import pytest

_OUTPUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "output")
if _OUTPUT_DIR not in sys.path:
    sys.path.insert(0, _OUTPUT_DIR)

import write_graphs  # noqa: E402
import encompass_client  # noqa: E402

LOAN_GUID = "12345678-aaaa-bbbb-cccc-1234567890ab"


@contextmanager
def _fake_loan_lock_ok(loan_id, state=None):
    yield "lock-guid-0001"


@contextmanager
def _fake_loan_lock_locked(loan_id, state=None):
    raise encompass_client.LoanLockedError(
        f"Loan {loan_id[:8]} is already locked by Test Admin.",
        holder_full_name="Test Admin",
    )
    yield  # pragma: no cover — unreachable, keeps this a generator fn


# ═══════════════════════════════════════════════════════════════════════
# write_los_fields
# ═══════════════════════════════════════════════════════════════════════

def test_write_fields_node_wraps_write_in_loan_lock(monkeypatch):
    calls = []
    monkeypatch.setattr(encompass_client, "loan_lock", _fake_loan_lock_ok)

    def fake_write_fields_resilient(loan_id, updates, state=None):
        calls.append("wrote")
        return {"4002": "123"}, {}

    monkeypatch.setattr("shared.encompass_io.write_fields_resilient", fake_write_fields_resilient)

    result = write_graphs._write_fields_node({
        "loan_number": LOAN_GUID,
        "env": "Test",
        "updates": {"4002": "123"},
    })

    assert calls == ["wrote"]
    assert result["results"]["written"] == {"4002": "123"}
    assert result["loan_id"] == LOAN_GUID


def test_write_fields_node_surfaces_loan_locked_error(monkeypatch):
    monkeypatch.setattr(encompass_client, "loan_lock", _fake_loan_lock_locked)
    write_calls = []
    monkeypatch.setattr(
        "shared.encompass_io.write_fields_resilient",
        lambda *a, **k: write_calls.append(1),
    )

    result = write_graphs._write_fields_node({
        "loan_number": LOAN_GUID,
        "env": "Test",
        "updates": {"4002": "123"},
    })

    assert write_calls == []  # the write itself never ran
    assert "already locked by Test Admin" in result["results"]["error"]
    assert result["results"]["written"] == {}


# ═══════════════════════════════════════════════════════════════════════
# write_los_collections
# ═══════════════════════════════════════════════════════════════════════

def test_write_collections_node_wraps_whole_span_in_one_lock(monkeypatch):
    monkeypatch.setattr(encompass_client, "loan_lock", _fake_loan_lock_ok)
    monkeypatch.setattr(encompass_client, "get_encompass_client", lambda **k: object())
    monkeypatch.setattr(
        encompass_client, "get_loan_applications",
        lambda loan_id, state=None: [{"id": "app-1"}],
    )

    apply_calls = []

    def fake_apply_collection_writes(client, io_state, loan_id, application_id, vods, vols, voes, contacts):
        apply_calls.append((loan_id, application_id, bool(contacts)))
        return {"contacts": {"success": True, "written": True}}

    monkeypatch.setattr(write_graphs, "_apply_collection_writes", fake_apply_collection_writes)

    result = write_graphs._write_collections_node({
        "loan_number": LOAN_GUID,
        "env": "Test",
        "contacts": [{"contactType": "Borrower"}],
    })

    assert apply_calls == [(LOAN_GUID, None, True)]
    assert result["results"] == {"contacts": {"success": True, "written": True}}
    assert result["loan_id"] == LOAN_GUID


def test_write_collections_node_surfaces_loan_locked_error_without_writing(monkeypatch):
    monkeypatch.setattr(encompass_client, "loan_lock", _fake_loan_lock_locked)
    monkeypatch.setattr(encompass_client, "get_encompass_client", lambda **k: object())

    apply_calls = []
    monkeypatch.setattr(
        write_graphs, "_apply_collection_writes",
        lambda *a, **k: apply_calls.append(1),
    )

    result = write_graphs._write_collections_node({
        "loan_number": LOAN_GUID,
        "env": "Test",
        "contacts": [{"contactType": "Borrower"}],
    })

    assert apply_calls == []
    assert "already locked by Test Admin" in result["results"]["error"]
