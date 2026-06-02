"""review_urla_liabilities — Tool for substep 5.3: Liabilities and VOL (2c)

Step 5 (STEP_05): 1003 URLA Part 3
Phase: DATA_REVIEW

# FACTORY-LOCK: true
"""

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, List, Dict, Any

from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.prebuilt import InjectedState
from langgraph.types import Command



ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

logger = logging.getLogger(__name__)

SUBSTEP = "5.3"


def _flag(title: str, severity: str, details: str, suggestion: str) -> Dict[str, Any]:
    return {
        "substep": SUBSTEP,
        "title": title,
        "severity": severity,
        "details": details,
        "suggestion": suggestion,
        "resolved": False,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@tool
def review_urla_liabilities(
    tool_call_id: Annotated[str, InjectedToolCallId],
    state: Annotated[dict, InjectedState],
) -> Command:
    """Review Section 3c — Liabilities (VOL).

    Fetches all VOL rows from the Encompass v3 API and checks:
      - Column 1 (Exclude Monthly Payment = Y): flag each excluded debt and ask
        why it was excluded (already paid off? not obligated?).
      - Column 2 (To Be Paid Off = Y): flag each such debt and request the most
        recent statement for that creditor (e.g. JPMCB card).

    Call this tool during STEP_05 (1003 URLA Part 3) as substep 5.3.
    """
    loan_id = state.get("loan_id")
    if not loan_id:
        return Command(update={"messages": [ToolMessage(
            content=json.dumps({"error": "No loan_id in state. Run find_loan first."}),
            tool_call_id=tool_call_id,
        )]})

    logger.info(f"[REVIEW_URLA_LIABILITIES] Starting for loan {str(loan_id)[:8]}...")

    flags: List[Dict[str, Any]] = []

    # ── Fetch VOL rows from Encompass v3 API ──
    try:
        from shared.encompass_io import read_vols
        vols = read_vols(loan_id, state=state)
        logger.info(f"[REVIEW_URLA_LIABILITIES] {len(vols)} VOL row(s) fetched")
    except LookupError:
        flags.append(_flag(
            title="VOL Collection Not Created in Encompass",
            severity="blocking",
            details="The Verification of Liabilities (VOL) collection does not exist yet in "
                    "Encompass for this loan. No liability rows have been entered.",
            suggestion="Open the 1003 Section 3c in Encompass, run credit or manually enter "
                       "liabilities before reviewing.",
        ))
        vols = []
    except Exception as exc:
        logger.warning(f"[REVIEW_URLA_LIABILITIES] Failed to fetch VOLs: {exc}")
        flags.append(_flag(
            title="VOL API Error",
            severity="warning",
            details=f"Could not retrieve VOL data from Encompass: {exc}",
            suggestion="Manually review Section 3c in Encompass.",
        ))
        vols = []

    # ── Column 1: Excluded Monthly Payment ──
    # excludedFromTotalMonthlyPaymentIndicator = true → payment excluded from DTI
    # Must document why (already paid off? non-obligated?); cannot silently omit.
    excluded = [v for v in vols if v.get("exclude_monthly_pay")]
    for vol in excluded:
        creditor = vol["holder_name"] or "Unknown creditor"
        balance  = vol["unpaid_balance"]
        payment  = vol["monthly_payment"]
        acct_snip = (vol["account_number"] or "")[-4:] or "N/A"
        flags.append(_flag(
            title=f"Excluded Liability — Explanation Required: {creditor}",
            severity="warning",
            details=(
                f"{creditor} (acct …{acct_snip}) has its monthly payment excluded from DTI. "
                f"Balance: ${balance:,.2f}  |  Monthly payment: ${payment:,.2f}. "
                "Column 1 (Exclude Monthly Payment) is checked Y."
            ),
            suggestion=(
                "Document the reason for exclusion in the file (e.g. already paid off, "
                "lease not in borrower's name, non-obligated coborrower debt, etc.)."
            ),
        ))

    # ── Column 2: To Be Paid Off ──
    # payoffIncludedIndicator = true → debt will be paid off at closing
    # Require the most recent statement for that creditor.
    payoffs = [v for v in vols if v.get("payoff_included")]
    for vol in payoffs:
        creditor = vol["holder_name"] or "Unknown creditor"
        balance  = vol["unpaid_balance"]
        payment  = vol["monthly_payment"]
        acct_snip = (vol["account_number"] or "")[-4:] or "N/A"
        flags.append(_flag(
            title=f"Payoff Statement Required: {creditor}",
            severity="warning",
            details=(
                f"{creditor} (acct …{acct_snip}) is marked To Be Paid Off. "
                f"Balance: ${balance:,.2f}  |  Monthly payment: ${payment:,.2f}. "
                "Column 2 (To Be Paid Off) is checked Y."
            ),
            suggestion=(
                f"Request the most recent statement for {creditor} (acct …{acct_snip}) "
                "and upload it to the eFolder. Confirm payoff amount before closing."
            ),
        ))

    # ── Section 3d: Other Liabilities ──
    # Flag as info if any other-liability rows exist (alimony, job-related expenses, etc.)
    try:
        from shared.encompass_io import read_other_liabilities
        other_liabs = read_other_liabilities(loan_id, state=state)
        logger.info(f"[REVIEW_URLA_LIABILITIES] {len(other_liabs)} other liability row(s)")
    except Exception as exc:
        logger.warning(f"[REVIEW_URLA_LIABILITIES] Could not fetch otherLiabilities: {exc}")
        other_liabs = []

    if other_liabs:
        lines = []
        for item in other_liabs:
            label = item["description"] or item["liability_type"] or "Unknown"
            owner = item["owner"] or "Borrower"
            pmt   = item["monthly_payment"]
            lines.append(f"  • {label} ({owner}): ${pmt:,.2f}/mo")
        flags.append(_flag(
            title="Section 3d — Other Liabilities Present",
            severity="info",
            details=(
                f"{len(other_liabs)} other liabilit{'y' if len(other_liabs)==1 else 'ies'} "
                f"found in Encompass (Section 3d):\n" + "\n".join(lines)
            ),
            suggestion="Verify these are correctly entered and accounted for in DTI.",
        ))

    # ── Informational summary (no flags to raise) ──
    if vols and not excluded and not payoffs and not other_liabs:
        logger.info("[REVIEW_URLA_LIABILITIES] No excluded, payoff-flagged, or other liability rows found.")

    # ── Build result ──
    result = {
        "success": True,
        "substep": SUBSTEP,
        "tool": "review_urla_liabilities",
        "vol_count": len(vols),
        "excluded_count": len(excluded),
        "payoff_count": len(payoffs),
        "other_liabilities_count": len(other_liabs),
        "flags_count": len(flags),
        "message": (
            f"VOL review complete — {len(vols)} liabilit{'y' if len(vols)==1 else 'ies'} (2c), "
            f"{len(other_liabs)} other liabilit{'y' if len(other_liabs)==1 else 'ies'} (2d), "
            f"{len(excluded)} excluded, {len(payoffs)} to-be-paid-off"
            + (f"; {len(flags)} flag(s) raised" if flags else "")
        ),
    }

    logger.info(f"[REVIEW_URLA_LIABILITIES] {result['message']}")

    update = {
        "messages": [ToolMessage(content=json.dumps(result), tool_call_id=tool_call_id)],
    }
    if flags:
        update["flags"] = flags

    return Command(update=update)
