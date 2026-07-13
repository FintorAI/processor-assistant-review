"""review_urla_signatures — Tool for substep 3.3: Review 1003 URLA Signatures

Step 3 (STEP_03): 1003 URLA Lender
Phase: DATA_REVIEW

Checklist §03 #4 — confirm the 1003 URLA is signed and dated by the applicant
(borrower, and co-borrower when present) and by the Loan Officer / Originator.
Warn/info only; no Encompass writes.

# FACTORY-LOCK: true
"""

import json
import logging
from datetime import datetime, timezone
from typing import Annotated

from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.prebuilt import InjectedState
from langgraph.types import Command

from ._helpers import _doc, _efolder_present, _los

logger = logging.getLogger(__name__)

SUBSTEP = "3.3"


def _flag(title, severity, details, suggestion="", resolved=False):
    return {
        "substep": SUBSTEP,
        "title": title,
        "severity": severity,
        "details": details,
        "suggestion": suggestion,
        "resolved": resolved,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def _has_value(val) -> bool:
    if val is None:
        return False
    if isinstance(val, bool):
        return val
    return bool(str(val).strip())


@tool
def review_urla_signatures(
    tool_call_id: Annotated[str, InjectedToolCallId],
    state: Annotated[dict, InjectedState],
) -> Command:
    """Review 1003 URLA borrower and Loan Officer signatures (§03 #4).

    Warns when applicant or LO signature/date is missing from the extracted 1003.
    Co-borrower signature date required when a co-borrower is on the loan.
    No-op when the 1003 URLA is not in the eFolder. Call as STEP_03 substep 3.3.
    """
    loan_id = state.get("loan_id")
    if not loan_id:
        return Command(update={"messages": [ToolMessage(
            content=json.dumps({"error": "No loan_id in state. Run find_loan first."}),
            tool_call_id=tool_call_id,
        )]})

    if not _efolder_present(state, "1003 URLA"):
        result = {
            "success": True,
            "substep": SUBSTEP,
            "tool": "review_urla_signatures",
            "skipped": True,
            "message": "1003 URLA not in eFolder — signature review skipped.",
        }
        logger.info(f"[REVIEW_URLA_SIGNATURES] {result['message']}")
        return Command(update={"messages": [ToolMessage(
            content=json.dumps(result), tool_call_id=tool_call_id)]})

    logger.info(f"[REVIEW_URLA_SIGNATURES] Starting for loan {str(loan_id)[:8]}...")
    flags: list[dict] = []

    borrower_signed = _doc(state, "urla_signed")
    borrower_date = _doc(state, "borrower_signature_date")
    coborrower_date = _doc(state, "coborrower_signature_date")
    lo_signed = _doc(state, "loan_officer_signed")
    lo_date = _doc(state, "loan_officer_signature_date")
    has_coborrower = bool(_los(state, "coborrower_first_name"))

    borrower_ok = borrower_signed is True and _has_value(borrower_date)
    lo_ok = lo_signed is True and _has_value(lo_date)
    coborrower_ok = not has_coborrower or _has_value(coborrower_date)

    if borrower_signed is not True:
        flags.append(_flag(
            "§03 #4 URLA Borrower Signature Missing",
            "warning",
            "Borrower/applicant signature not detected on the 1003 URLA (urla_signed is not true).",
            "Obtain borrower signature on the 1003 before proceeding.",
        ))
    elif not _has_value(borrower_date):
        flags.append(_flag(
            "§03 #4 URLA Borrower Signature Date Missing",
            "warning",
            "Borrower signature appears present but signature date is blank on the 1003 URLA.",
            "Confirm the borrower signature date is filled in on the 1003.",
        ))
    else:
        flags.append(_flag(
            "§03 #4 URLA Borrower Signature Confirmed",
            "info",
            f"Borrower signed the 1003 URLA (date: {borrower_date}).",
            resolved=True,
        ))

    if has_coborrower and not _has_value(coborrower_date):
        flags.append(_flag(
            "§03 #4 URLA Co-Borrower Signature Missing",
            "warning",
            "Co-borrower is on the loan but co-borrower signature date is blank on the 1003 URLA.",
            "Obtain co-borrower signature and date on the 1003.",
        ))
    elif has_coborrower:
        flags.append(_flag(
            "§03 #4 URLA Co-Borrower Signature Confirmed",
            "info",
            f"Co-borrower signed the 1003 URLA (date: {coborrower_date}).",
            resolved=True,
        ))

    if lo_signed is not True:
        flags.append(_flag(
            "§03 #4 URLA Loan Officer Signature Missing",
            "warning",
            "Loan Officer / Originator signature not detected on the 1003 URLA (loan_officer_signed is not true).",
            "Have the LO sign and date the 1003 URLA.",
        ))
    elif not _has_value(lo_date):
        flags.append(_flag(
            "§03 #4 URLA Loan Officer Signature Date Missing",
            "warning",
            "LO signature appears present but signature date is blank on the 1003 URLA.",
            "Confirm the Loan Officer signature date is filled in on the 1003.",
        ))
    else:
        flags.append(_flag(
            "§03 #4 URLA Loan Officer Signature Confirmed",
            "info",
            f"Loan Officer signed the 1003 URLA (date: {lo_date}).",
            resolved=True,
        ))

    if borrower_ok and lo_ok and coborrower_ok:
        flags.append(_flag(
            "§03 #4 URLA Signatures Complete",
            "info",
            "1003 URLA is signed and dated by the applicant(s) and Loan Officer.",
            resolved=True,
        ))

    warning_count = sum(1 for f in flags if f["severity"] == "warning")
    result = {
        "success": True,
        "substep": SUBSTEP,
        "tool": "review_urla_signatures",
        "borrower_signed": borrower_signed,
        "borrower_signature_date": borrower_date,
        "loan_officer_signed": lo_signed,
        "loan_officer_signature_date": lo_date,
        "coborrower_required": has_coborrower,
        "flags_count": len(flags),
        "warning_count": warning_count,
        "message": (
            f"URLA signature review: {warning_count} warning(s)."
            if warning_count
            else "URLA signatures confirmed."
        ),
    }
    logger.info(f"[REVIEW_URLA_SIGNATURES] {result['message']}")

    update: dict = {"messages": [ToolMessage(content=json.dumps(result), tool_call_id=tool_call_id)]}
    if flags:
        update["flags"] = flags
    return Command(update=update)
