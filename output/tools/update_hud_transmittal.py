"""update_hud_transmittal — Tool for substep 12.2: HUD Transmittal

Step 12 (STEP_12): FHA-Specific Forms
Phase: FORM_UPDATES

Reviews the HUD-92900-LT (FHA Loan Transmittal). This form is normally completed
by the underwriter, so the agent verifies/flags rather than writes:
  - Source/EIN should be MMP / 52 (Government)
  - FHA Case Number + ADP code must be present

No-op when loan_type != FHA.
"""
# FACTORY-LOCK: true

import json
import logging
from datetime import datetime, timezone
from typing import Annotated

from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.prebuilt import InjectedState
from langgraph.types import Command

from ._helpers import _doc, _los, _profile

logger = logging.getLogger(__name__)


def _is_fha(state: dict) -> bool:
    """True when the loan is FHA.

    Checks BOTH the LOS Mortgage Type (field 1172, authoritative) and the Step-0
    loan_profile. The profile defaults to "Conventional" when the preflight type
    is blank, so it must never override an FHA value coming from the LOS field —
    treat the loan as FHA if either source says FHA.
    """
    los_lt = str(_los(state, "loan_type") or "").lower()
    prof_lt = str(_profile(state, "loan_type") or "").lower()
    return "fha" in los_lt or "fha" in prof_lt


@tool
def update_hud_transmittal(
    tool_call_id: Annotated[str, InjectedToolCallId],
    state: Annotated[dict, InjectedState],
) -> Command:
    """Review the HUD-92900-LT (FHA Loan Transmittal) for FHA loans.

    Flag-only — the underwriter completes this form. Confirms Source/EIN =
    MMP/52 (Government) and that the FHA Case Number + ADP code are present.
    No-op when loan_type != FHA. Call as STEP_12 substep 12.2.
    """
    loan_id = state.get("loan_id")
    if not loan_id:
        return Command(update={"messages": [ToolMessage(
            content=json.dumps({"error": "No loan_id in state. Run find_loan first."}),
            tool_call_id=tool_call_id,
        )]})

    # ── FHA gate ──
    if not _is_fha(state):
        result = {
            "success": True,
            "substep": "12.2",
            "tool": "update_hud_transmittal",
            "skipped": True,
            "message": "Not an FHA loan — HUD Transmittal skipped.",
        }
        logger.info(f"[UPDATE_HUD_TRANSMITTAL] {result['message']}")
        return Command(update={"messages": [ToolMessage(
            content=json.dumps(result), tool_call_id=tool_call_id)]})

    logger.info(f"[UPDATE_HUD_TRANSMITTAL] Starting for loan {str(loan_id)[:8]}...")

    flags: list[dict] = []
    fha_case_number = _los(state, "fha_case_number")
    # Field 1040 is the same case-number field FHA Management (11.1) may have just
    # written; state["los_fields"] isn't refreshed after a write, so also accept the
    # assigned case number from FHA Government Documents as "present".
    case_doc = _doc(state, "fha_assigned_case_number")
    case_present = bool(
        (fha_case_number and str(fha_case_number).strip())
        or (case_doc and str(case_doc).strip())
    )

    flags.append({
        "substep": "12.2",
        "title": "HUD-92900-LT Review Required",
        "severity": "info",
        "details": (
            "FHA loan — the HUD-92900-LT (FHA Loan Transmittal) is completed by the "
            "underwriter. Verify Source/EIN = MMP/52 (Government) and that the FHA "
            "Case Number + ADP code (703 for a standard 1-unit property) are present."
        ),
        "suggestion": "Confirm the HUD Transmittal details before underwriting sign-off.",
        "resolved": False,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })

    if not case_present:
        flags.append({
            "substep": "12.2",
            "title": "HUD-92900-LT Case Number Missing",
            "severity": "warning",
            "details": "FHA Case Number (field 1040) is blank — the HUD-92900-LT cannot be completed.",
            "suggestion": "Assign the FHA Case Number via FHA Connection before the underwriter completes the HUD-92900-LT.",
            "resolved": False,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    result = {
        "success": True,
        "substep": "12.2",
        "tool": "update_hud_transmittal",
        "fha_case_number_present": case_present,
        "flags_count": len(flags),
        "message": "HUD Transmittal reviewed" + (f" with {len(flags)} flags" if flags else ""),
    }
    logger.info(f"[UPDATE_HUD_TRANSMITTAL] {result['message']}")

    update: dict = {"messages": [ToolMessage(content=json.dumps(result), tool_call_id=tool_call_id)]}
    if flags:
        update["flags"] = flags
    return Command(update=update)
