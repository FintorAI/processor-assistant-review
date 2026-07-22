"""update_hud_transmittal — Tool for substep 12.2: HUD Transmittal

Step 12 (STEP_12): FHA-Specific Forms
Phase: FORM_UPDATES

Two parts:
  1. Construction Status (field 1067) — write "ExistingConstruction" when
     blank (99% of loans are existing construction). Runs on EVERY loan type
     — field 1067 is a shared field that reflects on other forms (e.g.
     Transmittal Summary), not an FHA-only value. Filed here (rather than
     Transmittal Summary) because the HUD-92900-LT is where construction
     status is documented for FHA review, but the write is not gated on
     loan_type.
  2. HUD-92900-LT review — this form is normally completed by the
     underwriter, so the agent verifies/flags rather than writes:
       - Source/EIN should be MMP / 52 (Government)
       - FHA Case Number + ADP code must be present

Part 2 is FHA-only (no-op when loan_type != FHA); part 1 always runs.
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

from ._helpers import _doc, _get_or_detect_property_verification, _los, _profile, _write_fields

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
    """Review the HUD-92900-LT (FHA Loan Transmittal).

    1. Construction Status (field 1067) — write "ExistingConstruction" when
       blank; flag for manual confirmation when set to Proposed/Under
       Construction. Runs for every loan type.
    2. HUD-92900-LT review — flag-only, the underwriter completes this form.
       Confirms Source/EIN = MMP/52 (Government) and that the FHA Case Number
       + ADP code are present. FHA-only — no-op when loan_type != FHA.

    Call as STEP_12 substep 12.2.
    """
    loan_id = state.get("loan_id")
    if not loan_id:
        return Command(update={"messages": [ToolMessage(
            content=json.dumps({"error": "No loan_id in state. Run find_loan first."}),
            tool_call_id=tool_call_id,
        )]})

    logger.info(f"[UPDATE_HUD_TRANSMITTAL] Starting for loan {str(loan_id)[:8]}...")

    flags: list[dict] = []
    construction_status = _los(state, "construction_status")  # field 1067

    # Precomputed at STEP_01 1.3 (falls back to a live lookup if 1.3 was skipped).
    new_construction = (
        _get_or_detect_property_verification(state).get("new_construction") or {}
    )
    _is_new = bool(new_construction.get("is_new_construction"))

    # ── Construction Status (field 1067) — runs regardless of loan type.
    # Default is ExistingConstruction (99% of loans). When Zillow/1.3 indicates
    # new construction (< ~1 year), attempt to write "New" instead — if Encompass
    # rejects the enum value, _write_fields surfaces the rejection so we can
    # adjust the string. Verified live value "ExistingConstruction" on a
    # Conventional loan. ──
    _construction_current = (construction_status or "").strip()
    if not _construction_current:
        if _is_new:
            _target = "New"
            _label_detail = (
                f"Zillow indicates new construction"
                + (
                    f" (year_built={new_construction.get('year_built')})"
                    if new_construction.get("year_built") is not None else ""
                )
                + " — wrote 'New' instead of the default ExistingConstruction."
            )
            _flag_title = "Construction Status Set to New"
        else:
            _target = "ExistingConstruction"
            _label_detail = (
                "Field 1067 (Construction Status) was blank — wrote "
                "'ExistingConstruction' (99% of loans are existing construction)."
            )
            _flag_title = "Construction Status Set to Existing"
        _write_fields(
            loan_id, {"1067": _target}, substep="12.2", flags=flags,
            state=state, labels={"1067": "Construction Status"},
        )
        flags.append({
            "substep": "12.2",
            "title": _flag_title,
            "severity": "info",
            "details": _label_detail,
            "suggestion": "Verify Construction Status on the HUD Transmittal / Transmittal Summary.",
            "resolved": True,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        logger.info(
            f"[UPDATE_HUD_TRANSMITTAL] Wrote field 1067 (Construction Status) = {_target!r}"
        )
    elif _construction_current.lower() not in ("existingconstruction", "existing construction", "existing", "new"):
        flags.append({
            "substep": "12.2",
            "title": "Construction Status — Confirm Value",
            "severity": "info",
            "details": (
                f"Field 1067 (Construction Status) = {_construction_current!r} — 99% of "
                "loans are Existing Construction; confirm this loan is actually "
                "Proposed/Under Construction."
            ),
            "suggestion": "Confirm Proposed/Under Construction is correct for this loan.",
            "resolved": False,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
    elif _is_new and _construction_current.lower() in (
        "existingconstruction", "existing construction", "existing",
    ):
        flags.append({
            "substep": "12.2",
            "title": "Construction Status — New Construction Signal vs Existing",
            "severity": "warning",
            "details": (
                f"Field 1067 = {_construction_current!r}, but Zillow/1.3 indicates "
                f"new construction"
                + (
                    f" (year_built={new_construction.get('year_built')})"
                    if new_construction.get("year_built") is not None else ""
                )
                + ". Confirm whether Construction Status should be 'New'."
            ),
            "suggestion": "Verify Construction Status against the property listing / appraisal.",
            "resolved": False,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
    else:
        logger.info(f"[UPDATE_HUD_TRANSMITTAL] Field 1067 already populated: {_construction_current!r}")

    # ── FHA gate — HUD-92900-LT review is FHA-only ──
    if not _is_fha(state):
        result = {
            "success": True,
            "substep": "12.2",
            "tool": "update_hud_transmittal",
            "skipped_fha_only_sections": True,
            "construction_status": _construction_current or ("New" if _is_new else "ExistingConstruction"),
            "flags_count": len(flags),
            "message": (
                "Not an FHA loan — HUD-92900-LT review skipped; Construction Status "
                "(1067) check still ran." + (f" {len(flags)} flag(s)." if flags else "")
            ),
        }
        logger.info(f"[UPDATE_HUD_TRANSMITTAL] {result['message']}")
        update: dict = {"messages": [ToolMessage(content=json.dumps(result), tool_call_id=tool_call_id)]}
        if flags:
            update["flags"] = flags
        return Command(update=update)

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
        "construction_status": _construction_current or ("New" if _is_new else "ExistingConstruction"),
        "fha_case_number_present": case_present,
        "flags_count": len(flags),
        "message": "HUD Transmittal reviewed" + (f" with {len(flags)} flags" if flags else ""),
    }
    logger.info(f"[UPDATE_HUD_TRANSMITTAL] {result['message']}")

    update: dict = {"messages": [ToolMessage(content=json.dumps(result), tool_call_id=tool_call_id)]}
    if flags:
        update["flags"] = flags
    return Command(update=update)
