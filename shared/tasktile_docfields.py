"""Bridge between the processor's ``doc_fields`` model and the TaskTile bucket config.

The processor consumes an eFolder API that returns already-normalized
``ExtractedFields`` keyed by doc-type *name* + snake ``field_key``. The bucket
config (config/tasktile_doc_buckets.json) is keyed by SBIQ *category id* + AWM
field path. This module maps between the two so we can, for each real gap
(expected-but-missing doc field), classify it against expectations:

    bucket 1 gap  -> a TRUSTED field is missing  => investigate (possible regression)
    bucket 2 gap  -> KNOWN-MISSING from rns_ai_only => expected, go to fallback
    bucket 3 gap  -> untested/default            => fallback (conservative)

This is used for **shadow-mode analytics** first (log what we would do), before
any authoritative wiring. Field-level precision (snake field_key <-> AWM path)
needs the camelCase->snake field map (LOA's tasktile_field_map.json pattern) and
is a follow-up; today we resolve the bucket at category granularity plus the
config's field_overrides where the concept is unambiguous.
"""
from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Optional

from shared import tasktile_buckets as buckets

_DEFAULT_FIELD_MAP = Path(__file__).resolve().parent.parent / "config" / "tasktile_field_map.json"

# Processor doc-type NAME -> SBIQ category id. Includes spelling/label aliases.
DOC_TYPE_TO_CATEGORY: dict[str, int] = {
    "Driver's License": 323,
    "Drivers License": 323,
    "Passport": 845,
    "Permanent Resident Card": 844,
    "ALTA Settlement Statement": 2168,
    "Purchase Contract": 200,
    "Purchase Agreement": 200,
    "Credit Report": 117,
    "Closing Protection Letter": 167,
    "Closing Protection Letter (CPL)": 167,
    "Mortgage Insurance": 141,
    "MI Quote": 141,
    "Flood Certification": 538,
    "Flood Certificate": 538,
    "SSN Card": 843,
    "Social Security Card": 843,
    "Property Tax": 1481,
    "Closing Disclosure": 819,
    "Loan Estimate": 984,
    "Initial 1003": 349,
    "1003 URLA": 349,
    "Uniform Residential Loan Application (2020)": 349,
    "PMI Certificate": 816,
    "FHA MI Certificate": 1838,
    "Business Tax Return": 1,
    # Income / employment / asset docs (AWM category ids, verified via
    # GET /api/category-sets/awm). These feed state['doc_fields'] directly.
    "Paystubs": 16,
    "Paystub": 16,
    "Pay Stub": 16,
    "W-2": 25,
    "W2": 25,
    "Verification of Employment": 436,
    "Bank Statement": 502,
    "Bank Statements": 502,
    # Form 1040 (cat 10): routing alias kept for correct classification, but
    # INTENTIONALLY UNMAPPED in tasktile_field_map.json — the ai-only path returns
    # 0 data leaves for 1040s today. Add a fields block if TaskTile starts
    # extracting 1040 line items (AGI, wages, etc.).
    "Form 1040": 10,
    # Title / settlement / insurance / underwriting docs (AWM ids verified via
    # GET /api/category-sets/awm AND confirmed against real rns_ai_only manifests).
    "Title Report": 522,
    "Title Report / Commitment": 522,
    "Preliminary Report": 522,
    # Insurance: the EVIDENCE/binder (EOI) classifies as 1561, but the actual
    # homeowners POLICY classifies as 1479 Property Insurance on the ai-only path
    # (confirmed on loan 2606970588). The classification is unstable run-to-run,
    # so both 1561 and 1479 carry the SAME field_map spec — routing to either fills.
    "Evidence of Insurance": 1561,
    "Evidence of Hazard Insurance": 1561,
    "Homeowners Insurance": 1561,
    "Hazard Insurance": 1561,
    "Property Insurance": 1479,
    "Homeowners Insurance Policy": 1479,
    "Homeowner's Insurance Policy": 1479,
    "Transmittal Summary": 351,
    "Transmittal Summary (1008)": 351,
    "1008": 351,
    "MI Certificate": 141,
    "Mortgage Insurance Certificate": 141,
    "DU Findings / AUS Certificate": 324,
    "DU Findings": 324,
    "AUS Certificate": 324,
    "Conditional Commitment": 324,
    "Fraud Report": 1118,
    "Estimated Settlement Statement": 2168,
    "ESS": 2168,
    "Settlement Statement": 2168,
    "Appraisal (URAR / 1004)": 162,
    "Appraisal Report": 162,
    "Appraisal": 162,
}


