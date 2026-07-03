"""review_urla_other_income — Tool for substep 5.2: Other Income (1e)

Step 5 (STEP_05): 1003 URLA Page 2
Phase: DATA_REVIEW

# FACTORY-LOCK: true
"""

import json
import logging
import re
from datetime import datetime, timezone
from typing import Annotated

from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.prebuilt import InjectedState
from langgraph.types import Command

from ._helpers import _los, _doc_all, _efolder_present, _relevant_docs

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

# Other-income categories that plausibly land as recurring NON-payroll deposits
# on a checking/savings statement. Dividend/interest (brokerage) and rental
# (lease/Schedule E) are documented elsewhere, so we do not attempt a
# bank-statement receipt match for them.
_BANK_EVIDENCED_TYPES = {
    "social security", "pension", "retirement", "annuity",
    "disability", "va benefits", "veterans", "child support", "alimony",
}

# Keyword hints that tie a recurring-deposit description to a stated income type.
_TYPE_KEYWORDS = {
    "social security": ("social security", "ssa", "ssi", "treas 310"),
    "pension":         ("pension", "retirement system", "calpers", "pers"),
    "retirement":      ("retirement", "annuity", "ira", "401k", "403b", "pension"),
    "annuity":         ("annuity",),
    "disability":      ("disability", "ssdi", "va comp"),
    "va benefits":     ("va ", "veteran", "va benefits", "va comp"),
    "child support":   ("child support", "child sup", "csa", "dcss"),
    "alimony":         ("alimony", "spousal", "maintenance"),
}


def _money(val) -> float:
    """Parse a currency-ish string/number to float; 0.0 if unparseable."""
    if val in (None, ""):
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)
    cleaned = re.sub(r"[^0-9.\-]", "", str(val))
    try:
        return float(cleaned) if cleaned not in ("", "-", ".") else 0.0
    except ValueError:
        return 0.0


def _copy_field(copy: dict, *keys):
    """Read a field value from an eFolder copy dict, tolerant of both shapes."""
    fields = copy.get("extracted_fields") or copy.get("fields") or {}
    for k in keys:
        if k in fields:
            v = fields[k]
            if isinstance(v, dict):
                v = v.get("value")
            if v not in (None, ""):
                return v
    return None


def _coerce_deposit_list(val):
    """Normalize a recurring_deposits value into a list of dicts.

    The extractor returns an array of objects, but tolerate a JSON string.
    """
    if isinstance(val, list):
        return [d for d in val if isinstance(d, dict)]
    if isinstance(val, str) and val.strip().startswith("["):
        try:
            parsed = json.loads(val)
            return [d for d in parsed if isinstance(d, dict)] if isinstance(parsed, list) else []
        except (ValueError, TypeError):
            return []
    return []


def _collect_recurring_deposits(state: dict) -> list:
    """Gather recurring_deposits objects across every Bank Statement copy."""
    deposits: list = []
    seen: set = set()
    efolder_docs = state.get("efolder_documents", {}) or {}
    bank_entry = efolder_docs.get("Bank Statement", {}) or {}
    copies = bank_entry.get("copies") or []
    if not copies:
        # Fall back to the flat doc_fields projection when per-copy data is absent.
        for c in _doc_all(state, "recurring_deposits"):
            deposits.extend(_coerce_deposit_list(c.get("value")))
    else:
        for copy in copies:
            deposits.extend(_coerce_deposit_list(_copy_field(copy, "recurring_deposits")))
    # De-duplicate identical source+amount+date rows that repeat across copies.
    unique = []
    for d in deposits:
        sig = (
            str(d.get("source", "")).strip().lower(),
            str(d.get("income_type", "")).strip().lower(),
            _money(d.get("amount")),
            str(d.get("date", "")).strip(),
        )
        if sig not in seen:
            seen.add(sig)
            unique.append(d)
    return unique


