"""review_urla_assets — Tool for substep 6.1: Assets and VOD (2a)

Step 6 (STEP_06): 1003 URLA Part 3
Phase: DATA_REVIEW

# FACTORY-LOCK: true
"""

import json
import logging
import re
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
    """Return float from a value, or None if unparseable.

    Tolerant of currency formatting (``$``, thousands commas, stray text like
    "CR"), so extracted balances such as "$4,279.51" parse correctly.
    """
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    cleaned = re.sub(r"[^0-9.\-]", "", str(val))
    if cleaned in ("", "-", ".", "-.", "-"):
        return None
    try:
        return float(cleaned)
    except ValueError:
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

_TYPE_MAP = {
    "checkingaccount": "checking",
    "savingsaccount": "savings",
    "mutualfunds": "mutual funds",
    "mutualfund": "mutual funds",
    "moneymarket": "money market",
    "moneymarketfund": "money market",
    "retirement": "retirement",
    "retirementfund": "retirement",
    "stockbonds": "stocks/bonds",
    "stock": "stocks/bonds",
}


def _copy_field(copy: dict, *keys):
    """Read a field value from an eFolder copy dict, tolerant of both shapes.

    eFolder copies store extracted values under ``extracted_fields`` (the live
    shape) or ``fields`` (older/simplified shape), and each value may be a flat
    scalar or a nested ``{"value": ..., "confidence": ...}`` dict. Returns the
    first non-empty value across ``keys``.
    """
    fields = copy.get("extracted_fields") or copy.get("fields") or {}
    for k in keys:
        if k in fields:
            val = fields[k]
            if isinstance(val, dict):
                val = val.get("value")
            if val not in (None, ""):
                return val
    return None


def _copy_has_fields(copy) -> bool:
    return isinstance(copy, dict) and ("extracted_fields" in copy or "fields" in copy)


def _norm_type(val: str) -> str:
    raw = (val or "").lower().strip()
    if not raw:
        return ""
    mapped = _TYPE_MAP.get(raw.replace(" ", ""))
    if mapped:
        return mapped
    # Fall back to keyword detection for bank-specific labels
    # (e.g. "Woodforest Checking", "Premium Savings").
    for kw, norm in (
        ("checking", "checking"),
        ("savings", "savings"),
        ("money market", "money market"),
        ("retirement", "retirement"),
        ("mutual", "mutual funds"),
    ):
        if kw in raw:
            return norm
    return raw


def _truthy(v) -> bool:
    """Loose boolean parse for extracted indicator fields."""
    if isinstance(v, bool):
        return v
    return str(v or "").strip().lower() in ("true", "yes", "y", "1", "x", "checked")


def _retirement_records(copies: list, assume_retirement: bool) -> list:
    """Build normalized retirement records from full eFolder copy dicts.

    ``assume_retirement`` — when True (dedicated "Retirement Account Statement"
    bucket) every copy is treated as a retirement account; when False (generic
    "Assets" bucket) only copies flagged ``is_retirement_account`` or whose
    account_type normalizes to "retirement" are kept.
    """
    recs: list = []
    for copy in copies or []:
        if not _copy_has_fields(copy):
            continue
        atype = (_copy_field(copy, "account_type") or "")
        is_ret = _truthy(_copy_field(copy, "is_retirement_account"))
        if not assume_retirement and not (is_ret or _norm_type(atype) == "retirement"):
            continue
        acct_raw = _copy_field(copy, "account_number", "account_number_last4", "asset_account_number")
        recs.append({
            "vested":          _parse_float(_copy_field(copy, "vested_balance", "total_value", "ending_balance")),
            "outstanding":     _parse_float(_copy_field(copy, "outstanding_loan_balance")),
            "terms_present":   _truthy(_copy_field(copy, "terms_of_withdrawal_present")),
            "institution_raw": (_copy_field(copy, "institution_name") or "").strip(),
            "institution":     (_copy_field(copy, "institution_name") or "").strip().lower(),
            "last4":           _last_four(acct_raw),
            "account_type":    atype,
        })
    return recs


