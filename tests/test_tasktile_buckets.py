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

# Stable fixture for engine-behavior tests (decoupled from the live config,
# whose bucket assignments shift as TaskTile's ai_only extraction changes).
FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "fixtures", "tasktile_buckets_engine.json")


# ── bucket resolution (engine, against the stable fixture) ──────────────────
def test_fixture_category_default_bucket_one():
    assert tb.category_config(2168, FIX).get("bucket") == 1


def test_unknown_category_uses_global_default():
    assert tb.field_bucket(999999, "whatever", FIX) == tb.default_bucket(FIX)


def test_field_override_pins_bucket_two():
    assert tb.field_bucket(323, "owner.idNumber", FIX) == 2
    assert tb.field_bucket(2168, "escrowCompany", FIX) == 2
    assert tb.field_bucket(2168, "titleCompany", FIX) == 2


def test_prefix_override_covers_children():
    # override on `settlementAgent` (parent) applies to nested child
    assert tb.field_bucket(2168, "settlementAgent", FIX) == 2
    assert tb.field_bucket(2168, "settlementAgent.name", FIX) == 2


def test_non_overridden_field_takes_category_default():
    assert tb.field_bucket(2168, "buyers[].firstName", FIX) == 1


def test_cross_doc_source_points_to_cpl():
    assert tb.cross_doc_source(2168, "fileNumber", FIX) == 167
    assert tb.cross_doc_source(2168, "escrowCompany", FIX) == 167
    assert tb.cross_doc_source(2168, "settlementAgent.name", FIX) == 167  # prefix
    assert tb.cross_doc_source(323, "owner.idNumber", FIX) is None


# ── live-config sanity (reflects the CURRENT state, post ai_only fix) ───────
def test_live_untested_category_defaults_to_bucket_three():
    # 845 = Passport (in config, still untested); 375/2191 = not in config at all.
    for cid in (845, 375, 2191):
        assert tb.field_bucket(cid, "anything.at.all") == 3
        assert tb.is_tested(cid) is False


def test_live_id_number_fixed_is_bucket_one():
    # Issue A resolved by TaskTile (job a2643fae): DL idNumber now trusted.
    assert tb.field_bucket(323, "owner.idNumber") == 1
    # ALTA escrow/title now extract directly -> bucket 1, cross_doc retired.
    assert tb.field_bucket(2168, "escrowCompany") == 1
    assert tb.cross_doc_source(2168, "escrowCompany") is None


# ── decision flow (engine, against the stable fixture) ──────────────────────
def test_bucket1_trusts_valid_manifest_value():
    res = tf.resolve_field(2168, "buyers[].firstName", manifest_value="Jerardo",
                           landingai_lookup=lambda c, f: "NOPE", config_path=FIX)
    assert res.source == "manifest" and res.value == "Jerardo" and res.valid


def test_bucket1_falls_back_when_manifest_invalid():
    res = tf.resolve_field(
        2168, "buyers[].firstName",
        manifest_value="",  # empty -> invalid
        landingai_lookup=lambda c, f: "FromLandingAI",
        config_path=FIX,
    )
    assert res.source == "landingai" and res.value == "FromLandingAI"
    assert res.tried == ["manifest", "landingai"]


def test_bucket2_skips_manifest_and_uses_cross_doc():
    calls = {}

    def cross(sibling, fpath):
        calls["sibling"] = sibling
        return "Fidelity National Title Company"

    res = tf.resolve_field(
        2168, "escrowCompany",
        manifest_value="SHOULD_BE_IGNORED",
        cross_doc_lookup=cross,
        landingai_lookup=lambda c, f: "landingai_should_not_run",
        config_path=FIX,
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
        config_path=FIX,
    )
    assert res.source == "landingai" and res.value == "D1234567" and res.valid


def test_bucket3_checks_manifest_then_falls_back():
    hit = tf.resolve_field(349, "anyField", manifest_value="present-value", config_path=FIX)
    assert hit.source == "manifest" and hit.valid

    miss = tf.resolve_field(349, "anyField", manifest_value=None,
                            landingai_lookup=lambda c, f: "fallback", config_path=FIX)
    assert miss.source == "landingai"


def test_invalid_zip_triggers_fallback_in_bucket3():
    res = tf.resolve_field(
        349, "propertyAddress.zip",
        manifest_value="90005",           # wrong-but-present
        validator="zip",
        landingai_lookup=lambda c, f: "90605",
        config_path=FIX,
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
        config_path=FIX,
    )
    assert res2.source == "landingai" and res2.value == "90605"


def test_shadow_logger_receives_resolution():
    logged = []
    tf.resolve_field(2168, "buyers[].firstName", manifest_value="X",
                     shadow_logger=logged.append, config_path=FIX)
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


def test_fetch_enabled_requires_both_flags(monkeypatch):
    monkeypatch.delenv("TASKTILE_AI_ONLY_ENABLED", raising=False)
    monkeypatch.delenv("TASKTILE_AI_ONLY_FETCH", raising=False)
    assert tf.fetch_enabled() is False                      # both off

    monkeypatch.setenv("TASKTILE_AI_ONLY_FETCH", "true")
    assert tf.fetch_enabled() is False                      # ai_only still off -> gated

    monkeypatch.setenv("TASKTILE_AI_ONLY_ENABLED", "true")
    assert tf.fetch_enabled() is True                       # both on


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