def _norm(name: str) -> str:
    return "".join(ch for ch in (name or "").lower() if ch.isalnum())


# normalized alias lookup for fuzzy label matching
_NORM_LOOKUP = {_norm(k): v for k, v in DOC_TYPE_TO_CATEGORY.items()}


def category_for_doc_type(doc_type: str) -> Optional[int]:
    """Map a processor doc-type name to a SBIQ category id (exact, then fuzzy)."""
    if doc_type in DOC_TYPE_TO_CATEGORY:
        return DOC_TYPE_TO_CATEGORY[doc_type]
    return _NORM_LOOKUP.get(_norm(doc_type))


def _leaf(path: str) -> str:
    """Normalized last segment of an AWM path, e.g. 'owner.idNumber' -> 'idnumber'."""
    return _norm(path.split(".")[-1].replace("[]", ""))


def _match_override(overrides: dict, field_key: str) -> Optional[str]:
    """Match a snake field_key to an AWM override path via leaf-segment containment.

    e.g. field_key 'dl_id_number' matches override 'owner.idNumber' (leaf 'idnumber').
    Longest matching leaf wins. Returns the override key or None.
    """
    fk = _norm(field_key)
    best = None
    for k in overrides:
        leaf = _leaf(k)
        if len(leaf) >= 4 and leaf in fk and (best is None or len(_leaf(best)) < len(leaf)):
            best = k
    return best


def classify_gap(field_key: str, doc_types: Iterable[str], config_path: Optional[str] = None) -> dict:
    """Classify one missing field_key against bucket expectations.

    Picks the most informative doc_type (a mapped category wins) and returns:
        field_key, doc_type, category_id, category_name, bucket, tested,
        action, cross_doc_source
    """
    chosen_dt = None
    cat = None
    for dt in doc_types:
        c = category_for_doc_type(dt)
        if c is not None:
            chosen_dt, cat = dt, c
            break
    if chosen_dt is None:
        chosen_dt = next(iter(doc_types), None)

    if cat is None:
        return {
            "field_key": field_key, "doc_type": chosen_dt, "category_id": None,
            "category_name": None, "bucket": buckets.default_bucket(config_path),
            "tested": False, "action": "fallback_landingai", "cross_doc_source": None,
        }

    cfg = buckets.category_config(cat, config_path)
    bucket = int(cfg.get("bucket", buckets.default_bucket(config_path)))

    # Field-level refinement: a matching field_override pins this specific field
    # to its own bucket (e.g. owner.idNumber -> 2 even though the DL category is 1).
    overrides = cfg.get("field_overrides") or {}
    ov_key = _match_override(overrides, field_key)
    if ov_key is not None:
        bucket = int(overrides[ov_key])

    # Cross-doc sibling: prefer a field-precise match, else category-level.
    xdoc = cfg.get("cross_doc_source") or {}
    xkey = _match_override(xdoc, field_key)
    if xkey is not None:
        cross = int(xdoc[xkey])
    else:
        cross = int(next(iter(xdoc.values()))) if xdoc else None

    if bucket == 1:
        action = "investigate_bucket1_gap"     # trusted field missing => possible regression
    elif bucket == 2:
        action = "cross_doc" if cross is not None else "known_missing_fallback"
    else:
        action = "fallback_landingai"

    return {
        "field_key": field_key, "doc_type": chosen_dt, "category_id": cat,
        "category_name": cfg.get("name"), "bucket": bucket,
        "tested": bool(cfg.get("tested_job")), "action": action,
        "cross_doc_source": cross,
    }


