"""review_urla_other_income — Tool for substep 4.2: Other Income (1e)

Step 4 (STEP_04): 1003 URLA Page 2
Phase: DATA_REVIEW

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

from ._helpers import _los, _doc, _profile

logger = logging.getLogger(__name__)

# Income types that require specific documentation
_DOC_REQUIREMENTS = {
    "alimony":       "court order or divorce decree",
    "child support": "court order",
    "dividend":      "brokerage / investment statements (2 years)",
    "interest":      "brokerage / investment statements (2 years)",
    "social security": "Social Security award letter",
    "disability":    "disability award letter",
    "rental":        "lease agreement and Schedule E",
    "pension":       "pension award letter",
    "retirement":    "retirement award letter",
}


def _flag(substep, title, severity, details, suggestion):
    return {
        "substep": substep,
        "title": title,
        "severity": severity,
        "details": details,
        "suggestion": suggestion,
        "resolved": False,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def _is_checked(val) -> bool:
    return str(val or "").strip().lower() in ("true", "yes", "1", "checked")


@tool
def review_urla_other_income(
    tool_call_id: Annotated[str, InjectedToolCallId],
    state: Annotated[dict, InjectedState],
) -> Command:
    """Review Section 1e — Other Sources of Income.

    Checks URLA.X40 (borrower) and URLA.X41 (co-borrower) Does Not Apply
    checkboxes. If neither is checked, other income fields must be populated
    or the section flagged for review. When income is present, verifies that
    appropriate documentation requirements are noted.

    Call this tool during STEP_04 (1003 URLA Page 2) as substep 4.2.
    """
    loan_id = state.get("loan_id")
    if not loan_id:
        return Command(update={"messages": [ToolMessage(
            content=json.dumps({"error": "No loan_id in state. Run find_loan first."}),
            tool_call_id=tool_call_id,
        )]})

    logger.info(f"[REVIEW_URLA_OTHER_INCOME] Starting for loan {str(loan_id)[:8]}...")

    flags = []

    # ── Read LOS fields ───────────────────────────────────────────────────────
    other_income_type   = _los(state, "other_income_type")    # Field 172
    other_income_amount = _los(state, "other_income_amount")  # Field 173
    borr_dna            = _los(state, "borr_other_income_dna")   # URLA.X40
    coborr_dna          = _los(state, "coborr_other_income_dna") # URLA.X41

    borr_dna_checked   = _is_checked(borr_dna)
    coborr_dna_checked = _is_checked(coborr_dna)

    income_type_val   = (other_income_type or "").strip()
    income_amount_val = (other_income_amount or "").strip()

    has_other_income = bool(income_type_val or income_amount_val)

    # ── Rule: Does Not Apply vs income fields ────────────────────────────────
    if not has_other_income:
        if not borr_dna_checked and not coborr_dna_checked:
            flags.append(_flag("4.2",
                "Other Income Section Incomplete (1e)",
                "info",
                "Section 1e has no other income entries and neither URLA.X40 (borrower) nor URLA.X41 (co-borrower) Does Not Apply is checked.",
                "Confirm with borrower whether other income exists. If not applicable, check URLA.X40 and/or URLA.X41.",
            ))
    else:
        # Income is present — check for type/amount completeness
        if income_type_val and not income_amount_val:
            flags.append(_flag("4.2",
                "Other Income Amount Missing",
                "warning",
                f"Income type '{income_type_val}' is entered but monthly amount (Field 173) is empty.",
                "Enter the monthly other income amount in Section 1e",
            ))
        elif income_amount_val and not income_type_val:
            flags.append(_flag("4.2",
                "Other Income Type Missing",
                "warning",
                f"Other income amount ${income_amount_val} is entered but income type (Field 172) is empty.",
                "Enter the other income type in Section 1e",
            ))

        # ── Rule: Documentation requirements by type ──────────────────────────
        if income_type_val:
            type_lower = income_type_val.lower()
            doc_req = None
            for keyword, req in _DOC_REQUIREMENTS.items():
                if keyword in type_lower:
                    doc_req = req
                    break
            if doc_req:
                flags.append(_flag("4.2",
                    f"Documentation Required — {income_type_val}",
                    "info",
                    f"Income type '{income_type_val}' requires supporting documentation.",
                    f"Obtain: {doc_req}",
                ))

    # ── Build result ──────────────────────────────────────────────────────────
    dna_summary = []
    if borr_dna_checked:
        dna_summary.append("borrower (URLA.X40)")
    if coborr_dna_checked:
        dna_summary.append("co-borrower (URLA.X41)")

    result = {
        "success": True,
        "substep": "4.2",
        "tool": "review_urla_other_income",
        "has_other_income": has_other_income,
        "does_not_apply": dna_summary if dna_summary else None,
        "flags_count": len(flags),
        "message": (
            "Other Income (1e) completed"
            + (f" — Does Not Apply: {', '.join(dna_summary)}" if dna_summary else "")
            + (f" — {len(flags)} flag(s)" if flags else "")
        ),
    }

    logger.info(f"[REVIEW_URLA_OTHER_INCOME] {result['message']}")

    update = {
        "messages": [ToolMessage(content=json.dumps(result), tool_call_id=tool_call_id)],
    }
    if flags:
        update["flags"] = flags

    return Command(update=update)
