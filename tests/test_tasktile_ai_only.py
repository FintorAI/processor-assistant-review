"""Tests for the processor-side rns_ai_only client (shared/tasktile_ai_only.py).

All network + Encompass calls are mocked; these exercise gating + orchestration.
"""
import sys
import types

import pytest

from shared import tasktile_ai_only as ai


@pytest.fixture
def fake_encompass(monkeypatch):
    """Inject a fake ``encompass_client`` module + stub the attachment download."""
    mod = types.ModuleType("encompass_client")
    mod.get_encompass_client = lambda state=None: object()
    monkeypatch.setitem(sys.modules, "encompass_client", mod)
    monkeypatch.setattr(
        "shared.ess_contact_bypass._download_attachment",
        lambda client, loan_id, aid: b"%PDF-1.4 fake",
    )


ATTS = [{"attachment_id": "att-1", "filename": "Driver's License"}]


def test_disabled_returns_none(monkeypatch):
    monkeypatch.delenv("TASKTILE_AI_ONLY_ENABLED", raising=False)
    assert ai.run_ai_only("guid", ATTS) is None  # gated off by default


def test_no_loan_id_returns_none():
    assert ai.run_ai_only("", ATTS, force=True) is None


def test_no_attachments_returns_none():
    assert ai.run_ai_only("guid", [], force=True) is None


def test_no_creds_returns_none(monkeypatch, fake_encompass):
    for k in ("TASKTILE_PROD_CLIENT_KEY", "TASKTILE_PROD_CLIENT_SECRET",
              "TASKTILE_CLIENT_ID", "TASKTILE_CLIENT_SECRET"):
        monkeypatch.delenv(k, raising=False)
    # force past the enable gate; _token() should bail on missing creds
    assert ai.run_ai_only("guid", ATTS, force=True) is None


def test_success_path(monkeypatch, fake_encompass):
    monkeypatch.setattr(ai, "_token", lambda: "tok")
    up_calls = []
    monkeypatch.setattr(ai, "_upload",
                        lambda tok, fname, pdf, aid: up_calls.append(aid) or f"up-{aid}")
    job_calls = {}

    def fake_create_job(tok, ups, entity, **kw):
        job_calls["ups"] = ups
        job_calls["entity"] = entity
        return "job-9"

    monkeypatch.setattr(ai, "_create_job", fake_create_job)
    monkeypatch.setattr(ai, "_poll", lambda tok, jid, minutes=30: {"status": "success"})
    monkeypatch.setattr(ai, "_manifest",
                        lambda tok, jid: {"documents": [{"root_attachment_id": "att-1"}]})

    man = ai.run_ai_only("guid", ATTS, force=True, loan_number="123")
    assert man is not None
    assert man["_processor"]["job_id"] == "job-9"
    assert up_calls == ["att-1"]
    assert job_calls["ups"] == ["up-att-1"]


def test_download_failure_skips_attachment(monkeypatch, fake_encompass):
    # download returns None for the only attachment -> no uploads -> None
    monkeypatch.setattr("shared.ess_contact_bypass._download_attachment",
                        lambda client, loan_id, aid: None)
    monkeypatch.setattr(ai, "_token", lambda: "tok")
    assert ai.run_ai_only("guid", ATTS, force=True) is None


def test_creds_prefers_prod(monkeypatch):
    monkeypatch.setenv("TASKTILE_PROD_CLIENT_KEY", "prod-key")
    monkeypatch.setenv("TASKTILE_PROD_CLIENT_SECRET", "prod-sec")
    monkeypatch.setenv("TASKTILE_CLIENT_ID", "generic-id")
    monkeypatch.setenv("TASKTILE_CLIENT_SECRET", "generic-sec")
    assert ai._creds() == ("prod-key", "prod-sec")


def test_creds_none_when_unset(monkeypatch):
    for k in ("TASKTILE_PROD_CLIENT_KEY", "TASKTILE_PROD_CLIENT_SECRET",
              "TASKTILE_CLIENT_ID", "TASKTILE_CLIENT_SECRET"):
        monkeypatch.delenv(k, raising=False)
    assert ai._creds() is None