def _match_retirement_vod(rec: dict, vod_rows: list) -> Optional[dict]:
    """Find the 2a/VOD row that corresponds to a retirement statement record."""
    ret_rows = [r for r in vod_rows if _norm_type(r.get("account_type")) == "retirement"]
    last4 = rec.get("last4")
    if last4:
        for r in ret_rows:
            if _last_four(r.get("account_number")) == last4:
                return r
        for r in vod_rows:  # account number wins even if type label is off
            if _last_four(r.get("account_number")) == last4:
                return r
    inst = rec.get("institution")
    if inst:
        for r in ret_rows:
            if inst in (r.get("institution_name") or "").lower():
                return r
    if len(ret_rows) == 1:
        return ret_rows[0]
    return None


def _compare_with_vod(
    doc_copies: list,            # normalised bank-statement or asset copies
    vod_rows: list,              # from state['vod_data']
    doc_type_label: str,         # e.g. "Bank Statement" for flag titles
    substep: str,
    allow_populate: bool = False,
) -> tuple:
    """Cross-reference extracted document account rows against VOD (2a) entries.

    Matching strategy (tolerant):
      1. Try to match on last-4 of account number.
      2. If no account number, match by institution name (case-insensitive).

    Per the desired behaviour:
      - **Match** (account found, balances agree within $1) → an ``info`` flag
        explicitly confirming the bank statement matches the 2a/VOD entry.
      - **Value mismatch** (balance differs by > $1) → a ``warning`` flag; the
        VOD is *not* modified (processor reconciles manually).
      - **Missing from VOD** (no matching entry) → if ``allow_populate`` is set,
        the account is queued for creation in the 2a/VOD (returned in the second
        element); otherwise a ``warning`` flag is raised.

    Returns a tuple ``(flags, accounts_to_add, entries_to_complete)`` where
    ``accounts_to_add`` is a list of normalised account dicts the caller should
    create via ``add_vods`` (entirely-missing accounts), and
    ``entries_to_complete`` is a list of ``{vod_id, account_number, label,
    updates}`` dicts the caller should pass to ``update_vods`` to fill BLANK
    subfields on an existing matched entry (checklist 08 #10). Populated VOD
    values are never queued for overwrite.
    """
    flags: list = []
    to_add: list = []
    to_complete: list = []

    if not vod_rows:
        return flags, to_add, to_complete  # VOD not loaded — skip comparison

    for copy in doc_copies:
        # Extract fields from this copy dict
        if _copy_has_fields(copy):
            # Full copy object from state['efolder_documents'] (extracted_fields
            # with nested {"value": ...} entries, or older flat "fields").
            doc_acct_raw   = _copy_field(copy, "bank_account_number", "asset_account_number", "account_number")
            doc_balance    = _parse_float(_copy_field(copy, "ending_balance", "bank_balance", "asset_balance"))
            doc_institution_raw = (_copy_field(copy, "institution_name") or "").strip()
            doc_institution = doc_institution_raw.lower()
            doc_acct_type  = (_copy_field(copy, "account_type") or "").strip()
            doc_holder     = (_copy_field(copy, "account_holder_name", "borrower_name") or "").strip()
        else:
            # Simplified copy from _doc_all (value + metadata)
            doc_acct_raw   = copy.get("value")
            doc_balance    = None
            doc_institution_raw = ""
            doc_institution = ""
            doc_acct_type  = ""
            doc_holder     = ""

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

        # ── Missing from VOD ────────────────────────────────────────────────
        if not matched_vod:
            if not (doc_last4 or doc_institution):
                continue
            if allow_populate:
                to_add.append({
                    "institution_name": doc_institution_raw,
                    "account_type":     doc_acct_type,
                    "account_number":   str(doc_acct_raw) if doc_acct_raw else "",
                    "account_holder":   doc_holder,
                    "balance":          doc_balance,
                })
            else:
                _tag = doc_institution_raw or (str(doc_acct_raw) if doc_acct_raw else "account")
                flags.append(_flag(
                    substep,
                    f"{doc_type_label} — Account Not Found in 2a/VOD ({_tag})",
                    "warning",
                    f"Account {doc_acct_raw or doc_institution_raw!r} from extracted {doc_type_label} "
                    f"has no matching VOD entry in Encompass.",
                    "Verify borrower account and add/update VOD in Encompass.",
                ))
            continue

        # ── Matched ─────────────────────────────────────────────────────────
        vod_balance = matched_vod.get("balance")
        vod_inst_name = matched_vod.get("institution_name") or doc_institution_raw
        acct_label = doc_acct_raw or matched_vod.get("account_number") or vod_inst_name
        # read_vods coerces a missing cash/market value to 0.0, so a 0/None
        # balance is treated as an unset (blank) field for completion purposes.
        vod_bal_blank = vod_balance in (None, 0, 0.0)

        # ── Completion (08 #10): queue BLANK subfields to fill on this entry ──
        completion_updates: dict = {}
        if doc_acct_type and not (matched_vod.get("account_type") or "").strip():
            completion_updates["account_type"] = doc_acct_type
        if doc_balance is not None and doc_balance > 0 and vod_bal_blank:
            completion_updates["balance"] = doc_balance
        if doc_acct_raw and not (matched_vod.get("account_number") or "").strip():
            completion_updates["account_number"] = str(doc_acct_raw)
        if doc_holder and not (matched_vod.get("account_holder") or "").strip():
            completion_updates["account_holder"] = doc_holder
        if completion_updates and matched_vod.get("vod_id"):
            to_complete.append({
                "vod_id": matched_vod.get("vod_id"),
                "account_number": matched_vod.get("account_number") or (str(doc_acct_raw) if doc_acct_raw else ""),
                "label": vod_inst_name,
                "updates": completion_updates,
            })

        # ── Balance comparison — only when the VOD already carries a balance ──
        if doc_balance is not None and not vod_bal_blank:
            diff = abs(doc_balance - vod_balance)
            if diff > 1.00:
                # Value mismatch → warn only, never overwrite the VOD.
                flags.append(_flag(
                    substep,
                    f"{doc_type_label} — Balance Discrepancy vs 2a/VOD ({vod_inst_name})",
                    "warning",
                    (
                        f"Account {acct_label!r}: {doc_type_label.lower()} shows "
                        f"${doc_balance:,.2f}, 2a/VOD ({vod_inst_name}) shows "
                        f"${vod_balance:,.2f}. Difference: ${diff:,.2f}."
                    ),
                    "Reconcile balance between bank statement and 2a/VOD. "
                    "Update Encompass manually if the statement is correct.",
                ))
            else:
                # Match → confirm explicitly as info.
                flags.append(_flag(
                    substep,
                    f"{doc_type_label} — Matches 2a/VOD ({vod_inst_name})",
                    "info",
                    (
                        f"Account {acct_label!r} ({vod_inst_name}): "
                        f"{doc_type_label.lower()} balance ${doc_balance:,.2f} matches "
                        f"2a/VOD balance ${vod_balance:,.2f} (within $1.00)."
                    ),
                    "",
                ))
        elif not vod_bal_blank or "balance" not in completion_updates:
            # VOD balance present but doc balance missing, OR VOD balance blank
            # with nothing to complete — confirm presence (a queued balance
            # completion is reported by the caller's info-overwrite flag instead).
            flags.append(_flag(
                substep,
                f"{doc_type_label} — Found in 2a/VOD ({vod_inst_name})",
                "info",
                (
                    f"Account {acct_label!r} ({vod_inst_name}) is present in the 2a/VOD, "
                    f"but the {doc_type_label.lower()} balance could not be extracted to "
                    f"confirm the amount."
                ),
                "Manually confirm the balance matches the 2a/VOD entry.",
            ))

        # Account type mismatch (only flag if both are meaningfully different)
        doc_type_norm = _norm_type(doc_acct_type)
        vod_type_norm = _norm_type(matched_vod.get("account_type"))

        if doc_type_norm and vod_type_norm and doc_type_norm != vod_type_norm:
            flags.append(_flag(
                substep,
                f"{doc_type_label} — Account Type Mismatch vs 2a/VOD ({vod_inst_name})",
                "info",
                (
                    f"Account {acct_label!r}: document type is '{doc_type_norm}', "
                    f"2a/VOD type is '{vod_type_norm}'."
                ),
                "Confirm account type with borrower and update VOD in Encompass if needed.",
            ))

    return flags, to_add, to_complete


