"""Shared helpers for DocsOrch tools."""

import json
import logging
import os
from typing import Any, Dict

from shared.encompass_io import flush_field_writes_ledger

logger = logging.getLogger(__name__)

# ── Substep name lookup (built once at import time) ──

_SUBSTEP_NAMES: Dict[str, str] = {}


def _build_substep_names() -> None:
    """Populate _SUBSTEP_NAMES from workflow_config.json."""
    cfg_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "output", "config", "workflow_config.json",
    )
    try:
        with open(cfg_path, "r") as f:
            cfg = json.load(f)
        for step_id, step_data in cfg.get("steps", {}).items():
            step_num = str(int(step_id.replace("STEP_", "")))
            for ss_key, ss_data in step_data.get("substeps", {}).items():
                name = ss_data.get("name", "")
                if name:
                    _SUBSTEP_NAMES[f"{step_num}.{ss_key}"] = name
    except Exception as exc:
        logger.debug(f"Could not load substep names: {exc}")


_build_substep_names()


def _enrich_flags(update: Dict[str, Any]) -> None:
    """Add substep_name key to every flag and keep substep as the number only."""
    flags = update.get("flags")
    if not flags or not _SUBSTEP_NAMES:
        return
    for flag in flags:
        if not isinstance(flag, dict):
            continue
        ss = flag.get("substep", "")
        if not ss:
            continue
        # If substep was previously enriched with " — Name", split it back
        if " — " in ss:
            parts = ss.split(" — ", 1)
            flag["substep"] = parts[0]
            flag["substep_name"] = parts[1]
        elif "substep_name" not in flag:
            name = _SUBSTEP_NAMES.get(ss, "")
            flag["substep_name"] = name


def merge_ledger_into_update(update: Dict[str, Any]) -> Dict[str, Any]:
    """Drain the field-writes ledger and attach it to a Command update dict.

    Call this right before ``return Command(update=update)`` in any tool
    that may have called ``write_fields()`` during its execution.  The
    accumulated entries are moved into ``update["field_writes_ledger"]``
    so the LangGraph state reducer can append them to the run-wide ledger.

    Also enriches flag substep IDs with human-readable names.
    """
    ledger = flush_field_writes_ledger()
    if ledger:
        update["field_writes_ledger"] = ledger
    _enrich_flags(update)
    return update
