"""Tests for the TaskTile bucket config + gap-driven fallback engine.

Covers:
  - bucket resolution (field overrides, prefix matches, category default, unknown default)
  - cross_doc_source resolution
  - resolve_field decision flow for buckets 1/2/3
  - (optional) sanity against a real local manifest, skipped if absent
"""
import json
import os

import pytest

from shared import tasktile_buckets as tb
from shared import tasktile_fallback as tf
from shared import tasktile_validation as tv


# ── bucket resolution ──────────────────────────────────────────────────────
def test_tested_categories_are_bucket_one():
    # 8 empirically-tested categories default to TRUST
    for cid in (2168, 323, 117, 167, 200, 141, 538, 2044):
        assert tb.category_config(cid).get("bucket") == 1
        assert tb.is_tested(cid) is True


def test_untested_category_defaults_to_bucket_three():
    # still-untested categories (349/819/984 were bootstrapped -> bucket 1)
    for cid in (843, 1481, 1):
        assert tb.field_bucket(cid, "anything.at.all") == 3
        assert tb.is_tested(cid) is False


def test_unknown_category_uses_global_default():
    assert tb.field_bucket(999999, "whatever") == tb.default_bucket()


def test_structural_gaps_are_bucket_two():
    assert tb.field_bucket(323, "owner.idNumber") == 2
    assert tb.field_bucket(2168, "escrowCompany") == 2
    assert tb.field_bucket(2168, "titleCompany") == 2
    # passport/PRC untested overall but idNumber pinned structural
    assert tb.field_bucket(845, "owner.idNumber") == 2
    assert tb.field_bucket(844, "resident.idNumber") == 2


def test_prefix_override_covers_children():
    # override on `settlementAgent` (parent) applies to nested child
    assert tb.field_bucket(2168, "settlementAgent") == 2
    assert tb.field_bucket(2168, "settlementAgent.name") == 2


def test_non_overridden_field_takes_category_default():
    # 2168 default bucket is 1; a field with no override trusts the manifest
    assert tb.field_bucket(2168, "buyers[].firstName") == 1


def test_cross_doc_source_points_to_cpl():
    assert tb.cross_doc_source(2168, "fileNumber") == 167
    assert tb.cross_doc_source(2168, "escrowCompany") == 167
    assert tb.cross_doc_source(2168, "settlementAgent.name") == 167  # prefix
    assert tb.cross_doc_source(323, "owner.idNumber") is None


# ── decision flow ──────────────────────────────────────────────────────────
def test_bucket1_trusts_valid_manifest_value():
    res = tf.resolve_field(2168, "buyers[].firstName", manifest_value="Jerardo",
                           landingai_lookup=lambda c, f: "NOPE")
    assert res.source == "manifest" and res.value == "Jerardo" and res.valid


def test_bucket1_falls_back_when_manifest_invalid():
    res = tf.resolve_field(
        2168, "buyers[].firstName",
        manifest_value="",  # empty -> invalid
        landingai_lookup=lambda c, f: "FromLandingAI",
    )
    assert res.source == "landingai" and res.value == "FromLandingAI"
    assert res.tried == ["manifest", "landingai"]


def test_bucket2_skips_manifest_and_uses_cross_doc():
    # escrowCompany on ALTA is bucket 2 with cross_doc_source -> CPL(167)
    calls = {}

    def cross(sibling, fpath):
        calls["sibling"] = sibling
        return "Fidelity National Title Company"

    res = tf.resolve_field(
        2168, "escrowCompany",
        manifest_value="SHOULD_BE_IGNORED",
        cross_doc_lookup=cross,
        landingai_lookup=lambda c, f: "landingai_should_not_run",
    )
    assert res.source == "cross_doc"
    assert res.value == "Fidelity National Title Company"
    assert calls["sibling"] == 167
    assert "manifest" not in res.tried  # bucket 2 never reads the manifest


def test_bucket2_falls_to_landingai_when_no_cross_doc_value():
    res = tf.resolve_field(
        323, "owner.idNumber",
        manifest_value=None,
        landingai_lookup=lambda c, f: "D1234567",
        validator="id_number",
    )
    assert res.source == "landingai" and res.value == "D1234567" and res.valid


def test_bucket3_checks_manifest_then_falls_back():
    hit = tf.resolve_field(349, "anyField", manifest_value="present-value")
    assert hit.source == "manifest" and hit.valid

    miss = tf.resolve_field(349, "anyField", manifest_value=None,
                            landingai_lookup=lambda c, f: "fallback")
    assert miss.source == "landingai"


def test_invalid_zip_triggers_fallback_in_bucket3():
    res = tf.resolve_field(
        349, "propertyAddress.zip",
        manifest_value="90005",           # wrong-but-present
        validator="zip",
        landingai_lookup=lambda c, f: "90605",
    )
    # 90005 IS a valid 5-digit zip format, so validator passes -> trusts manifest.
    # This documents that format-validation only catches malformed values, not
    # semantically-wrong-but-well-formed ones (those need cross-source compare).
    assert res.source == "manifest" and res.value == "90005"

    res2 = tf.resolve_field(
        349, "propertyAddress.zip",
        manifest_value="9O6O5",           # letters -> malformed
        validator="zip",
        landingai_lookup=lambda c, f: "90605",
    )
    assert res2.source == "landingai" and res2.value == "90605"


def test_shadow_logger_receives_resolution():
    logged = []
    tf.resolve_field(2168, "buyers[].firstName", manifest_value="X",
                     shadow_logger=logged.append)
    assert logged and logged[0]["field"] == "buyers[].firstName"
    assert logged[0]["source"] == "manifest" and logged[0]["bucket"] == 1


# ── validators ─────────────────────────────────────────────────────────────
def test_validators():
    assert tv.is_zip("90605") and tv.is_zip("90605-1234")
    assert not tv.is_zip("9O6O5")
    assert tv.is_ssn("622283234") and tv.is_ssn("622-28-3234")
    assert tv.is_id_number("D1234567") and not tv.is_id_number("!!")
    assert tv.is_date("07/15/2026") and tv.is_date("2026-07-15")
    assert not tv.is_present("") and not tv.is_present([]) and tv.is_present("x")


# ── feature flags ──────────────────────────────────────────────────────────
def test_flags_default(monkeypatch):
    monkeypatch.delenv("TASKTILE_AI_ONLY_ENABLED", raising=False)
    monkeypatch.delenv("TASKTILE_SHADOW_MODE", raising=False)
    assert tf.ai_only_enabled() is False   # off by default
    assert tf.shadow_mode() is True        # shadow on by default

    monkeypatch.setenv("TASKTILE_AI_ONLY_ENABLED", "true")
    monkeypatch.setenv("TASKTILE_SHADOW_MODE", "0")
    assert tf.ai_only_enabled() is True
    assert tf.shadow_mode() is False


# ── real manifest sanity (skipped if the local file is absent, e.g. in CI) ──
_MANIFEST = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "local", "rns_results", "2608976334_rns_ai_only_6e2a2c0d.json",
)


@pytest.mark.skipif(not os.path.exists(_MANIFEST), reason="local manifest not present")
def test_real_manifest_dl_has_no_idnumber():
    man = json.load(open(_MANIFEST))
    dl = tf.doc_by_category(man, 323)
    assert dl is not None
    flat = tf.flatten(dl.get("metadata") or {})
    assert not any("idNumber" in k for k in flat)     # Issue A confirmed
    assert any(k.startswith("name.") for k in flat)   # names still present
