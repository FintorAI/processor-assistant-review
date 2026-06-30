"""update_processor_closing — Tool for substep 12.2: Processor Closing Update

Step 12 (STEP_12): Processor Workflow and Closing
Phase: FORM_UPDATES

For purchase loans, signing date = wire requested date = closing date.
Reads closing date from field 763 (Est Closing Date) and writes to:
  - CUST50FV     — Signing Date
  - CX.WIREDATELO — Wire Requested Date
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

from ._helpers import _los, _write_fields

logger = logging.getLogger(__name__)

FIELD_LABELS = {
    "CUST50FV":     "Signing Date",
    "CX.WIREDATELO": "Wire Requested Date",
}


def _to_iso_date(value: str) -> str | None:
    """Normalize a date string to ISO ``yyyy-MM-dd``.

    CUST50FV / CX.WIREDATELO are UTC date fields that require ISO format with no
    timezone offset. Encompass field 763 returns MM/DD/YYYY (e.g. ``06/18/2026``).
    Returns None if the value can't be parsed as a date.
    """
    if not value:
        return None
    raw = str(value).strip()
    # Drop a trailing time component if present (e.g. "06/18/2026 00:00:00").
    raw = raw.split("T")[0].split(" ")[0]
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


@tool
def update_processor_closing(
    tool_call_id: Annotated[str, InjectedToolCallId],
    state: Annotated[dict, InjectedState],
) -> Command:
    """Fill the Processor Closing screen. For purchase loans, set Signing Date
    and Wire Requested Date to the estimated closing date value (field 763).

    Call this tool during STEP_12 (Processor Workflow and Closing) as substep 12.2.
    Reads LOS: closing_date, signing_date, wire_requested_date, loan_purpose
    Flags: Closing Date Not Set (warning), Signing Date Not Set (warning)
    """
    loan_id = state.get("loan_id")
    if not loan_id:
        return Command(update={"messages": [ToolMessage(
            content=json.dumps({"error": "No loan_id in state. Run find_loan first."}),
            tool_call_id=tool_call_id,
        )]})

    logger.info(f"[UPDATE_PROCESSOR_CLOSING] Starting for loan {str(loan_id)[:8]}...")

    flags = []

    closing_date      = _los(state, "closing_date")  # field 763 (estimated/scheduled closing date)
    signing_date      = _los(state, "signing_date")       # CUST50FV (current value)
    # wire_requested_date (CX.WIREDATELO) is written, not read — overwritten with closing_date below
    loan_purpose      = _los(state, "loan_purpose")       # field 19
    is_purchase       = (loan_purpose or "").strip().lower() == "purchase"

    writes: dict[str, str] = {}

    if is_purchase:
        if not closing_date:
            flags.append({
                "substep": "12.2",
                "title": "Closing Date Not Set",
                "severity": "warning",
                "details": "Field 763 (Est Closing Date) is blank — cannot populate Signing Date or Wire Requested Date.",
                "suggestion": "Set the closing date in Encompass before running this step.",
                "resolved": False,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
        else:
            # CUST50FV / CX.WIREDATELO require ISO yyyy-MM-dd; field 763 is MM/DD/YYYY.
            iso_closing = _to_iso_date(closing_date)
            if not iso_closing:
                flags.append({
                    "substep": "12.2",
                    "title": "Closing Date Unparseable",
                    "severity": "warning",
                    "details": (
                        f"Field 763 (Est Closing Date) = {closing_date!r} could not be parsed "
                        "to ISO yyyy-MM-dd — Signing Date / Wire Requested Date not written."
                    ),
                    "suggestion": "Verify the closing date format in Encompass (expected MM/DD/YYYY).",
                    "resolved": False,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
            else:
                # For purchase: all three dates are the same
                writes["CUST50FV"]      = iso_closing
                writes["CX.WIREDATELO"] = iso_closing
    else:
        # Non-purchase: flag if signing date is blank, don't auto-fill
        if not signing_date:
            flags.append({
                "substep": "12.2",
                "title": "Signing Date Not Set",
                "severity": "warning",
                "details": f"Loan purpose is {loan_purpose!r} (not Purchase) — signing date must be set manually.",
                "suggestion": "Set the signing date on the Processor Closing screen.",
                "resolved": False,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

    if writes:
        _write_fields(loan_id, writes, substep="12.2", flags=flags, state=state, labels=FIELD_LABELS)

    result = {
        "success": True,
        "substep": "12.2",
        "tool": "update_processor_closing",
        "loan_purpose": loan_purpose,
        "closing_date": closing_date,
        "fields_written": list(writes.keys()),
        "flags_count": len(flags),
        "message": (
            (f"Processor Closing: set Signing Date and Wire Date to {writes['CUST50FV']} (purchase loan)"
             if writes else
             f"Processor Closing: no writes — {'closing date blank' if is_purchase else 'non-purchase loan'}")
            + (f" with {len(flags)} flag(s)" if flags else "")
        ),
    }

    logger.info(f"[UPDATE_PROCESSOR_CLOSING] {result['message']}")

    update = {"messages": [ToolMessage(content=json.dumps(result), tool_call_id=tool_call_id)]}
    if flags:
        update["flags"] = flags

    return Command(update=update)
