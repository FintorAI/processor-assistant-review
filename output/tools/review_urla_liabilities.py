"""review_urla_liabilities — Tool for substep 6.3: Liabilities and VOL (2c)

Step 6 (STEP_06): 1003 URLA Part 3
Phase: DATA_REVIEW

# FACTORY-LOCK: true
"""

import json
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, List, Dict, Any, Optional

from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.prebuilt import InjectedState
from langgraph.types import Command

from ._helpers import _doc, _los, _relevant_docs



ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

logger = logging.getLogger(__name__)

SUBSTEP = "6.3"

# Balance/payment agreement tolerance — credit-report figures lag statement
# cycles, so a small dollar delta is treated as a match, not a mismatch.
_BALANCE_TOL = 50.0
_PAYMENT_TOL = 10.0

# Tradeline type/comment tokens that mean the debt belongs in URLA Section 3d
# (Other Liabilities) rather than 3c (checklist 03 #9).
_SUPPORT_TOKENS = ("child support", "alimony", "maintenance", "separate maintenance")

# Tokens indicating a mortgage / real-estate-secured tradeline (03 #10 / #11).
_MORTGAGE_TOKENS = ("mortgage", "home equity", "heloc", "real estate", "conv re",
                    "fha re", "va re", "deed of trust", "secured by re")

# Status/comment tokens for tradelines that must NOT drive DTI or duplicate
# warnings on their own (authorized user, closed, paid, disputed).
_AUTH_USER_TOKENS = ("authorized user", "auth user", "authorized-user", "terms auth")
_DEFERRED_TOKENS = ("deferred", "deferment", "forbearance")


