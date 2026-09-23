"""TaskTile `rns_ai_only` extraction-confidence buckets.

Loads and resolves the hand-maintained bucket config at
``config/tasktile_doc_buckets.json`` (see docs/tasktile-integration-plan.md →
"Fallback strategy"). This module is pure config resolution — no I/O to TaskTile
or LandingAI. The runtime decision engine lives in ``tasktile_fallback.py``.

Bucket meanings:
    1 = TRUST            use the manifest value (fall back only if empty/invalid)
    2 = KNOWN-MISSING    skip the manifest; go straight to fallback (cross-doc, then LandingAI)
    3 = CHECK-THEN-FALLBACK (default)  read manifest; fall back if absent or invalid
"""
from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Optional

_DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "config" / "tasktile_doc_buckets.json"

# Bucket used when a category is entirely absent from the config.
_HARD_DEFAULT_BUCKET = 3


@lru_cache(maxsize=8)
def load_buckets(path: Optional[str] = None) -> dict:
    """Load and cache the bucket config. Pass ``path`` to override (tests)."""
    cfg_path = Path(path) if path else Path(os.getenv("TASKTILE_BUCKETS_CONFIG", _DEFAULT_CONFIG))
    with open(cfg_path) as f:
        return json.load(f)


def _cfg(path: Optional[str] = None) -> dict:
    return load_buckets(path)


def category_config(category_id, path: Optional[str] = None) -> dict:
    """Raw config block for a category id (str or int). ``{}`` if unknown."""
    cats = _cfg(path).get("categories", {})
    return cats.get(str(category_id), {})


def default_bucket(path: Optional[str] = None) -> int:
    return int(_cfg(path).get("default_bucket", _HARD_DEFAULT_BUCKET))


def _override_matches(override_key: str, field_path: str) -> bool:
    """An override applies to an exact path or to any descendant of it.

    e.g. an override on ``settlementAgent`` also covers ``settlementAgent.name``;
    ``titleCharges[].description`` matches only itself.
    """
    if override_key == field_path:
        return True
    # parent-object override covers nested children
    return field_path.startswith(override_key + ".")


def field_bucket(category_id, field_path: str, path: Optional[str] = None) -> int:
    """Resolve the bucket for a specific (category, field).

    Precedence: field_overrides (most specific match) > category bucket > global default.
    """
    cat = category_config(category_id, path)
    overrides = cat.get("field_overrides", {})
    # most specific (longest) matching override wins
    best_key = None
    for k in overrides:
        if _override_matches(k, field_path) and (best_key is None or len(k) > len(best_key)):
            best_key = k
    if best_key is not None:
        return int(overrides[best_key])
    if "bucket" in cat:
        return int(cat["bucket"])
    return default_bucket(path)


def cross_doc_source(category_id, field_path: str, path: Optional[str] = None) -> Optional[int]:
    """Sibling category id to try (same job) before LandingAI, or None.

    Matches exact field or any ancestor (so ``settlementAgent`` covers ``settlementAgent.name``).
    """
    src = category_config(category_id, path).get("cross_doc_source", {})
    best_key = None
    for k in src:
        if _override_matches(k, field_path) and (best_key is None or len(k) > len(best_key)):
            best_key = k
    return int(src[best_key]) if best_key is not None else None


def is_tested(category_id, path: Optional[str] = None) -> bool:
    """True if this category has been empirically verified (has a tested_job)."""
    return bool(category_config(category_id, path).get("tested_job"))
