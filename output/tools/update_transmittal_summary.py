"""update_transmittal_summary — Tool for substep 9.1: Update Transmittal Summary

Step 9 (STEP_09): Transmittal Summary
Phase: FORM_UPDATES

What this agent does:
  1. Rate check — compare Note Rate (field 3) vs Qualifying Rate (field 1014).
     Flag warning if they differ.
  2. Project Type info — read field 1553, surface as info flag.
  3. Condo pending flag — if property is Condo/PUD and project fields are blank,
     flag info that the computer-use agent must run Freddie Mac Condo Project Advisor.

What this agent does NOT do:
  - Populate Project Name (CX.CONDO.PROJECT.NAME) — requires browser lookup (CUA).
  - Populate CPM Project ID# (CX.CONDO.PROJECT.ID) — requires browser lookup (CUA).
  See ARCHITECTURE.md "Transmittal Summary — Condo Split" for the full design.
"""
# FACTORY-LOCK: true

import json
import logging
from datetime import datetime, timezone
from typing import Annotated, Optional

from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.prebuilt import InjectedState
from langgraph.types import Command

from ._helpers import _los, _profile

logger = logging.getLogger(__name__)

CONDO_PROPERTY_TYPES = {"condo", "condominium", "pud", "planned unit development"}


def _is_condo(property_type: Optional[str]) -> bool:
    if not property_type:
        return False
    return any(t in property_type.lower() for t in CONDO_PROPERTY_TYPES)


def _parse_rate(val: Optional[str]) -> Optional[float]:
    if val is None:
        return None
    try:
        return float(str(val).replace("%", "").strip())
    except (ValueError, TypeError):
        return None


@tool
def update_transmittal_summary(
    tool_call_id: Annotated[str, InjectedToolCallId],
    state: Annotated[dict, InjectedState],
) -> Command:
    """Review the 1008 Transmittal Summary: compare note rate vs qualifying rate,
    surface project type for info, and flag condo project fields as pending CUA.

    Call this tool during STEP_09 (Transmittal Summary) as substep 9.1.
    Reads LOS: note_rate, qualifying_rate, transmittal_project_type, property_type,
               condo_project_name, condo_project_id
    Flags: Note Rate vs Qualifying Rate Mismatch (warning), Project Type (info),
           Condo Project Fields Pending (info)
    """
    loan_id = state.get("loan_id")
    if not loan_id:
        return Command(update={"messages": [ToolMessage(
            content=json.dumps({"error": "No loan_id in state. Run find_loan first."}),
            tool_call_id=tool_call_id,
        )]})

    logger.info(f"[UPDATE_TRANSMITTAL_SUMMARY] Starting for loan {str(loan_id)[:8]}...")

    flags = []

    note_rate            = _los(state, "note_rate")             # field 3
    qualifying_rate      = _los(state, "qualifying_rate")       # field 1014
    project_type         = _los(state, "transmittal_project_type")  # field 1553
    property_type        = _los(state, "property_type")         # field 1041
    condo_project_name   = _los(state, "condo_project_name")    # CX.CONDO.PROJECT.NAME
    condo_project_id     = _los(state, "condo_project_id")      # CX.CONDO.PROJECT.ID

    ts = datetime.now(timezone.utc).isoformat()

    # ── Rule: Note Rate vs Qualifying Rate ──────────────────────────────────
    note_rate_f = _parse_rate(note_rate)
    qual_rate_f = _parse_rate(qualifying_rate)

    if note_rate_f is not None and qual_rate_f is not None:
        if abs(note_rate_f - qual_rate_f) > 0.001:
            flags.append({
                "substep": "9.1",
                "title": "Note Rate vs Qualifying Rate Mismatch",
                "severity": "warning",
                "details": (
                    f"Note Rate (field 3) = {note_rate_f:.3f}% "
                    f"but Qualifying Rate (field 1014) = {qual_rate_f:.3f}%. "
                    f"For fixed-rate loans these must match."
                ),
                "suggestion": "Reconcile rates — qualifying rate should equal note rate for fixed-rate loans.",
                "resolved": False,
                "timestamp": ts,
            })
        else:
            logger.info(f"[UPDATE_TRANSMITTAL_SUMMARY] Rates match: {note_rate_f:.3f}%")
    elif note_rate_f is None or qual_rate_f is None:
        missing = []
        if note_rate_f is None:
            missing.append("Note Rate (field 3)")
        if qual_rate_f is None:
            missing.append("Qualifying Rate (field 1014)")
        flags.append({
            "substep": "9.1",
            "title": "Rate Fields Not Populated",
            "severity": "warning",
            "details": f"Cannot compare rates — {', '.join(missing)} is blank.",
            "suggestion": "Ensure note rate and qualifying rate are populated in Encompass.",
            "resolved": False,
            "timestamp": ts,
        })

    # ── Rule: Project Type info ─────────────────────────────────────────────
    flags.append({
        "substep": "9.1",
        "title": "Project Type",
        "severity": "info",
        "details": (
            f"Transmittal Summary Project Type (field 1553) = {project_type!r}."
            if project_type else
            "Transmittal Summary Project Type (field 1553) is blank."
        ),
        "suggestion": "Verify project type is correct for this property.",
        "resolved": False,
        "timestamp": ts,
    })

    # ── Rule: Condo project fields pending CUA ──────────────────────────────
    if _is_condo(property_type):
        if not condo_project_name or not condo_project_id:
            missing_fields = []
            if not condo_project_name:
                missing_fields.append("Project Name (CX.CONDO.PROJECT.NAME)")
            if not condo_project_id:
                missing_fields.append("CPM Project ID# (CX.CONDO.PROJECT.ID)")
            flags.append({
                "substep": "9.1",
                "title": "Condo Project Fields Pending — CUA Required",
                "severity": "info",
                "details": (
                    f"Property type is {property_type!r} (Condo/PUD). "
                    f"Missing: {', '.join(missing_fields)}. "
                    f"These are populated by the computer-use agent after the "
                    f"Freddie Mac Condo Project Advisor browser lookup."
                ),
                "suggestion": "Ensure computer-use agent runs the Freddie Mac Condo Project Advisor substep.",
                "resolved": False,
                "timestamp": ts,
            })
        else:
            logger.info(
                f"[UPDATE_TRANSMITTAL_SUMMARY] Condo fields already populated: "
                f"name={condo_project_name!r}, id={condo_project_id!r}"
            )

    result = {
        "success": True,
        "substep": "9.1",
        "tool": "update_transmittal_summary",
        "note_rate": note_rate,
        "qualifying_rate": qualifying_rate,
        "project_type": project_type,
        "is_condo": _is_condo(property_type),
        "flags_count": len(flags),
        "message": (
            f"Transmittal Summary: note_rate={note_rate}, qualifying_rate={qualifying_rate}, "
            f"project_type={project_type!r}"
            + (f" with {len(flags)} flag(s)" if flags else "")
        ),
    }

    logger.info(f"[UPDATE_TRANSMITTAL_SUMMARY] {result['message']}")

    update = {"messages": [ToolMessage(content=json.dumps(result), tool_call_id=tool_call_id)]}
    if flags:
        update["flags"] = flags

    return Command(update=update)