def plan_doc_gaps(
    missing_keys: Iterable[str],
    doc_field_map: dict,
    required_doc_types: Optional[Iterable[str]] = None,
    config_path: Optional[str] = None,
) -> list[dict]:
    """Classify every missing doc field against the bucket config.

    Args:
        missing_keys: field_keys that were expected but not populated.
        doc_field_map: DOC_FIELD_MAP (doc_type name -> [field_key, ...]).
        required_doc_types: restrict the field_key -> doc_type inversion to these.
    """
    required = set(required_doc_types) if required_doc_types is not None else None
    # invert: field_key -> [doc_type, ...]
    key_to_docs: dict[str, list] = {}
    for dt, keys in (doc_field_map or {}).items():
        if required is not None and dt not in required:
            continue
        for k in keys:
            key_to_docs.setdefault(k, []).append(dt)

    plan = []
    for key in missing_keys:
        docs = key_to_docs.get(key, [])
        if not docs:
            continue  # field not owned by any required doc type
        plan.append(classify_gap(key, docs, config_path))
    return plan


def summarize_plan(plan: list[dict]) -> dict:
    """Count gaps by recommended action (for shadow-mode logging)."""
    out: dict[str, int] = {}
    for g in plan:
        out[g["action"]] = out.get(g["action"], 0) + 1
    return out


def gap_doc_types(plan: list[dict]) -> list[str]:
    """Distinct doc-type names that have at least one gap (for targeted fetch)."""
    seen = []
    for g in plan:
        dt = g.get("doc_type")
        if dt and dt not in seen:
            seen.append(dt)
    return seen


@lru_cache(maxsize=4)
def load_field_map(path: Optional[str] = None) -> dict:
    """Load the manifest-leaf -> processor-field_key map (per category id)."""
    p = Path(path) if path else Path(os.getenv("TASKTILE_FIELD_MAP_CONFIG", _DEFAULT_FIELD_MAP))
    with open(p) as f:
        return json.load(f)


def _has_value(entry) -> bool:
    """True if a doc_fields entry already holds a usable value."""
    if entry is None:
        return False
    v = entry.get("value") if isinstance(entry, dict) else entry
    return v not in (None, "", [], {}, "null")


# SBIQ categories whose documents carry a single person's government ID
# (Driver's License, Passport, Permanent Resident Card). A borrower and a
# co-borrower arrive as SEPARATE ID attachments, so the gov-id number must be
# stacked as multi-copy doc_fields (copy 0 = borrower, copy 1 = co-borrower, …)
# rather than filling one slot and dropping the rest. review_borrower_summary's
# _write_government_id then matches each copy to a person (by name, else copy
# order) and writes field 5053 (borrower) / 5054 (co-borrower).
_ID_DOC_CATEGORIES = {323, 845, 844}


def _first_present_leaf(flat: dict, leaf_spec):
    """Resolve a spec ``leaf`` (str or candidate list) to (leaf, value).

    Returns the first candidate that carries a present value; falls back to the
    first candidate path when none carry a value.
    """
    candidates = leaf_spec if isinstance(leaf_spec, list) else [leaf_spec]
    leaf = candidates[0]
    value = flat.get(leaf)
    for cand in candidates:
        v = flat.get(cand)
        if v not in (None, "", [], {}):
            return cand, v
    return leaf, value


def _id_person_name(entries: dict, flat: dict) -> Optional[str]:
    """Build a 'First Last' name for an ID doc from its mapped name leaves.

    Used to align a ``dl_borrower_name`` copy with each ``dl_gov_id`` copy so the
    review tool can match the co-borrower's ID to the co-borrower by name.
    """
    parts = []
    for fk in ("borrower_first_name", "borrower_last_name"):
        spec = entries.get(fk)
        if not spec:
            continue
        _, v = _first_present_leaf(flat, spec.get("leaf"))
        if v not in (None, "", [], {}):
            parts.append(str(v))
    return " ".join(parts) or None