_COMPLETION_FIELD_LABELS = {
    "account_type": "account type",
    "balance": "cash/market value",
    "account_number": "account number",
    "account_holder": "account holder",
}


def _completion_field_names(updates: dict) -> list:
    return [_COMPLETION_FIELD_LABELS.get(k, k) for k in updates]


def _apply_vod_completions(
    loan_id: str,
    to_complete: list,
    refs: list,
    source_label: str,
    state: dict,
    flags: list,
    dry_run: bool = False,
) -> None:
    """Fill blank subfields on existing 2a/VOD entries and emit audit flags.

    Only blank fields are written (detection already filtered populated values),
    and one ``info-overwrite`` flag is emitted per completed entry. A write
    failure or an unsupported (legacy-schema) entry produces a single readable
    flag so the processor can finish it manually. Honors ``dry_run``.
    """
    if not to_complete:
        return

    if dry_run:
        for c in to_complete:
            flags.append(_flag(
                "6.1",
                f"2a/VOD Entry Completion (dry-run) ({c.get('label')})",
                "info",
                (
                    f"[DRY-RUN] Would complete "
                    f"{', '.join(_completion_field_names(c.get('updates', {})))} on the existing "
                    f"2a/VOD entry for {c.get('label')} from the {source_label.lower()}."
                ),
                "",
                docs=refs,
            ))
        return

    from shared.encompass_io import update_vods
    res = update_vods(loan_id, to_complete, state=state)
    updated_by_id = {u.get("vod_id"): u.get("fields", []) for u in res.get("updated", [])}
    skipped_by_id = {s.get("vod_id"): s.get("reason", "") for s in res.get("skipped", [])}

    for c in to_complete:
        vid = c.get("vod_id")
        label = c.get("label")
        if vid in updated_by_id:
            flags.append(_flag(
                "6.1",
                f"2a/VOD Entry Completed ({label})",
                "info-overwrite",
                (
                    f"Filled blank field(s) on the existing 2a/VOD entry for {label} from the "
                    f"{source_label.lower()}: {', '.join(updated_by_id[vid])}."
                ),
                "Verify the completed 2a/VOD entry in Encompass.",
                docs=refs,
            ))
        elif not res.get("success"):
            flags.append(_flag(
                "6.1",
                f"2a/VOD Completion Failed ({label})",
                "warning",
                (
                    f"Could not complete blank field(s) on the 2a/VOD entry for {label}: "
                    f"{res.get('error')}"
                ),
                "Complete the 2a/VOD entry manually in Encompass.",
                docs=refs,
            ))
        elif vid in skipped_by_id:
            flags.append(_flag(
                "6.1",
                f"2a/VOD Entry Not Auto-Completed ({label})",
                "info",
                (
                    f"Blank field(s) on the 2a/VOD entry for {label} were not auto-completed: "
                    f"{skipped_by_id[vid]}."
                ),
                "Complete the 2a/VOD entry manually in Encompass.",
                docs=refs,
            ))


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
      1. Bank statement presence and recency (60 days Conv / 30 days FHA), plus
         coverage continuity (no missing month between consecutive statements).
      2. ZEL / Zelle / Klarna / unusual deposits → borrower LOE required.
      3. Large / green deposits → sourcing required.
      4. Cross-reference extracted bank-statement balances + account numbers
         against Encompass 2a/VOD data (fetched by fetch_vod_data):
           • match (balance within $1)  → info flag confirming the match
           • value mismatch (> $1)      → warning (VOD left untouched)
           • account missing from VOD   → populated into the 2a/VOD via add_vods
           • existing entry incomplete  → BLANK subfields (account type, cash/
             market value, account number, holder) are filled in on the entry
             via update_vods (08 #10); populated values are never overwritten.
      5. Cross-reference asset doc balances against VOD data; existing entries
         with blank subfields are completed the same way (warn-only otherwise).
      5b. FHA retirement 60% haircut: for FHA loans, compares the Encompass
          2a/VOD retirement amount against vested × 0.6 (net of any 401(k)
          loan). Match → info; mismatch → warning. When terms of withdrawal
          are documented, 100% of the net vested balance is accepted as a match.
      6. VOD coverage — flags accounts in VOD with no matching doc.

    Reads LOS:  total_assets, checking_balance, savings_balance
    Reads Docs: Bank Statement (all copies), Assets (all copies),
                Retirement Account Statement (vested_balance, total_value,
                outstanding_loan_balance, terms_of_withdrawal_present, ...)
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

    # ── 3. VOD data (from fetch_vod_data, with a self-contained fallback) ─────
    # Prefer state['vod_data'] when fetch_vod_data has run (the key is present
    # even if it found zero rows). If the key is absent — fetch_vod_data was not
    # wired into this run — read the 2a/VOD directly so the cross-check still
    # executes instead of silently no-op'ing.
    if "vod_data" in state:
        vod_rows: list = state.get("vod_data") or []
    else:
        try:
            from shared.encompass_io import read_vods
            vod_rows = read_vods(loan_id, state=state)
            logger.info(f"[REVIEW_URLA_ASSETS] vod_data absent — read {len(vod_rows)} VOD row(s) directly")
        except Exception as exc:
            logger.warning(f"[REVIEW_URLA_ASSETS] direct VOD read failed: {exc}")
            vod_rows = []
    logger.info(f"[REVIEW_URLA_ASSETS] VOD rows available: {len(vod_rows)}")

    # Dry-run gate for 2a/VOD entry completion writes (checklist 08 #10).
    _vod_dry_run = False
    try:
        from output.registry import DEV_MODE
        _vod_dry_run = getattr(DEV_MODE, "dry_run", False)
    except Exception:
        _vod_dry_run = False

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

        # 4b-ii. Coverage continuity — flag gaps BETWEEN consecutive statements.
        # Recency (4b) confirms the newest statement is current; this confirms the
        # transaction history is continuous to current with no missing month
        # (checklist 08 #2 "transaction history to current"). Only runs when ≥2
        # statements expose a parseable start+end period.
        _periods = []
        for copy in bank_stmt_full_copies:
            s = _parse_date(_copy_field(copy, "statement_period_start"))
            e = _parse_date(_copy_field(copy, "statement_period_end"))
            if s and e:
                _periods.append((s, e))
        if len(_periods) >= 2:
            _periods.sort(key=lambda p: p[0])
            for (prev_s, prev_e), (next_s, next_e) in zip(_periods, _periods[1:]):
                # Gap = days between the end of one statement and the start of the
                # next. Allow ~5 days slack for month-boundary/statement-cut drift;
                # a gap beyond ~1 month means a statement is missing.
                gap_days = (next_s - prev_e).days
                if gap_days > 35:
                    flags.append(_flag(
                        "6.1",
                        "Bank Statement Gap in Coverage",
                        "warning",
                        (
                            f"Missing coverage of ~{gap_days} days between statement ending "
                            f"{prev_e.isoformat()} and the next statement starting "
                            f"{next_s.isoformat()}. Transaction history must be continuous "
                            f"to current."
                        ),
                        "Request the missing bank statement(s) to close the gap in coverage.",
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

    # 4e. VOD (2a) cross-reference for bank statements
    #     match → info-confirm; value mismatch → warning; missing → populate 2a/VOD.
    if vod_rows:
        _bank_refs = _relevant_docs(state, doc_types=["Bank Statement"])
        _bs_vod_flags: list = []
        _bs_to_add: list = []
        _bs_to_complete: list = []
        if bank_stmt_full_copies:
            _bs_vod_flags, _bs_to_add, _bs_to_complete = _compare_with_vod(
                bank_stmt_full_copies, vod_rows, "Bank Statement", "6.1", allow_populate=True
            )
        elif bank_statement_copies:
            _bs_vod_flags, _bs_to_add, _bs_to_complete = _compare_with_vod(
                bank_statement_copies, vod_rows, "Bank Statement", "6.1", allow_populate=True
            )
        if _bank_refs:
            for _f in _bs_vod_flags:
                _f["relevant_documents"] = _bank_refs
        flags += _bs_vod_flags

        # Populate the 2a/VOD with any bank-statement account entirely missing
        # from Encompass. Value mismatches are never overwritten (warned above).
        if _bs_to_add:
            from shared.encompass_io import add_vods
            _add_result = add_vods(loan_id, _bs_to_add, state=state)
            _added = set(_add_result.get("added", []))
            for acct in _bs_to_add:
                inst = acct.get("institution_name") or acct.get("account_number") or "account"
                acct_no = acct.get("account_number") or inst
                if inst in _added:
                    flags.append(_flag(
                        "6.1",
                        f"2a/VOD Account Populated from Bank Statement ({inst})",
                        "info-overwrite",
                        (
                            f"Account {acct_no!r} ({inst}) was missing from the 2a/VOD and has "
                            f"been added from the bank statement"
                            + (f" with balance ${acct['balance']:,.2f}." if acct.get("balance") is not None else ".")
                        ),
                        "",
                        docs=_bank_refs,
                    ))
                else:
                    flags.append(_flag(
                        "6.1",
                        f"2a/VOD Account Missing — Auto-Add Failed ({inst})",
                        "warning",
                        (
                            f"Account {acct_no!r} ({inst}) is missing from the 2a/VOD and could "
                            f"not be added automatically"
                            + (f": {_add_result.get('error')}" if _add_result.get("error") else ".")
                        ),
                        "Add the account to the 2a/VOD manually in Encompass.",
                        docs=_bank_refs,
                    ))

        # Complete BLANK subfields on existing 2a/VOD entries (08 #10).
        _apply_vod_completions(
            loan_id, _bs_to_complete, _bank_refs, "Bank Statement", state, flags,
            dry_run=_vod_dry_run,
        )

    # ── 5. Asset copies ──────────────────────────────────────────────────────
    asset_full_copies = efolder_docs.get("Assets", {}).get("copies", [])
    asset_acct_copies = _doc_all(state, "asset_account_number")

    if vod_rows:
        _asset_refs = _relevant_docs(state, doc_types=["Assets"])
        _asset_vod_flags: list = []
        _asset_to_complete: list = []
        if asset_full_copies:
            _asset_vod_flags, _, _asset_to_complete = _compare_with_vod(asset_full_copies, vod_rows, "Assets", "6.1")
        elif asset_acct_copies:
            _asset_vod_flags, _, _asset_to_complete = _compare_with_vod(asset_acct_copies, vod_rows, "Assets", "6.1")
        if _asset_refs:
            for _f in _asset_vod_flags:
                _f["relevant_documents"] = _asset_refs
        flags += _asset_vod_flags

        # Complete BLANK subfields on existing 2a/VOD entries from asset docs (08 #10).
        _apply_vod_completions(
            loan_id, _asset_to_complete, _asset_refs, "Assets", state, flags,
            dry_run=_vod_dry_run,
        )

    # ── 5b. FHA retirement 60% haircut ───────────────────────────────────────
    # FHA counts only 60% of a retirement account's vested balance (net of any
    # loan against it) toward funds — unless terms of withdrawal evidence the
    # borrower can access 100%. The Encompass 2a/VOD amount for the retirement
    # account should therefore equal vested × 0.6 (net of loans). Flag only —
    # never overwrites the VOD.
    retirement_full_copies = efolder_docs.get("Retirement Account Statement", {}).get("copies", [])
    if is_fha:
        ret_records = _retirement_records(retirement_full_copies, assume_retirement=True)
        # Catch retirement accounts mis-filed under the generic Assets bucket.
        ret_records += _retirement_records(asset_full_copies, assume_retirement=False)
        # Single-value fallback: vested_balance is retirement-specific (bank
        # statements never carry it), so doc_fields presence is a safe signal.
        if not ret_records:
            _v = _parse_float(_doc(state, "vested_balance"))
            if _v is not None:
                ret_records = [{
                    "vested":          _v,
                    "outstanding":     _parse_float(_doc(state, "outstanding_loan_balance")),
                    "terms_present":   _truthy(_doc(state, "terms_of_withdrawal_present")),
                    "institution_raw": (_doc(state, "institution_name") or "").strip(),
                    "institution":     (_doc(state, "institution_name") or "").strip().lower(),
                    "last4":           _last_four(_doc(state, "account_number_last4") or _doc(state, "account_number")),
                    "account_type":    _doc(state, "account_type") or "",
                }]

        _ret_refs = _relevant_docs(state, doc_types=["Retirement Account Statement", "Assets"])

        # Statements are on file but neither the record parse nor the single-field
        # fallback yielded anything usable — don't fall through as if there's no
        # retirement evidence; flag so the 60% haircut isn't silently skipped.
        if retirement_full_copies and not ret_records:
            flags.append(_flag(
                "6.1",
                "FHA Retirement Statement Not Extracted",
                "warning",
                "A Retirement Account Statement is on file but no vested/total balance "
                "or account record could be extracted, so the FHA 60% haircut could not "
                "be verified.",
                "Manually confirm the Encompass 2a/VOD shows 60% of the vested balance "
                "(net of any 401(k) loan).",
                docs=_ret_refs,
            ))

        for rec in ret_records:
            vested = rec.get("vested")
            inst_label = rec.get("institution_raw") or "retirement account"
            if vested is None:
                flags.append(_flag(
                    "6.1",
                    f"FHA Retirement Value Not Extracted ({inst_label})",
                    "warning",
                    "A retirement account statement is present but no vested/total balance "
                    "could be extracted, so the FHA 60% haircut could not be verified.",
                    "Manually confirm the Encompass 2a/VOD shows 60% of the vested balance "
                    "(net of any 401(k) loan).",
                    docs=_ret_refs,
                ))
                continue

            outstanding = rec.get("outstanding") or 0.0
            net = max(vested - outstanding, 0.0)
            expected = round(net * 0.60, 2)        # standard FHA retirement haircut
            expected_full = round(net, 2)          # 100% when terms of withdrawal allow it
            terms_present = rec.get("terms_present")
            basis = f"vested ${vested:,.2f}" + (f" − 401(k) loan ${outstanding:,.2f}" if outstanding else "")

            matched = _match_retirement_vod(rec, vod_rows)
            vod_balance = matched.get("balance") if matched else None
            if matched and vod_balance is not None:
                inst_label = matched.get("institution_name") or inst_label

            if matched is None:
                flags.append(_flag(
                    "6.1",
                    f"FHA Retirement Account Not in 2a/VOD ({inst_label})",
                    "warning",
                    f"Retirement statement shows {basis}. FHA-eligible at 60% = ${expected:,.2f}, "
                    f"but no matching retirement account was found in the Encompass 2a/VOD.",
                    f"Add the retirement account to the 2a/VOD at ${expected:,.2f} (vested × 0.6"
                    + (", or up to ${0:,.2f} at 100% with terms of withdrawal".format(expected_full) if terms_present else "")
                    + ").",
                    docs=_ret_refs,
                ))
                continue

            if vod_balance is None:
                flags.append(_flag(
                    "6.1",
                    f"FHA Retirement Amount Unverifiable in 2a/VOD ({inst_label})",
                    "info",
                    f"Retirement statement shows {basis} (FHA-eligible at 60% = ${expected:,.2f}), "
                    f"but the Encompass 2a/VOD balance could not be read to compare.",
                    "Manually confirm the 2a/VOD shows 60% of vested (net of any loan).",
                    docs=_ret_refs,
                ))
                continue

            if abs(vod_balance - expected) <= 1.00:
                flags.append(_flag(
                    "6.1",
                    f"FHA Retirement 60% Haircut Matches 2a/VOD ({inst_label})",
                    "info",
                    f"{basis}; FHA-eligible at 60% = ${expected:,.2f} matches the Encompass "
                    f"2a/VOD amount ${vod_balance:,.2f} (within $1).",
                    "",
                    docs=_ret_refs,
                ))
            elif terms_present and abs(vod_balance - expected_full) <= 1.00:
                flags.append(_flag(
                    "6.1",
                    f"FHA Retirement Uses 100% — Terms of Withdrawal Documented ({inst_label})",
                    "info",
                    f"{basis}; Encompass 2a/VOD amount ${vod_balance:,.2f} equals 100% of the net "
                    f"vested balance. Terms of withdrawal are present on the statement, which can "
                    f"support using 100% instead of the 60% FHA haircut (${expected:,.2f}).",
                    "Verify the terms of withdrawal support 100% usage; otherwise reduce to "
                    f"${expected:,.2f} (vested × 0.6).",
                    docs=_ret_refs,
                ))
            else:
                flags.append(_flag(
                    "6.1",
                    f"FHA Retirement 60% Haircut Mismatch ({inst_label})",
                    "warning",
                    f"{basis}; FHA-eligible at 60% = ${expected:,.2f}, but the Encompass 2a/VOD "
                    f"shows ${vod_balance:,.2f} (Δ ${abs(vod_balance - expected):,.2f})."
                    + (f" Terms of withdrawal are present, so up to ${expected_full:,.2f} (100%) "
                       "may be allowed." if terms_present else ""),
                    "Update the Encompass retirement asset to vested × 0.6 (net of any 401(k) "
                    "loan)" + (", unless terms of withdrawal support using 100%." if terms_present else "."),
                    docs=_ret_refs,
                ))

    # ── 6. VOD coverage — accounts in VOD with no matching doc ───────────────
    if vod_rows:
        matched_vod_ids = set()

        def _check_matches(copies, field_key="bank_account_number"):
            for row in vod_rows:
                vod_last4 = _last_four(row.get("account_number"))
                vod_inst  = (row.get("institution_name") or "").lower()
                for copy in copies:
                    if _copy_has_fields(copy):
                        doc_num = _last_four(_copy_field(copy, field_key, "account_number"))
                        doc_inst = (_copy_field(copy, "institution_name") or "").lower()
                    else:
                        doc_num = _last_four(copy.get("value"))
                        doc_inst = ""
                    if (vod_last4 and doc_num and vod_last4 == doc_num) or (vod_inst and doc_inst and doc_inst in vod_inst):
                        matched_vod_ids.add(row["vod_id"] + "_" + (row.get("account_number") or ""))

        _check_matches(bank_stmt_full_copies or bank_statement_copies, "bank_account_number")
        _check_matches(asset_full_copies or asset_acct_copies, "asset_account_number")
        _check_matches(retirement_full_copies, "account_number")

        for row in vod_rows:
            key = row["vod_id"] + "_" + (row.get("account_number") or "")
            if key not in matched_vod_ids:
                flags.append(_flag(
                    "6.1",
                    f"VOD Account Has No Supporting Document ({row.get('institution_name') or row.get('account_number') or 'account'})",
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
