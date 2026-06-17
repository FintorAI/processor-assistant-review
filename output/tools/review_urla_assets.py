"""review_urla_assets — Tool for substep 6.1: Assets and VOD (2a)

Step 6 (STEP_06): 1003 URLA Part 3
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

from ._helpers import _los, _doc, _doc_all, _profile, _relevant_docs

logger = logging.getLogger(__name__)

# Keywords that indicate a Zelle / ZEL-type transfer in deposit descriptions
_ZELLE_KEYWORDS = ("zel", "zelle", "klarna", "firm")


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _parse_float(val) -> Optional[float]:
    """Return float from a value, or None if unparseable."""
    if val is None:
        return None
    try:
        return float(str(val).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _last_four(acct_num: Optional[str]) -> Optional[str]:
    """Return last 4 digits of account number, stripping masks."""
    if not acct_num:
        return None
    digits = "".join(c for c in str(acct_num) if c.isdigit())
    return digits[-4:] if len(digits) >= 4 else digits or None


def _parse_date(val) -> Optional[datetime]:
    """Try several common date formats, return datetime or None."""
    if not val:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(str(val).strip(), fmt)
        except ValueError:
            pass
    return None


def _days_old(date_val) -> Optional[int]:
    """Days since the given date string, or None."""
    dt = _parse_date(date_val)
    if not dt:
        return None
    return (datetime.now() - dt).days


def _flag(substep: str, title: str, severity: str, details: str, suggestion: str, docs=None) -> dict:
    f = {
        "substep": substep,
        "title": title,
        "severity": severity,
        "details": details,
        "suggestion": suggestion,
        "resolved": False,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if docs:
        f["relevant_documents"] = docs
    return f


# ──────────────────────────────────────────────────────────────────────────────
# VOD comparison helpers
# ──────────────────────────────────────────────────────────────────────────────

def _compare_with_vod(
    doc_copies: list,            # normalised bank-statement or asset copies
    vod_rows: list,              # from state['vod_data']
    doc_type_label: str,         # e.g. "Bank Statement" for flag titles
    substep: str,
) -> list:
    """Cross-reference extracted document account rows against VOD entries.

    Matching strategy (tolerant):
      1. Try to match on last-4 of account number.
      2. If no account number, match by institution name (case-insensitive).

    Discrepancy criteria:
      - Balance difference > $1 (rounding tolerance).
      - Account type mismatch (if both present and clearly different).

    Returns a list of flag dicts.
    """
    flags = []

    if not vod_rows:
        return flags  # VOD not loaded — skip comparison

    for copy in doc_copies:
        # Extract fields from this copy dict
        if isinstance(copy, dict) and "fields" in copy:
            # Full copy object from state['efolder_documents']
            fields = copy.get("fields", {})
            doc_acct_raw   = fields.get("bank_account_number") or fields.get("asset_account_number")
            doc_balance    = _parse_float(fields.get("ending_balance") or fields.get("bank_balance") or fields.get("asset_balance"))
            doc_institution = (fields.get("institution_name") or "").lower().strip()
            doc_acct_type  = (fields.get("account_type") or "").lower().strip()
        else:
            # Simplified copy from _doc_all (value + metadata)
            doc_acct_raw   = copy.get("value")
            doc_balance    = None
            doc_institution = ""
            doc_acct_type  = ""

        doc_last4 = _last_four(doc_acct_raw)

        # Find the best matching VOD row
        matched_vod = None
        for row in vod_rows:
            vod_last4 = _last_four(row.get("account_number"))
            vod_inst  = (row.get("institution_name") or "").lower().strip()

            if doc_last4 and vod_last4 and doc_last4 == vod_last4:
                matched_vod = row
                break
            if not matched_vod and doc_institution and vod_inst and doc_institution in vod_inst:
                matched_vod = row  # institution-level match (weaker)

        if not matched_vod:
            if doc_last4 or doc_institution:
                flags.append(_flag(
                    substep,
                    f"{doc_type_label} — Account Not Found in VOD",
                    "warning",
                    f"Account {doc_acct_raw or doc_institution!r} from extracted {doc_type_label} "
                    f"has no matching VOD entry in Encompass.",
                    "Verify borrower account and add/update VOD in Encompass.",
                ))
            continue

        vod_balance = matched_vod.get("balance")
        vod_acct_type = (matched_vod.get("account_type") or "").lower()

        # Balance discrepancy check
        if doc_balance is not None and vod_balance is not None:
            diff = abs(doc_balance - vod_balance)
            if diff > 1.00:
                flags.append(_flag(
                    substep,
                    f"{doc_type_label} — Balance Discrepancy vs VOD",
                    "warning",
                    (
                        f"Account {doc_acct_raw or matched_vod.get('account_number')!r}: "
                        f"document shows ${doc_balance:,.2f}, "
                        f"VOD ({matched_vod['institution_name']}) shows ${vod_balance:,.2f}. "
                        f"Difference: ${diff:,.2f}."
                    ),
                    "Reconcile balance between bank statement and VOD. Update Encompass VOD if needed.",
                ))

        # Account type mismatch (only flag if both are meaningfully different)
        _TYPE_MAP = {
            "checkingaccount": "checking",
            "savingsaccount": "savings",
            "mutualfunds": "mutual funds",
            "moneymarket": "money market",
            "retirement": "retirement",
            "stockbonds": "stocks/bonds",
        }
        doc_type_norm = _TYPE_MAP.get(doc_acct_type.replace(" ", "").lower(), doc_acct_type)
        vod_type_norm = _TYPE_MAP.get(vod_acct_type.replace(" ", "").lower(), vod_acct_type)

        if doc_type_norm and vod_type_norm and doc_type_norm != vod_type_norm:
            flags.append(_flag(
                substep,
                f"{doc_type_label} — Account Type Mismatch vs VOD",
                "info",
                (
                    f"Account {doc_acct_raw!r}: document type is '{doc_type_norm}', "
                    f"VOD type is '{vod_type_norm}'."
                ),
                "Confirm account type with borrower and update VOD in Encompass if needed.",
            ))

    return flags


# ──────────────────────────────────────────────────────────────────────────────
# Main tool
# ──────────────────────────────────────────────────────────────────────────────

@tool
def review_urla_assets(
    tool_call_id: Annotated[str, InjectedToolCallId],
    state: Annotated[dict, InjectedState],
) -> Command:
    """Review Section 3a (Assets / VOD).

    Checks:
      1. Bank statement presence and recency (60 days Conv / 30 days FHA).
      2. ZEL / Zelle / Klarna / unusual deposits → borrower LOE required.
      3. Large / green deposits → sourcing required.
      4. Cross-reference extracted bank-statement balances + account numbers
         against Encompass VOD data (fetched by fetch_vod_data).
      5. Cross-reference asset doc balances against VOD data.
      6. VOD coverage — flags accounts in VOD with no matching doc.

    Reads LOS:  total_assets, checking_balance, savings_balance
    Reads Docs: Bank Statement (all copies), Assets (all copies)
    Reads State: vod_data (populated by fetch_vod_data)

    Call this tool during STEP_06 (1003 URLA Part 3) as substep 6.1.
    """
    loan_id = state.get("loan_id")
    if not loan_id:
        return Command(update={"messages": [ToolMessage(
            content=json.dumps({"error": "No loan_id in state. Run find_loan first."}),
            tool_call_id=tool_call_id,
        )]})

    logger.info(f"[REVIEW_URLA_ASSETS] Starting for loan {str(loan_id)[:8]}...")

    flags = []

    # ── 1. Loan profile ──────────────────────────────────────────────────────
    loan_type    = _profile(state, "loan_type", "Conventional")   # Conventional | FHA | VA | USDA
    is_fha       = loan_type.upper() == "FHA"
    max_days_old = 30 if is_fha else 60                            # statement recency window

    # ── 2. LOS field reads ───────────────────────────────────────────────────
    total_assets      = _parse_float(_los(state, "total_assets"))       # Field 732
    # checking_balance / savings_balance (733/734) reserved for future drill-down checks

    # ── 3. VOD data (from fetch_vod_data) ────────────────────────────────────
    vod_rows: list = state.get("vod_data") or []
    logger.info(f"[REVIEW_URLA_ASSETS] VOD rows in state: {len(vod_rows)}")

    # ── 4. Bank Statement copies ─────────────────────────────────────────────
    # Each copy dict from _doc_all has: {value, source_document, confidence, copy_index}
    bank_statement_copies = _doc_all(state, "bank_account_number")   # one per attachment
    bank_statement_dates  = _doc_all(state, "statement_period_end")

    # For richer per-copy data we also try direct efolder_documents access
    efolder_docs = state.get("efolder_documents", {})
    bank_stmt_full_copies = (
        efolder_docs.get("Bank Statement", {}).get("copies", [])
    )

    # 4a. Bank statement presence
    if not bank_stmt_full_copies and not bank_statement_copies:
        flags.append(_flag(
            "6.1",
            "Bank Statement Missing",
            "stop",
            "No bank statement documents found in eFolder.",
            f"Request {max_days_old}-day bank statement history from borrower.",
        ))
    else:
        # 4b. Recency check — check the most recent statement end date across copies.
        # Guardrail: only treat the check as "performed" when at least one copy yields a
        # readable end date. If a statement is present but every statement_period_end is
        # empty/unparseable, do NOT silently pass — flag recency as unverifiable.
        _recency_evaluated = False
        for copy in bank_statement_dates:
            end_date_val = copy.get("value")
            days = _days_old(end_date_val)
            if days is not None:
                _recency_evaluated = True
                if days > max_days_old:
                    flags.append(_flag(
                        "6.1",
                        "Bank Statement Stale",
                        "warning",
                        (
                            f"Statement ending {end_date_val} is {days} days old "
                            f"({loan_type} requires ≤{max_days_old} days). "
                            f"Source: {copy.get('source_document', 'unknown')}."
                        ),
                        f"Request updated bank statements covering the last {max_days_old} days.",
                        docs=_relevant_docs(
                            state,
                            doc_types=["Bank Statement"],
                            matched={"Bank Statement": copy.get("copy_index")},
                        ),
                    ))
                break  # only flag once for the primary copy

        if not _recency_evaluated:
            flags.append(_flag(
                "6.1",
                "Bank Statement Recency Unverifiable",
                "warning",
                (
                    "Bank statement(s) present in eFolder but no readable statement period "
                    "end date was extracted, so recency could not be verified "
                    f"({loan_type} requires statements ≤{max_days_old} days old)."
                ),
                "Manually verify the statement is recent enough, or re-run extraction "
                "(possible doc-type/schema name mismatch — see docs/EFOLDER_EXTRACTION.md).",
                docs=_relevant_docs(state, doc_types=["Bank Statement"]),
            ))

        # 4c. ZEL / Zelle / Klarna / unusual deposit keyword check
        # Kept inside the else block so these only fire when a Bank Statement is
        # actually present — prevents doc_fields contamination from other statement-
        # type documents populating bank_zel_deposits / bank_large_deposits.
        zelle_deposits = _doc(state, "bank_zel_deposits")
        if zelle_deposits:
            desc = str(zelle_deposits).lower()
            for kw in _ZELLE_KEYWORDS:
                if kw in desc:
                    flags.append(_flag(
                        "6.1",
                        "ZEL / Zelle Deposit Requires Explanation",
                        "warning",
                        f"Unusual deposit keyword '{kw}' detected in bank statement: {zelle_deposits!r}.",
                        "Request borrower Letter of Explanation (LOE) for ZEL/Zelle/Klarna transactions.",
                        docs=_relevant_docs(state, "bank_zel_deposits", doc_types=["Bank Statement"]),
                    ))
                    break

        # 4d. Large / green deposit check
        # The extractor already determined these are notable — flag unconditionally.
        large_deposits = _doc(state, "bank_large_deposits")
        if large_deposits:
            flags.append(_flag(
                "6.1",
                "Large / Green Deposit Requires Sourcing",
                "warning",
                f"Large or unusual deposit(s) flagged in bank statement: {large_deposits!r}.",
                "Request documentation sourcing the deposit (LOE + receipts if applicable).",
                docs=_relevant_docs(state, "bank_large_deposits", doc_types=["Bank Statement"]),
            ))

    # 4e. VOD cross-reference for bank statements
    if vod_rows:
        _bank_refs = _relevant_docs(state, doc_types=["Bank Statement"])
        _bs_vod_flags = []
        if bank_stmt_full_copies:
            _bs_vod_flags = _compare_with_vod(
                bank_stmt_full_copies, vod_rows, "Bank Statement", "6.1"
            )
        elif bank_statement_copies:
            _bs_vod_flags = _compare_with_vod(
                bank_statement_copies, vod_rows, "Bank Statement", "6.1"
            )
        if _bank_refs:
            for _f in _bs_vod_flags:
                _f["relevant_documents"] = _bank_refs
        flags += _bs_vod_flags

    # ── 5. Asset copies ──────────────────────────────────────────────────────
    asset_full_copies = efolder_docs.get("Assets", {}).get("copies", [])
    asset_acct_copies = _doc_all(state, "asset_account_number")

    if vod_rows:
        _asset_refs = _relevant_docs(state, doc_types=["Assets"])
        _asset_vod_flags = []
        if asset_full_copies:
            _asset_vod_flags = _compare_with_vod(asset_full_copies, vod_rows, "Assets", "6.1")
        elif asset_acct_copies:
            _asset_vod_flags = _compare_with_vod(asset_acct_copies, vod_rows, "Assets", "6.1")
        if _asset_refs:
            for _f in _asset_vod_flags:
                _f["relevant_documents"] = _asset_refs
        flags += _asset_vod_flags

    # ── 6. VOD coverage — accounts in VOD with no matching doc ───────────────
    if vod_rows:
        matched_vod_ids = set()

        def _check_matches(copies, field_key="bank_account_number"):
            for row in vod_rows:
                vod_last4 = _last_four(row.get("account_number"))
                vod_inst  = (row.get("institution_name") or "").lower()
                for copy in copies:
                    if isinstance(copy, dict) and "fields" in copy:
                        doc_num = _last_four(copy.get("fields", {}).get(field_key))
                        doc_inst = (copy.get("fields", {}).get("institution_name") or "").lower()
                    else:
                        doc_num = _last_four(copy.get("value"))
                        doc_inst = ""
                    if (vod_last4 and doc_num and vod_last4 == doc_num) or (vod_inst and doc_inst and doc_inst in vod_inst):
                        matched_vod_ids.add(row["vod_id"] + "_" + (row.get("account_number") or ""))

        _check_matches(bank_stmt_full_copies or bank_statement_copies, "bank_account_number")
        _check_matches(asset_full_copies or asset_acct_copies, "asset_account_number")

        for row in vod_rows:
            key = row["vod_id"] + "_" + (row.get("account_number") or "")
            if key not in matched_vod_ids:
                flags.append(_flag(
                    "6.1",
                    "VOD Account Has No Supporting Document",
                    "warning",
                    (
                        f"VOD entry for {row['institution_name']!r} "
                        f"({row.get('account_type', '')} {row.get('account_number', '')}) "
                        f"balance ${row['balance']:,.2f} has no matching bank statement or asset doc."
                    ),
                    "Ensure all VOD accounts have corresponding bank statements or asset docs in eFolder.",
                ))

    # ── 7. LOS total_assets sanity (informational) ───────────────────────────
    if vod_rows and total_assets is not None:
        vod_total = sum(r["balance"] for r in vod_rows)
        diff = abs(total_assets - vod_total)
        if diff > 5.00:
            flags.append(_flag(
                "6.1",
                "Total Assets Mismatch: LOS vs VOD",
                "info",
                (
                    f"LOS total_assets = ${total_assets:,.2f}, "
                    f"sum of VOD balances = ${vod_total:,.2f} "
                    f"(difference ${diff:,.2f})."
                ),
                "Verify all accounts are entered in Encompass and VOD is complete.",
            ))

    # ── 8. FHA modifier note ─────────────────────────────────────────────────
    if is_fha:
        logger.info("[REVIEW_URLA_ASSETS] FHA loan: 1-month bank statement window applies.")

    # ── Build result ──────────────────────────────────────────────────────────
    result = {
        "success": True,
        "substep": "6.1",
        "tool": "review_urla_assets",
        "flags_count": len(flags),
        "vod_rows_checked": len(vod_rows),
        "bank_stmt_copies": len(bank_stmt_full_copies or bank_statement_copies),
        "asset_copies": len(asset_full_copies or asset_acct_copies),
        "message": "Assets and VOD (2a) review completed" + (f" with {len(flags)} flag(s)" if flags else " — no issues"),
    }

    logger.info(f"[REVIEW_URLA_ASSETS] {result['message']}")

    update: dict = {
        "messages": [ToolMessage(content=json.dumps(result), tool_call_id=tool_call_id)],
    }
    if flags:
        update["flags"] = flags

    return Command(update=update)