def _append_id_copy(doc_fields: dict, gov_id, name, leaf) -> bool:
    """Append a government-ID copy (and aligned name copy) to ``doc_fields``.

    Copy 0 is the borrower; each subsequent distinct ID doc becomes the next
    copy (co-borrower, etc.). The top-level ``value`` is seeded from copy 0 so
    ``_doc(state, "dl_gov_id")`` stays backward compatible. De-dupes by value so
    the same person's ID (re-uploaded) is not counted twice. Returns True when a
    new copy was added.
    """
    gid = doc_fields.get("dl_gov_id")
    gid = gid if isinstance(gid, dict) else {}
    copies = list(gid.get("copies") or [])
    if any(str(c.get("value")) == str(gov_id) for c in copies):
        return False  # same ID already captured
    ci = len(copies)
    copies.append({
        "value": gov_id, "source_document": "tasktile_ai_only",
        "confidence": 1.0, "copy_index": ci, "raw_key": leaf,
    })
    gid["copies"] = copies
    if not _has_value(gid):  # seed top-level (borrower) for _doc() compat
        gid.update({"value": gov_id, "source_document": "tasktile_ai_only",
                    "confidence": 1.0, "raw_key": leaf})
    doc_fields["dl_gov_id"] = gid

    if name:  # aligned name copy for per-person matching in the review tool
        nm = doc_fields.get("dl_borrower_name")
        nm = nm if isinstance(nm, dict) else {}
        ncopies = list(nm.get("copies") or [])
        ncopies.append({"value": name, "source_document": "tasktile_ai_only",
                        "confidence": 1.0, "copy_index": ci})
        nm["copies"] = ncopies
        if not _has_value(nm):
            nm.update({"value": name, "source_document": "tasktile_ai_only",
                       "confidence": 1.0})
        doc_fields["dl_borrower_name"] = nm
    return True


def _stack_id_gov_id(doc_fields, entries, flat, *, cat, apply,
                     config_path, shadow_logger) -> Optional[dict]:
    """Resolve & stack an ID doc's government-ID number as a multi-copy fill.

    Unlike the single-slot path this bypasses the no-clobber guard so a second
    ID attachment (the co-borrower) is captured as an additional copy — but it
    still yields to a *primary* extraction (any non-ai-only source already on
    ``dl_gov_id``), preserving the no-clobber contract for trusted data.
    """
    from shared.tasktile_fallback import resolve_field

    spec = entries.get("dl_gov_id")
    if not spec:
        return None
    leaf, manifest_value = _first_present_leaf(flat, spec.get("leaf"))
    res = resolve_field(cat, leaf, manifest_value=manifest_value,
                        validator=spec.get("validator"),
                        config_path=config_path, shadow_logger=shadow_logger)
    if not (res.valid and res.value not in (None, "", [], {})):
        return None

    # Respect a primary extraction: only stack co-borrower copies on top of our
    # own ai-only fills, never over trusted/primary data.
    existing = doc_fields.get("dl_gov_id")
    if (isinstance(existing, dict) and _has_value(existing)
            and existing.get("source_document") != "tasktile_ai_only"):
        return None

    applied = False
    if apply:
        applied = _append_id_copy(doc_fields, res.value,
                                  _id_person_name(entries, flat), leaf)
    return {
        "field_key": "dl_gov_id", "value": res.value,
        "source": f"tasktile_ai_only:{res.source}", "category_id": cat,
        "leaf": leaf, "applied": applied, "copy": True,
    }