def _deposit_matches_type(deposit: dict, income_type_lower: str, keywords: tuple) -> bool:
    """True if a recurring deposit corresponds to the stated other-income type."""
    dep_type = str(deposit.get("income_type", "")).strip().lower()
    dep_src = str(deposit.get("source", "")).strip().lower()
    if dep_type and (dep_type in income_type_lower or income_type_lower in dep_type):
        return True
    hay = f"{dep_type} {dep_src}"
    return any(kw and kw in hay for kw in keywords)


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

    Additionally cross-checks receipt of the stated other income against the
    bank statements (checklist 06 #3): for income types that appear as recurring
    non-payroll deposits (Social Security, pension, retirement, disability,
    child support, alimony, VA benefits), it matches the extracted
    ``recurring_deposits[]`` on the Bank Statement(s) to the stated type,
    confirms receipt, and reconciles a monthly deposit amount against Field 173.
    This only runs when bank statements are in the file.

    Call this tool during STEP_05 (1003 URLA Page 2) as substep 5.2.
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
            flags.append(_flag("5.2",
                "Other Income Section Incomplete (2e)",
                "info",
                "Section 2e (other income, 1003 URLA Part 2) has no entries and neither URLA.X40 (borrower) nor URLA.X41 (co-borrower) Does Not Apply is checked.",
                "Confirm with borrower whether other income exists. If not applicable, check URLA.X40 and/or URLA.X41.",
            ))
    else:
        # Income is present — check for type/amount completeness
        if income_type_val and not income_amount_val:
            flags.append(_flag("5.2",
                "Other Income Amount Missing",
                "warning",
                f"Income type '{income_type_val}' is entered but monthly amount (Field 173) is empty.",
                "Enter the monthly other income amount in Section 2e",
            ))
        elif income_amount_val and not income_type_val:
            flags.append(_flag("5.2",
                "Other Income Type Missing",
                "warning",
                f"Other income amount ${income_amount_val} is entered but income type (Field 172) is empty.",
                "Enter the other income type in Section 2e",
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
                flags.append(_flag("5.2",
                    f"Documentation Required — {income_type_val}",
                    "info",
                    f"Income type '{income_type_val}' requires supporting documentation.",
                    f"Obtain: {doc_req}",
                ))

        # ── Rule: Receipt of other income on bank statements (06 #3) ──────────
        # When the stated other-income type is one that shows up as a recurring
        # non-payroll bank deposit (SS/pension/retirement/disability/child
        # support/alimony), cross-check the extracted recurring_deposits[] on the
        # Bank Statement(s). Only runs when a type is stated AND bank statements
        # are in the file — absence of bank statements is not a finding here
        # (that income is often documented via award letter instead).
        if income_type_val and _efolder_present(state, "Bank Statement"):
            type_lower = income_type_val.lower()
            if any(t in type_lower for t in _BANK_EVIDENCED_TYPES):
                keywords: tuple = ()
                for t, kws in _TYPE_KEYWORDS.items():
                    if t in type_lower:
                        keywords = kws
                        break
                deposits = _collect_recurring_deposits(state)
                matches = [d for d in deposits if _deposit_matches_type(d, type_lower, keywords)]
                bank_docs = _relevant_docs(state, doc_types=["Bank Statement"])
                if matches:
                    stated_monthly = _money(income_amount_val)
                    lines = []
                    amount_mismatch = None
                    for d in matches:
                        amt = _money(d.get("amount"))
                        freq = str(d.get("frequency", "")).strip().lower()
                        lines.append(
                            f"- {d.get('source') or d.get('income_type') or 'recurring deposit'}: "
                            f"${amt:,.2f}"
                            + (f" ({freq})" if freq else "")
                            + (f" on {d.get('date')}" if d.get("date") else "")
                        )
                        if (stated_monthly and amt and freq == "monthly"
                                and abs(amt - stated_monthly) / stated_monthly > 0.10):
                            amount_mismatch = (amt, stated_monthly)
                    details = (
                        f"Receipt of '{income_type_val}' income is evidenced by recurring "
                        f"deposit(s) on the bank statement(s):\n" + "\n".join(lines)
                    )
                    if amount_mismatch:
                        details += (
                            f"\n\nAmount check: a monthly deposit of ${amount_mismatch[0]:,.2f} "
                            f"differs from the stated ${amount_mismatch[1]:,.2f}/mo (Field 173) "
                            f"by more than 10%."
                        )
                    flags.append({
                        "substep": "5.2",
                        "title": f"Other Income Receipt Confirmed — {income_type_val}",
                        "severity": "warning" if amount_mismatch else "info",
                        "details": details,
                        "suggestion": (
                            "Reconcile the deposit amount/frequency against the stated other income."
                            if amount_mismatch else
                            "Receipt confirmed on bank statement — no action needed."
                        ),
                        "resolved": False,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "relevant_documents": bank_docs,
                    })
                else:
                    other = [
                        f"{(d.get('source') or d.get('income_type') or 'deposit')} "
                        f"(${_money(d.get('amount')):,.2f})"
                        for d in deposits
                    ]
                    detail = (
                        f"Stated '{income_type_val}' income (Field 172) was not matched to a "
                        f"recurring non-payroll deposit on the bank statement(s). "
                    )
                    detail += (
                        f"Other recurring deposits detected: {', '.join(other)}."
                        if other else
                        "No recurring non-payroll deposits were detected on the statement(s)."
                    )
                    flags.append({
                        "substep": "5.2",
                        "title": f"Other Income Receipt Not Evidenced — {income_type_val}",
                        "severity": "info",
                        "details": detail,
                        "suggestion": (
                            "Confirm receipt of this income — it may be deposited to an account "
                            "not in the file, or verified via award letter / benefit statement."
                        ),
                        "resolved": False,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "relevant_documents": bank_docs,
                    })

    # ── Build result ──────────────────────────────────────────────────────────
    dna_summary = []
    if borr_dna_checked:
        dna_summary.append("borrower (URLA.X40)")
    if coborr_dna_checked:
        dna_summary.append("co-borrower (URLA.X41)")

    result = {
        "success": True,
        "substep": "5.2",
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