def _flag(title: str, severity: str, details: str, suggestion: str, docs=None) -> Dict[str, Any]:
    f = {
        "substep": SUBSTEP,
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
# Credit-report tradeline reconciliation helpers (checklist 03 #8/#9/#10/#11)
# ──────────────────────────────────────────────────────────────────────────────

def _num(val) -> Optional[float]:
    """Currency-tolerant float parse; returns None when unparseable/blank."""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    cleaned = re.sub(r"[^0-9.\-]", "", str(val))
    if cleaned in ("", "-", ".", "-.", "--"):
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _last4(acct) -> str:
    d = re.sub(r"\D", "", str(acct or ""))
    return d[-4:] if len(d) >= 4 else d


def _norm_name(name) -> str:
    """Normalise a creditor name for fuzzy comparison (drop suffixes/punctuation)."""
    s = re.sub(r"[^a-z0-9 ]", " ", str(name or "").lower())
    s = re.sub(r"\b(na|n a|bank|banks|card|cards|corp|inc|llc|co|company|"
               r"financial|finance|services|svcs|mortgage|mtg|home loans|"
               r"loans|loan|the|of|and)\b", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _name_overlap(a: str, b: str) -> bool:
    """True when two normalised creditor names share a meaningful token."""
    ta = {t for t in a.split() if len(t) >= 3}
    tb = {t for t in b.split() if len(t) >= 3}
    if not ta or not tb:
        return bool(a and b and (a in b or b in a))
    return bool(ta & tb)


def _tl_get(tl: dict, *keys, default=None):
    for k in keys:
        if k in tl and tl[k] not in (None, ""):
            return tl[k]
    return default


def _normalize_tradeline(tl: dict) -> dict:
    """Coerce one extracted credit-report liability into a common shape."""
    creditor = str(_tl_get(tl, "creditor", "creditor_name", "company", "holder_name",
                           "name", default="")).strip()
    acct = str(_tl_get(tl, "account_number", "account", "acct", "account_no", default="")).strip()
    acct_type = str(_tl_get(tl, "account_type", "type", "loan_type", default="")).strip()
    status = str(_tl_get(tl, "status", "comment", "narrative", "remarks",
                         "payment_status", default="")).strip()
    owner = str(_tl_get(tl, "owner", "borrower", "ecoa", default="")).strip()
    sec_addr = str(_tl_get(tl, "secured_property_address", "property_address",
                           "collateral_address", default="")).strip()
    blob = f"{acct_type} {status} {creditor}".lower()
    is_mortgage = bool(tl.get("is_mortgage")) or any(t in blob for t in _MORTGAGE_TOKENS)
    return {
        "creditor": creditor,
        "creditor_norm": _norm_name(creditor),
        "account_number": acct,
        "last4": _last4(acct),
        "account_type": acct_type,
        "balance": _num(_tl_get(tl, "balance", "unpaid_balance", "current_balance")),
        "payment": _num(_tl_get(tl, "payment", "monthly_payment", "min_payment")),
        "credit_limit": _num(_tl_get(tl, "credit_limit", "high_credit", "limit")),
        "owner": owner,
        "status": status,
        "secured_property_address": sec_addr,
        "is_mortgage": is_mortgage,
        "is_support": any(t in blob for t in _SUPPORT_TOKENS),
        "is_auth_user": any(t in blob for t in _AUTH_USER_TOKENS),
        "is_deferred": any(t in blob for t in _DEFERRED_TOKENS),
    }


def _match_vol(tl: dict, vols: List[dict]) -> Optional[dict]:
    """Find the VOL row that best matches a credit-report tradeline.

    Primary key: account-number last-4 plus a creditor-name token overlap.
    Fallback: creditor-name overlap alone, preferring the closest balance.
    """
    tl_last4 = tl["last4"]
    tl_name = tl["creditor_norm"]

    if tl_last4:
        for v in vols:
            if _last4(v.get("account_number")) == tl_last4 and _name_overlap(
                tl_name, _norm_name(v.get("holder_name"))
            ):
                return v
        for v in vols:  # last4 alone (masked/absent creditor)
            if _last4(v.get("account_number")) == tl_last4:
                return v

    candidates = [v for v in vols if _name_overlap(tl_name, _norm_name(v.get("holder_name")))]
    if not candidates:
        return None
    if tl["balance"] is not None:
        candidates.sort(key=lambda v: abs((v.get("unpaid_balance") or 0.0) - tl["balance"]))
    return candidates[0]


def _addr_tokens(addr: str) -> set:
    return {t for t in re.sub(r"[^a-z0-9 ]", " ", str(addr or "").lower()).split() if t}


def _addr_matches_any(addr: str, others: List[str]) -> bool:
    """Loose street-number + street-name containment match."""
    a = _addr_tokens(addr)
    if not a:
        return False
    a_nums = {t for t in a if t.isdigit()}
    for o in others:
        b = _addr_tokens(o)
        if not b:
            continue
        b_nums = {t for t in b if t.isdigit()}
        # require a shared street number and a shared word token
        if (a_nums & b_nums) and (a & b - a_nums):
            return True
    return False


def _reconcile_credit_liabilities(
    tradelines: List[dict],
    vols: List[dict],
    reo: List[dict],
    subject_addr: str,
    refs: list,
) -> tuple:
    """Reconcile extracted credit-report tradelines against Encompass VOL rows.

    Implements the checklist 03 #8/#9/#10/#11 policy:
      • match → pass (info-confirm only when notable)
      • sub-field blank in VOL but present in doc → queue a blank-only write
      • sub-field mismatch (both populated, differ) → warning, no overwrite
      • whole liability present in credit report, absent from VOL → warning, no write
      • duplicate tradelines (same creditor + acct) → warn once, include once
      • alimony / child-support tradeline → info: belongs in Section 3d, not 3c
      • mortgage tradeline not tied to a property/subject → warning (#10/#11)

    Returns ``(flags, completions)`` where completions feed ``update_vols``.
    """
    flags: List[Dict[str, Any]] = []
    completions: List[Dict[str, Any]] = []

    norm = [_normalize_tradeline(tl) for tl in tradelines if isinstance(tl, dict)]
    reo_addrs = [f"{r.get('street_address','')} {r.get('city','')} {r.get('state','')}" for r in (reo or [])]

    # ── Dedup: same account reported by multiple bureaus. Cluster greedily on
    # shared account last-4 + creditor-name overlap (last-4 spellings vary), or
    # on creditor name alone when neither carries an account number. ──
    clusters: List[List[dict]] = []
    for t in norm:
        placed = False
        for cl in clusters:
            head = cl[0]
            if t["last4"] and head["last4"]:
                same = t["last4"] == head["last4"] and _name_overlap(t["creditor_norm"], head["creditor_norm"])
            else:
                same = bool(t["creditor_norm"]) and t["creditor_norm"] == head["creditor_norm"]
            if same:
                cl.append(t)
                placed = True
                break
        if not placed:
            clusters.append([t])

    reps: List[dict] = []
    for grp in clusters:
        if len(grp) > 1:
            rep = max(grp, key=lambda g: (g["balance"] or 0.0))
            flags.append(_flag(
                f"Duplicate Credit-Report Tradelines: {rep['creditor'] or 'Unknown'}",
                "warning",
                (
                    f"{len(grp)} tradelines for {rep['creditor'] or 'Unknown creditor'} "
                    f"(acct …{rep['last4'] or 'N/A'}) appear in the credit report — likely the same "
                    f"account reported by multiple bureaus. It must be entered only once in the URLA."
                ),
                "Confirm this is a single obligation and include it once in Section 3c. "
                "Remove any duplicate VOL rows.",
                docs=refs,
            ))
            reps.append(rep)
        else:
            reps.append(grp[0])

    for tl in reps:
        label = tl["creditor"] or f"acct …{tl['last4'] or 'N/A'}"

        # ── 03 #9: alimony / child support belongs in Section 3d, not 3c ──
        if tl["is_support"]:
            flags.append(_flag(
                f"Support Obligation — Verify Section 3d Placement: {label}",
                "info",
                (
                    f"Credit report lists a support-type obligation ({tl['account_type'] or 'support'}) "
                    f"for {label}"
                    + (f" (${tl['payment']:,.2f}/mo)." if tl["payment"] else ".")
                    + " Child support / alimony / separate maintenance must be recorded in URLA "
                    "Section 3d (Other Liabilities), not as a 3c credit liability."
                ),
                "Confirm this obligation is captured in Section 3d with the correct monthly amount "
                "and, if applicable, months remaining.",
                docs=refs,
            ))
            continue

        vol = _match_vol(tl, vols)

        # ── Whole liability missing from VOL (present in credit report) ──
        if vol is None:
            if tl["is_auth_user"]:
                flags.append(_flag(
                    f"Authorized-User Tradeline Not in VOL: {label}",
                    "info",
                    (
                        f"{label} (acct …{tl['last4'] or 'N/A'}) is an authorized-user account on the "
                        f"credit report and is not entered in Section 3c. Authorized-user debts may be "
                        f"intentionally omitted when the borrower is not the obligor."
                    ),
                    "Confirm the borrower is not contractually liable; document the omission.",
                    docs=refs,
                ))
            else:
                flags.append(_flag(
                    f"Credit-Report Liability Missing from VOL: {label}",
                    "warning",
                    (
                        f"{label} (acct …{tl['last4'] or 'N/A'}, {tl['account_type'] or 'unknown type'}) "
                        f"appears on the credit report"
                        + (f" with balance ${tl['balance']:,.2f}" if tl["balance"] is not None else "")
                        + (f" and ${tl['payment']:,.2f}/mo payment" if tl["payment"] else "")
                        + " but has no matching row in Section 3c (VOL). It was NOT auto-added because "
                        "a new liability affects DTI and requires underwriting review."
                    ),
                    "Add this liability to Section 3c manually (or confirm it is correctly excluded, "
                    "e.g. paid/closed/disputed) and re-run.",
                    docs=refs,
                ))
            continue

        # ── Matched — compare sub-fields; fill blanks, warn on mismatch ──
        vol_id = vol.get("vol_id")
        updates: Dict[str, Any] = {}

        # Balance
        vbal = vol.get("unpaid_balance")
        if tl["balance"] is not None:
            if vbal in (None, "") or float(vbal or 0) == 0.0:
                updates["balance"] = tl["balance"]
            elif abs(float(vbal) - tl["balance"]) > _BALANCE_TOL:
                flags.append(_flag(
                    f"Liability Balance Mismatch: {label}",
                    "warning",
                    (
                        f"Credit report shows balance ${tl['balance']:,.2f} for {label} "
                        f"(acct …{tl['last4'] or 'N/A'}) but VOL has ${float(vbal):,.2f}."
                    ),
                    "Verify the correct current balance and reconcile Section 3c.",
                    docs=refs,
                ))

        # Monthly payment
        vpmt = vol.get("monthly_payment")
        if tl["payment"] is not None:
            if vpmt in (None, "") or float(vpmt or 0) == 0.0:
                updates["payment"] = tl["payment"]
            elif abs(float(vpmt) - tl["payment"]) > _PAYMENT_TOL:
                flags.append(_flag(
                    f"Liability Payment Mismatch: {label}",
                    "warning",
                    (
                        f"Credit report shows ${tl['payment']:,.2f}/mo for {label} but VOL has "
                        f"${float(vpmt):,.2f}/mo."
                    ),
                    "Verify the monthly payment used for DTI and reconcile Section 3c.",
                    docs=refs,
                ))

        # Credit limit (revolving)
        vlimit = vol.get("credit_limit")
        if tl["credit_limit"] is not None and (vlimit in (None, "") or float(vlimit or 0) == 0.0):
            updates["credit_limit"] = tl["credit_limit"]

        # Account number
        if tl["account_number"] and not (vol.get("account_number") or "").strip():
            updates["account_number"] = tl["account_number"]

        # Account type mismatch (never auto-written — enum risk)
        v_type = _norm_name(vol.get("liability_type"))
        t_type = _norm_name(tl["account_type"])
        if v_type and t_type and not _name_overlap(v_type, t_type):
            flags.append(_flag(
                f"Liability Type Mismatch: {label}",
                "warning",
                (
                    f"Credit report classifies {label} as '{tl['account_type']}' but VOL shows "
                    f"'{vol.get('liability_type')}'."
                ),
                "Confirm the correct liability type in Section 3c.",
                docs=refs,
            ))

        if updates and vol_id:
            completions.append({"vol_id": vol_id, "label": label, "updates": updates})

        # ── 03 #10 / #11: mortgage tradeline must tie to a property ──
        if tl["is_mortgage"]:
            sec = tl["secured_property_address"]
            tied = False
            if sec:
                tied = _addr_matches_any(sec, reo_addrs) or (
                    subject_addr and _addr_matches_any(sec, [subject_addr])
                )
            if sec and not tied:
                flags.append(_flag(
                    f"Mortgage Debt Not Tied to a Property: {label}",
                    "warning",
                    (
                        f"Mortgage liability {label} (secured by '{sec}') does not match the subject "
                        f"property or any REO row in Section 3. Every mortgage debt must be linked to "
                        f"a property on the Schedule of Real Estate."
                    ),
                    "Add/confirm the corresponding REO property (Section 3) and link the mortgage to it.",
                    docs=refs,
                ))
            elif not sec:
                flags.append(_flag(
                    f"Mortgage Debt — Confirm Property Link: {label}",
                    "info",
                    (
                        f"Mortgage liability {label} has no secured-property address on the credit "
                        f"report. Confirm it is correctly linked to a property on the Schedule of "
                        f"Real Estate (Section 3)."
                    ),
                    "Verify the mortgage is tied to the correct REO/subject property.",
                    docs=refs,
                ))

    return flags, completions


def _apply_vol_completions(
    loan_id: str,
    completions: List[dict],
    refs: list,
    state: dict,
    flags: list,
    dry_run: bool = False,
) -> None:
    """Fill blank sub-fields on existing VOL rows and emit audit flags (03 #8)."""
    if not completions:
        return

    _names = {
        "balance": "unpaid balance", "payment": "monthly payment",
        "credit_limit": "credit limit", "account_number": "account number",
    }

    def _field_list(upd):
        return ", ".join(_names.get(k, k) for k in upd)

    if dry_run:
        for c in completions:
            flags.append(_flag(
                f"VOL Entry Completion (dry-run): {c.get('label')}",
                "info",
                (
                    f"[DRY-RUN] Would fill blank field(s) [{_field_list(c.get('updates', {}))}] on the "
                    f"existing Section 3c liability for {c.get('label')} from the credit report."
                ),
                "",
                docs=refs,
            ))
        return

    from shared.encompass_io import update_vols
    res = update_vols(loan_id, completions, state=state)
    updated_by_id = {u.get("vol_id"): u.get("fields", []) for u in res.get("updated", [])}

    for c in completions:
        vid = c.get("vol_id")
        label = c.get("label")
        if vid in updated_by_id:
            flags.append(_flag(
                f"VOL Entry Completed: {label}",
                "info-overwrite",
                (
                    f"Filled blank field(s) on the existing Section 3c liability for {label} from the "
                    f"credit report: {', '.join(updated_by_id[vid])}."
                ),
                "Verify the completed liability row in Encompass.",
                docs=refs,
            ))
        elif not res.get("success"):
            flags.append(_flag(
                f"VOL Completion Failed: {label}",
                "warning",
                f"Could not fill blank field(s) on the Section 3c liability for {label}: {res.get('error')}",
                "Complete the liability row manually in Encompass.",
                docs=refs,
            ))


@tool
def review_urla_liabilities(
    tool_call_id: Annotated[str, InjectedToolCallId],
    state: Annotated[dict, InjectedState],
) -> Command:
    """Review Section 3c — Liabilities (VOL) and reconcile against the credit report.

    Fetches all VOL rows from the Encompass v3 API and checks:
      - Column 1 (Exclude Monthly Payment = Y): flag each excluded debt and ask
        why it was excluded (already paid off? not obligated?).
      - Column 2 (To Be Paid Off = Y): flag each such debt and request the most
        recent statement for that creditor (e.g. JPMCB card).

    Then reconciles the extracted credit-report ``liabilities[]`` tradelines
    against the VOL rows (checklist 03 #8/#9/#10/#11):
      - match → pass; blank VOL sub-field + doc value → fill blank only
        (unpaid balance / payment / credit limit / account number);
      - both populated but differ → warning (never overwritten);
      - tradeline with no VOL row → warning (never auto-added — affects DTI);
      - duplicate tradelines (same creditor + acct) → warn once, include once;
      - alimony / child-support tradeline → info: belongs in Section 3d;
      - mortgage tradeline not tied to subject/REO → warning (#10/#11).
    The credit-report block is a no-op when the report has not been extracted.

    Call this tool during STEP_06 (1003 URLA Part 3) as substep 6.3.
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
            severity="critical",
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

    # ── Credit-report reconciliation (03 #8 / #9 / #10 / #11) ──
    # Consumes the extracted `liabilities[]` tradelines; no-op (backward
    # compatible) when the credit report has not been extracted.
    recon_count = 0
    completion_count = 0
    raw_tradelines = _doc(state, "liabilities")
    tradelines = raw_tradelines if isinstance(raw_tradelines, list) else []
    if tradelines:
        try:
            from shared.encompass_io import read_reo_properties
            reo = read_reo_properties(loan_id, state=state)
        except Exception as exc:
            logger.warning(f"[REVIEW_URLA_LIABILITIES] Could not fetch REO for mortgage linkage: {exc}")
            reo = []

        subject_addr = " ".join(str(_los(state, k) or "") for k in (
            "property_address", "property_city", "property_state", "property_zip",
        )).strip()
        cr_refs = _relevant_docs(state, "liabilities", doc_types=["Credit Report"])

        recon_flags, completions = _reconcile_credit_liabilities(
            tradelines, vols, reo, subject_addr, cr_refs
        )
        recon_count = len(tradelines)

        _vol_dry_run = False
        try:
            from output.registry import DEV_MODE
            _vol_dry_run = getattr(DEV_MODE, "dry_run", False)
        except Exception:
            _vol_dry_run = False

        _apply_vol_completions(
            loan_id, completions, cr_refs, state, recon_flags, dry_run=_vol_dry_run,
        )
        completion_count = len(completions)
        flags += recon_flags
        logger.info(
            f"[REVIEW_URLA_LIABILITIES] Reconciled {recon_count} credit-report tradeline(s); "
            f"{completion_count} VOL completion(s) queued"
        )

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
        "credit_tradelines_reconciled": recon_count,
        "vol_completions": completion_count,
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
