"""TaskTile-first + gap-driven LandingAI fallback — decision engine (scaffold).

Implements the 3-bucket trigger rule from docs/tasktile-integration-plan.md
("Fallback strategy"). This is source-agnostic: the caller injects how to read a
value from the manifest, from a sibling doc (cross-doc fill), and from LandingAI.
Nothing here calls TaskTile or LandingAI directly — wiring comes in a later PR.

Flow per field:
    bucket 1 (trust)  -> manifest value if valid, else fall back
    bucket 2 (missing)-> skip manifest, fall back (cross-doc -> LandingAI)
    bucket 3 (default)-> manifest value if valid, else fall back (cross-doc -> LandingAI)

Feature flags (env):
    TASKTILE_AI_ONLY_ENABLED  "1"/"true" to route through TaskTile at all (default off)
    TASKTILE_SHADOW_MODE      "1"/"true" (default on) — compute + log, do NOT write
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from shared import tasktile_buckets as buckets
from shared.tasktile_validation import get_validator, is_present


# ── feature flags ──────────────────────────────────────────────────────────
def _flag(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def ai_only_enabled() -> bool:
    return _flag("TASKTILE_AI_ONLY_ENABLED", False)


def shadow_mode() -> bool:
    return _flag("TASKTILE_SHADOW_MODE", True)


# ── result ─────────────────────────────────────────────────────────────────
@dataclass
class FieldResolution:
    category_id: Any
    field_path: str
    bucket: int
    value: Any = None
    source: str = "missing"        # manifest | cross_doc | landingai | missing
    valid: bool = False
    tried: list = field(default_factory=list)

    def as_log(self) -> dict:
        return {
            "category_id": self.category_id,
            "field": self.field_path,
            "bucket": self.bucket,
            "source": self.source,
            "valid": self.valid,
            "has_value": is_present(self.value),
            "tried": self.tried,
        }


# ── engine ─────────────────────────────────────────────────────────────────
def resolve_field(
    category_id,
    field_path: str,
    *,
    manifest_value: Any = None,
    cross_doc_lookup: Optional[Callable[[int, str], Any]] = None,
    landingai_lookup: Optional[Callable[[Any, str], Any]] = None,
    validator: Optional[str] = None,
    config_path: Optional[str] = None,
    shadow_logger: Optional[Callable[[dict], None]] = None,
) -> FieldResolution:
    """Resolve one field via the bucket rules.

    Args:
        manifest_value: value already read from the ai_only manifest (or None).
        cross_doc_lookup: fn(sibling_category_id, field_path) -> value | None.
        landingai_lookup: fn(category_id, field_path) -> value | None.
        validator: name in tasktile_validation.VALIDATORS (default "present").
    """
    bucket = buckets.field_bucket(category_id, field_path, config_path)
    check = get_validator(validator)
    res = FieldResolution(category_id=category_id, field_path=field_path, bucket=bucket)

    # 1) manifest (buckets 1 and 3 read it; bucket 2 skips it entirely)
    if bucket != 2:
        res.tried.append("manifest")
        if check(manifest_value):
            res.value, res.source, res.valid = manifest_value, "manifest", True
            _emit(shadow_logger, res)
            return res

    # 2) cross-doc fill (sibling doc in the same job)
    sibling = buckets.cross_doc_source(category_id, field_path, config_path)
    if sibling is not None and cross_doc_lookup is not None:
        res.tried.append(f"cross_doc:{sibling}")
        cv = cross_doc_lookup(sibling, field_path)
        if check(cv):
            res.value, res.source, res.valid = cv, "cross_doc", True
            _emit(shadow_logger, res)
            return res

    # 3) LandingAI (or targeted extractor)
    if landingai_lookup is not None:
        res.tried.append("landingai")
        lv = landingai_lookup(category_id, field_path)
        res.value, res.source, res.valid = lv, "landingai", check(lv)
        _emit(shadow_logger, res)
        return res

    _emit(shadow_logger, res)
    return res


def _emit(logger: Optional[Callable[[dict], None]], res: FieldResolution) -> None:
    if logger is not None:
        logger(res.as_log())


# ── manifest helpers ───────────────────────────────────────────────────────
def doc_by_category(manifest: dict, category_id) -> Optional[dict]:
    """First document in a manifest matching the category id."""
    for doc in (manifest or {}).get("documents", []) or []:
        cat = doc.get("category") or {}
        if str(cat.get("category_id")) == str(category_id):
            return doc
    return None


def flatten(obj: Any, prefix: str = "") -> dict:
    """Flatten nested extraction metadata to dotted paths (arrays -> '[]')."""
    out: dict = {}
    skip = {"source", "group_name", "group_index", "total_pages", "category_url"}
    if isinstance(obj, dict):
        for k, v in obj.items():
            if not prefix and k in skip:
                continue
            out.update(flatten(v, f"{prefix}{k}."))
    elif isinstance(obj, list):
        if obj:
            out.update(flatten(obj[0], f"{prefix}[]."))
    else:
        out[prefix.rstrip(".")] = obj
    return out
