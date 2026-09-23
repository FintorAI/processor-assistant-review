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

from typing import Iterable, Optional

from shared import tasktile_buckets as buckets

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