def resolve_and_fill(
    doc_fields: dict,
    manifest: dict,
    att_to_doctype: dict,
    *,
    field_map_path: Optional[str] = None,
    config_path: Optional[str] = None,
    apply: bool = False,
    shadow_logger=None,
) -> list[dict]:
    """Translate manifest leaves -> processor field_keys and (optionally) fill gaps.

    For each manifest doc, resolve its doc_type via ``att_to_doctype`` (rns_ai_only
    does NOT tag category), map to a SBIQ category, then for every mapped field_key
    run the bucket engine (``resolve_field``) with the manifest leaf value + validator.
    A field is proposed only when it is currently missing/empty in ``doc_fields`` and
    the resolved value is present + valid. Bucket-2/3 fields resolve to fallback (no
    manifest trust) and are skipped here.

    Args:
        doc_fields: the processor's normalized doc_fields (mutated iff ``apply``).
        manifest: an rns_ai_only manifest.
        att_to_doctype: {root_attachment_id: doc_type_name}.
        apply: when True, write proposed fills into ``doc_fields``. When False
            (shadow), only return the proposals.

    Returns a list of proposal dicts: {field_key, value, source, category_id, leaf, applied}.
    """
    from shared.tasktile_fallback import flatten, resolve_field

    fmap = load_field_map(field_map_path)
    cats = fmap.get("categories", {})
    proposals: list[dict] = []

    for d in manifest.get("documents") or []:
        dt = att_to_doctype.get(d.get("root_attachment_id"))
        if not dt:
            continue
        cat = category_for_doc_type(dt)
        if cat is None:
            continue
        entries = (cats.get(str(cat)) or {}).get("fields") or {}
        if not entries:
            continue
        flat = flatten(d.get("metadata") or d.get("content") or {})
        is_id_cat = cat in _ID_DOC_CATEGORIES
        for field_key, spec in entries.items():
            if is_id_cat and field_key == "dl_gov_id":
                # Government-ID number is multi-copy (borrower + co-borrower are
                # separate ID attachments) — handled by _stack_id_gov_id below.
                continue
            if _has_value(doc_fields.get(field_key)):
                continue  # never clobber an existing extraction
            # `leaf` may be a single path or a list of candidate paths (the
            # ai-only output shape is unstable run-to-run, e.g. ALTA). Pick the
            # first candidate that carries a value; fall back to the first path.
            leaf_spec = spec.get("leaf")
            candidates = leaf_spec if isinstance(leaf_spec, list) else [leaf_spec]
            leaf = candidates[0]
            manifest_value = flat.get(leaf)
            for cand in candidates:
                v = flat.get(cand)
                if v not in (None, "", [], {}):
                    leaf, manifest_value = cand, v
                    break
            res = resolve_field(
                cat, leaf,
                manifest_value=manifest_value,
                validator=spec.get("validator"),
                config_path=config_path,
                shadow_logger=shadow_logger,
            )
            if not (res.valid and res.value not in (None, "", [], {})):
                continue
            proposal = {
                "field_key": field_key, "value": res.value,
                "source": f"tasktile_ai_only:{res.source}",
                "category_id": cat, "leaf": leaf, "applied": bool(apply),
            }
            if apply:
                doc_fields[field_key] = {
                    "value": res.value,
                    "source_document": "tasktile_ai_only",
                    "confidence": 1.0,
                    "raw_key": leaf,
                }
            proposals.append(proposal)

        if is_id_cat:
            # Stack this ID doc's gov-id number as a copy (borrower = copy 0,
            # co-borrower = copy 1, …) so both people's IDs are captured.
            id_prop = _stack_id_gov_id(
                doc_fields, entries, flat, cat=cat, apply=apply,
                config_path=config_path, shadow_logger=shadow_logger,
            )
            if id_prop:
                proposals.append(id_prop)
    return proposals


def manifest_coverage(manifest: dict) -> dict:
    """Summarize what an rns_ai_only manifest actually returned per document.

    Shadow-mode only — reports the extracted leaf fields per doc so we can see, for
    each gap doc type, whether TaskTile would have supplied the value. Field-precise
    fill (leaf -> processor field_key) is a follow-up; this proves the loop end-to-end.

    Returns ``{job_id, docs: [{root_attachment_id, category_id, leaf_count, leaf_keys}]}``.
    """
    from shared.tasktile_fallback import flatten  # reuse the same flattener

    out = {"job_id": (manifest.get("_processor") or {}).get("job_id"), "docs": []}
    for d in manifest.get("documents") or []:
        md = d.get("metadata") or d.get("content") or {}
        leaves = list(flatten(md))
        out["docs"].append({
            "root_attachment_id": d.get("root_attachment_id"),
            "category_id": d.get("category_id"),
            "leaf_count": len(leaves),
            "leaf_keys": leaves,
        })
    return out
