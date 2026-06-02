"""review_urla_emd — Tool for substep 5.2: EMD Check (2b)

Step 5 (STEP_05): 1003 URLA Part 3
Phase: DATA_REVIEW

# FACTORY-LOCK: true
"""

import json
import logging
from datetime import datetime, timezone
from typing import Annotated, Optional

from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.prebuilt import InjectedState
from langgraph.types import Command

from ._helpers import _doc
from shared.encompass_io import read_other_assets

logger = logging.getLogger(__name__)


def _flag(substep: str, title: str, severity: str, details: str, suggestion: str) -> dict:
    return {
        "substep": substep,
        "title": title,
        "severity": severity,
        "details": details,
        "suggestion": suggestion,
        "resolved": False,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def _parse_float(val) -> Optional[float]:
    if val is None:
        return None
    try:
        return float(str(val).replace(",", "").replace("$", "").strip())
    except (TypeError, ValueError):
        return None


@tool
def review_urla_emd(
    tool_call_id: Annotated[str, InjectedToolCallId],
    state: Annotated[dict, InjectedState],
) -> Command:
    """Review Section 3b — Earnest Money Deposit (EMD).

    Logic:
      1. Fetch otherAssets from Encompass v3 API; find the row where
         assetType == "EarnestMoney" to get the LOS-entered EMD value.
      2. Read the extracted EMD amount from the Purchase Agreement doc
         (emd_amount_pa field).
      3. Compare: if both values are present and differ by more than $1,
         flag "EMD Amount Mismatch".
      4. If neither source has a value, flag "EMD Not Found".
      5. Also read payment_terms and emd_payable_to from the Purchase
         Agreement and report them informatively.

    Reads API:  Encompass v3 otherAssets (assetType=EarnestMoney)
    Reads Docs: Purchase Agreement → emd_amount_pa, payment_terms, emd_payable_to

    Call this tool during STEP_05 (1003 URLA Part 3) as substep 5.2.
    """
    loan_id = state.get("loan_id")
    if not loan_id:
        return Command(update={"messages": [ToolMessage(
            content=json.dumps({"error": "No loan_id in state. Run find_loan first."}),
            tool_call_id=tool_call_id,
        )]})

    logger.info(f"[REVIEW_URLA_EMD] Starting for loan {str(loan_id)[:8]}...")

    flags = []

    # ── 1. Fetch EMD from Encompass otherAssets API ───────────────────────────
    # GET /encompass/v3/loans/{loanId}/applications/{applicationId}/otherAssets
    # Find the row where assetType == "EarnestMoney"
    other_assets = read_other_assets(loan_id, state=state)
    emd_asset = next(
        (r for r in other_assets if (r.get("assetType") or "") == "EarnestMoney"),
        None,
    )
    los_emd = _parse_float(emd_asset.get("cashOrMarketValue")) if emd_asset else None

    logger.info(f"[REVIEW_URLA_EMD] otherAssets EMD row: {emd_asset}")

    # ── 2. Read Purchase Agreement doc fields ─────────────────────────────────
    pa_emd_raw     = _doc(state, "emd_amount_pa")
    payment_terms  = _doc(state, "payment_terms")
    emd_payable_to = _doc(state, "emd_payable_to")

    pa_emd = _parse_float(pa_emd_raw)

    logger.info(
        f"[REVIEW_URLA_EMD] LOS EMD=${los_emd}, PA EMD={pa_emd_raw!r} (${pa_emd}), "
        f"payable_to={emd_payable_to!r}, terms={payment_terms!r}"
    )

    # ── 3. Compare ────────────────────────────────────────────────────────────
    if los_emd is not None and pa_emd is not None:
        if abs(los_emd - pa_emd) > 1.00:
            flags.append(_flag(
                "5.2",
                "EMD Amount Mismatch",
                "warning",
                (
                    f"Encompass otherAssets shows EMD = ${los_emd:,.2f}, "
                    f"but Purchase Agreement doc shows ${pa_emd:,.2f}. "
                    f"Difference: ${abs(los_emd - pa_emd):,.2f}."
                ),
                "Correct the EMD amount in Encompass (Section 3b / otherAssets) to match the Purchase Contract.",
            ))
    elif los_emd is None and pa_emd is None:
        flags.append(_flag(
            "5.2",
            "EMD Not Found",
            "warning",
            "No EarnestMoney entry found in Encompass otherAssets and EMD could not be "
            "extracted from the Purchase Agreement doc (emd_amount_pa is null — "
            "extraction may have hit an addendum instead of the main contract).",
            "Verify the EMD is entered in Encompass (Section 3b) and that the Purchase Agreement "
            "main contract is in the eFolder (not just addendums).",
        ))
    elif los_emd is None:
        flags.append(_flag(
            "5.2",
            "EMD Not Entered in Encompass (Section 3b)",
            "warning",
            f"Purchase Agreement doc shows EMD = ${pa_emd:,.2f}, "
            f"but no EarnestMoney row found in Encompass otherAssets.",
            "Add the EMD amount to Section 3b in Encompass.",
        ))
    elif pa_emd is None:
        # LOS has a value but doc extraction missed it — informational
        logger.info(
            f"[REVIEW_URLA_EMD] LOS EMD ${los_emd:,.2f} found; "
            "PA doc extraction returned null (likely hit an addendum)."
        )

    # ── 4. EMD check copy missing (emd_payable_to acts as proxy) ─────────────
    # Per notes: "8c - Re/Max Results - Email agent to get copy of check"
    # We don't have a separate "check present" field, so flag for manual follow-up.
    if los_emd and los_emd > 0:
        flags.append(_flag(
            "5.2",
            "EMD Check Copy — Confirm in eFolder",
            "info",
            (
                f"EMD of ${los_emd:,.2f} is recorded. "
                + (f"Payable to: {emd_payable_to}. " if emd_payable_to else "")
                + "Verify a copy of the EMD check is present in the eFolder."
            ),
            "If check copy is missing, email the Realtor/agent to request it.",
        ))

    # ── Build result ──────────────────────────────────────────────────────────
    result = {
        "success": True,
        "substep": "5.2",
        "tool": "review_urla_emd",
        "los_emd":        los_emd,
        "pa_emd":         pa_emd,
        "payment_terms":  payment_terms,
        "emd_payable_to": emd_payable_to,
        "flags_count":    len(flags),
        "message": (
            "EMD Check (2b) completed"
            + (f" — LOS ${los_emd:,.2f}" if los_emd else " — no LOS EMD")
            + (f", PA ${pa_emd:,.2f}" if pa_emd else ", no PA EMD")
            + (f", {len(flags)} flag(s)" if flags else "")
        ),
    }

    logger.info(f"[REVIEW_URLA_EMD] {result['message']}")

    update: dict = {
        "messages": [ToolMessage(content=json.dumps(result), tool_call_id=tool_call_id)],
    }
    if flags:
        update["flags"] = flags

    return Command(update=update)
