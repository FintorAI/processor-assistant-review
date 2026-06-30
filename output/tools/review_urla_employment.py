"""review_urla_employment — Tool for substep 5.1: Employment Verification (1b VOE)

Step 5 (STEP_05): 1003 URLA Page 2
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

from ._helpers import _los, _doc, _write_fields, _relevant_docs
from shared.encompass_io import read_employment

logger = logging.getLogger(__name__)

# ── Helpers ────────────────────────────────────────────────────────────────────

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


def _normalize_name(v: Optional[str]) -> str:
    """Lowercase, strip punctuation for fuzzy employer name comparison."""
    if not v:
        return ""
    return re.sub(r"[^a-z0-9 ]", "", v.lower()).strip()


def _parse_amount(v) -> Optional[float]:
    """Parse dollar amount from string ('$7,500.00'), int, or float to float."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        cleaned = re.sub(r"[^0-9.]", "", str(v))
        return float(cleaned) if cleaned else None
    except (ValueError, TypeError):
        return None


def _periods_per_year(freq) -> Optional[int]:
    """Map a pay-period frequency string to the number of periods per year."""
    f = str(freq or "").strip().lower().replace("-", "").replace(" ", "")
    return {
        "weekly": 52,
        "biweekly": 26,
        "semimonthly": 24,
        "twicemonthly": 24,
        "monthly": 12,
        "quarterly": 4,
        "semiannually": 2,
        "annually": 1,
        "annual": 1,
        "yearly": 1,
    }.get(f)


def _entry_populated(entry: dict) -> bool:
    """True if at least employer_name is set."""
    return bool(entry.get("employer_name"))


def _get_voe_copy_for_employer(state: dict, los_employer_name: str):
    """Return (extracted_fields, copy_index) from the VOE copy whose employer name
    best matches the given LOS employer name. Falls back to copy 0 if no match.

    The VOE bucket may hold multiple attachments (one per employer). When comparing
    the LOS current employer against the VOE, we must use the copy from the same
    employer rather than blindly using the first copy (which may be a different /
    terminated employer).

    Returns (flat dict {field_key: value}, copy_index) — copy_index is None when
    there are no copies.
    """
    copies = (
        state.get("efolder_documents", {})
        .get("VOE - non service provider", {})
        .get("copies", [])
    )
    if not copies:
        return {}, None

    los_norm = _normalize_name(los_employer_name)

    best_copy: dict | None = None
    best_idx: int = -1
    best_score: int = -1

    for idx, copy in enumerate(copies):
        ef = copy.get("extracted_fields", {})
        raw_name = ef.get("current_employer_name", {})
        voe_name = raw_name.get("value") if isinstance(raw_name, dict) else raw_name
        if not voe_name:
            continue
        voe_norm = _normalize_name(str(voe_name))
        # Token-based similarity: count matching words
        los_tokens = set(los_norm.split())
        voe_tokens = set(voe_norm.split())
        score = len(los_tokens & voe_tokens)
        if score > best_score:
            best_score = score
            best_copy = ef
            best_idx = idx

    # If best match has at least 1 token in common, use it; otherwise fall back to copy 0
    if best_score > 0 and best_copy is not None:
        flat = {k: (v.get("value") if isinstance(v, dict) else v) for k, v in best_copy.items()}
        return flat, best_idx

    # Fallback: copy 0
    ef0 = copies[0].get("extracted_fields", {})
    flat = {k: (v.get("value") if isinstance(v, dict) else v) for k, v in ef0.items()}
    return flat, 0


def _voe_values(copy_flat: dict, fallback_state: Optional[dict] = None) -> dict:
    """Build the VOE comparison dict from a matched copy's flat fields.

    For the borrower we fall back to the top-level _doc(...) values to preserve
    historical behavior; the co-borrower uses only its own matched copy (the
    global doc_fields are the borrower's).
    """
    def g(key):
        v = copy_flat.get(key)
        if (v is None or v == "") and fallback_state is not None:
            v = _doc(fallback_state, key)
        return v
    return {
        "cur_name":     g("current_employer_name"),
        "cur_hire":     g("current_original_hire_date"),
        "cur_base_pay": g("current_monthly_base_pay"),
        "cur_position": g("current_position_title"),
        "cur_rate":     g("current_rate_of_pay"),
        "cur_hours_pp": g("current_avg_hours_per_pay_period"),
        "cur_pp_freq":  g("current_pay_period_frequency"),
        "prev_name":    g("previous_employer_name"),
        "prev_base_pay": g("previous_monthly_base_pay"),
    }


def _split_current_prior(entries: list, flags: list, who: str):
    """Return (current_entry, [prior_entries]) from a normalized employment list.
    Flags multiple-current as a warning."""
    current_entry = None
    prior_entries: list = []
    for e in entries:
        if not _entry_populated(e):
            continue
        if e.get("current"):
            if current_entry:
                flags.append(_flag("5.1",
                    f"Multiple Current Employers ({who})",
                    "warning",
                    f"More than one {who.lower()} employment record has currentEmploymentIndicator=True. "
                    f"First: '{current_entry.get('employer_name')}', also: '{e.get('employer_name')}'.",
                    "Verify only one entry should be Current; mark additional entries as Prior if applicable.",
                ))
            else:
                current_entry = e
        else:
            prior_entries.append(e)
    return current_entry, prior_entries


def _check_current_employer(who: str, e: dict, voe: dict, refs_cur: list,
                            has_prior: bool, flags: list) -> None:
    """Cross-check a Current employment entry against the matched VOE copy."""
    # printAttachmentIndicator — "print see attached borrower's authorization"
    raw = e.get("_raw") or {}
    if raw.get("printAttachmentIndicator") is False:
        flags.append(_flag("5.1",
            f"Authorization Attachment Not Checked ({who})",
            "warning",
            "printAttachmentIndicator is False — 'see attached borrower's authorization' will not print on the VOE signature line.",
            "Check the authorization attachment checkbox in Encompass for the current employer.",
        ))

    voe_cur_name = voe.get("cur_name")
    # Employer name
    if voe_cur_name:
        los_name = _normalize_name(e.get("employer_name"))
        doc_name = _normalize_name(voe_cur_name)
        if los_name and doc_name and los_name != doc_name:
            flags.append(_flag("5.1",
                f"Employer Name Mismatch — Current ({who})",
                "warning",
                f"LOS: '{e.get('employer_name')}' | VOE: '{voe_cur_name}'",
                "Correct the employer name in Encompass to match the VOE",
                docs=refs_cur,
            ))
    elif not e.get("employer_name"):
        flags.append(_flag("5.1",
            f"Current Employer Name Missing ({who})",
            "warning",
            "BE0102 (employer name) is empty for the Current employment entry.",
            "Enter the current employer name in Encompass",
        ))

    # Position / title vs VOE
    voe_cur_position = voe.get("cur_position")
    if voe_cur_position and e.get("position_title"):
        if _normalize_name(e["position_title"]) != _normalize_name(voe_cur_position):
            flags.append(_flag("5.1",
                f"Position Title Mismatch — Current ({who})",
                "warning",
                f"LOS: '{e['position_title']}' | VOE: '{voe_cur_position}'",
                "Correct the position/title in Encompass to match the VOE",
                docs=refs_cur,
            ))

    # Employer phone — presence only
    if not e.get("employer_phone"):
        flags.append(_flag("5.1",
            f"Current Employer Phone Missing ({who}, BE0117)",
            "warning",
            "BE0117 (employer phone) is empty for the current employer.",
            "Enter the current employer phone number in Encompass",
        ))

    # Date hired vs VOE — normalize both to YYYY-MM-DD before comparing
    voe_cur_hire = voe.get("cur_hire")
    if voe_cur_hire and e.get("date_hired"):
        _date_fmts = ["%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y"]

        def _parse_date(s):
            for _f in _date_fmts:
                try:
                    return datetime.strptime(s.strip(), _f).date()
                except ValueError:
                    continue
            return None
        _los_d = _parse_date(e["date_hired"])
        _voe_d = _parse_date(voe_cur_hire)
        if _los_d and _voe_d and _los_d != _voe_d:
            flags.append(_flag("5.1",
                f"Hire Date Mismatch — Current ({who})",
                "warning",
                f"LOS hire date: '{e['date_hired']}' | VOE original hire date: '{voe_cur_hire}'",
                "Correct the hire date in Encompass to match the VOE",
                docs=refs_cur,
            ))

    # Date terminated — must be null/empty for current employer
    if e.get("date_terminated"):
        flags.append(_flag("5.1",
            f"Date Terminated Populated for Current Employer ({who}, BE0114)",
            "warning",
            f"BE0114 shows termination date '{e['date_terminated']}' but employment type is Current.",
            "Clear the termination date for the current employer in Encompass",
        ))

    # Monthly base pay vs VOE
    voe_cur_base_pay = voe.get("cur_base_pay")
    if voe_cur_base_pay or e.get("monthly_base_pay"):
        los_pay = _parse_amount(e.get("monthly_base_pay"))
        doc_pay = _parse_amount(voe_cur_base_pay)
        if los_pay is not None and doc_pay is not None:
            if abs(los_pay - doc_pay) > 1.00:  # allow $1 rounding tolerance
                # Show the full derivation so the processor can see WHY they differ.
                _detail = (
                    f"LOS (1003): ${los_pay:,.2f}/mo  |  VOE: ${doc_pay:,.2f}/mo  |  "
                    f"Δ ${abs(los_pay - doc_pay):,.2f}/mo"
                )
                _rate  = _parse_amount(voe.get("cur_rate"))
                _hours = _parse_amount(voe.get("cur_hours_pp"))
                _ppy   = _periods_per_year(voe.get("cur_pp_freq"))
                if _rate and _hours and _ppy:
                    _voe_annual = _rate * _hours * _ppy
                    _detail += (
                        f"\nVOE base = rate × avg hours/pay-period × periods/yr ÷ 12: "
                        f"${_rate:,.2f}/hr × {_hours:g} hrs × {_ppy} ({voe.get('cur_pp_freq')}) "
                        f"= ${_voe_annual:,.2f}/yr ÷ 12 = ${_voe_annual / 12:,.2f}/mo "
                        f"({_hours * _ppy:,.0f} hrs/yr ≈ {(_hours * _ppy) / 52:.1f} hrs/wk)"
                    )
                if _rate:
                    _los_annual_hrs = los_pay * 12 / _rate
                    _detail += (
                        f"\nLOS base ${los_pay:,.2f}/mo at ${_rate:,.2f}/hr implies "
                        f"{_los_annual_hrs:,.0f} hrs/yr ≈ {_los_annual_hrs / 52:.1f} hrs/wk "
                        f"(${_rate:,.2f} × 40 × 52 ÷ 12 = ${_rate * 40 * 52 / 12:,.2f}/mo)"
                    )
                    if _hours and _ppy:
                        _voe_hpw = (_hours * _ppy) / 52
                        _los_hpw = _los_annual_hrs / 52
                        _detail += (
                            f"\n→ Difference driven by hours/week: VOE {_voe_hpw:.1f} vs LOS "
                            f"{_los_hpw:.1f} ({_voe_hpw - _los_hpw:+.1f} hrs/wk)."
                        )
                flags.append(_flag("5.1",
                    f"Monthly Base Pay Mismatch — Current ({who})",
                    "warning",
                    _detail,
                    "Reconcile the monthly base pay with the VOE — the gap is driven by the "
                    "assumed hours/week. Confirm whether to qualify on 40 hrs base or the VOE's "
                    "averaged hours (which include regular overtime).",
                    docs=refs_cur,
                ))
            else:
                # Income matches VOE — surface a positive confirmation.
                flags.append(_flag("5.1",
                    f"Monthly Base Pay Match — Current ({who})",
                    "info",
                    f"Income validated: LOS (1003) ${los_pay:,.2f}/mo matches VOE ${doc_pay:,.2f}/mo (±$1).",
                    "Current employment income agrees with the VOE — no action needed.",
                    docs=refs_cur,
                ))

    # Employment duration — trigger gap check if < 2 years total
    yrs = e.get("years_in_job")
    mos = e.get("months_in_job")
    total_mos = (int(yrs or 0) * 12 + int(mos or 0)) if (yrs is not None or mos is not None) else None
    if total_mos is not None and total_mos < 24 and not has_prior:
        flags.append(_flag("5.1",
            f"Employment History Gap — No Prior Employer ({who}, < 2 Years Current)",
            "warning",
            f"Current employment is {total_mos} months (< 2 years) and no prior employer entry found.",
            "Add prior employment history entries in Encompass to document 2-year history",
        ))


def _check_prior_employers(who: str, prior_entries: list, voe: dict,
                           refs_all: list, flags: list) -> None:
    """Cross-check Prior employment entries against the VOE 'previous_*' fields."""
    voe_prev_name = voe.get("prev_name")
    voe_prev_base_pay = voe.get("prev_base_pay")
    for e in prior_entries:
        if voe_prev_name and e.get("employer_name"):
            los_name = _normalize_name(e["employer_name"])
            doc_name = _normalize_name(voe_prev_name)
            if los_name and doc_name and los_name != doc_name:
                flags.append(_flag("5.1",
                    f"Employer Name Mismatch — Prior ({who})",
                    "warning",
                    f"LOS prior employer: '{e['employer_name']}' | VOE previous: '{voe_prev_name}'",
                    "Correct the prior employer name in Encompass to match the VOE",
                    docs=refs_all,
                ))

        if not e.get("date_terminated"):
            flags.append(_flag("5.1",
                f"Date Terminated Missing for Prior Employer ({who}, BE0114)",
                "warning",
                f"Employment entry for '{e.get('employer_name', 'unknown')}' is Prior but date terminated is empty.",
                "Enter the termination date for the prior employer in Encompass",
            ))

        if voe_prev_base_pay or e.get("monthly_base_pay"):
            los_pay = _parse_amount(e.get("monthly_base_pay"))
            doc_pay = _parse_amount(voe_prev_base_pay)
            if los_pay is not None and doc_pay is not None:
                if abs(los_pay - doc_pay) > 1.00:
                    flags.append(_flag("5.1",
                        f"Monthly Base Pay Mismatch — Prior ({who})",
                        "warning",
                        f"LOS: ${los_pay:,.2f} | VOE previous: ${doc_pay:,.2f}",
                        "Reconcile prior employer base pay with the VOE",
                        docs=refs_all,
                    ))
                else:
                    flags.append(_flag("5.1",
                        f"Monthly Base Pay Match — Prior ({who})",
                        "info",
                        f"Income validated: LOS ${los_pay:,.2f}/mo matches VOE previous ${doc_pay:,.2f}/mo (±$1).",
                        "Prior employment income agrees with the VOE — no action needed.",
                        docs=refs_all,
                    ))


def _check_employment_gap(who: str, current_entry: Optional[dict],
                          prior_entries: list, loan_type: str, flags: list) -> None:
    """Flag the gap between the most-recent prior job and the current hire date."""
    if not (current_entry and prior_entries):
        return
    cur_hire = current_entry.get("date_hired")
    _date_fmts_gap = ["%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y"]

    def _parse_date_gap(s):
        if not s:
            return None
        for _f in _date_fmts_gap:
            try:
                return datetime.strptime(s.strip(), _f).date()
            except ValueError:
                continue
        return None

    _most_recent_prior = None
    _most_recent_term_date = None
    for prior in prior_entries:
        _td = _parse_date_gap(prior.get("date_terminated"))
        if _td and (_most_recent_term_date is None or _td > _most_recent_term_date):
            _most_recent_term_date = _td
            _most_recent_prior = prior

    if not (_most_recent_prior and cur_hire):
        return
    prior_term = _most_recent_prior.get("date_terminated")
    d_hire = _parse_date_gap(cur_hire)
    d_term = _most_recent_term_date
    if not (d_hire and d_term):
        return
    try:
        gap_days = (d_hire - d_term).days
        if gap_days > 30:
            gap_months = gap_days // 30
            if gap_months < 6:
                if loan_type.upper() == "FHA":
                    flags.append(_flag("5.1",
                        f"FHA Employment Gap — Explanation Required ({who})",
                        "warning",
                        f"Gap of ~{gap_months} month(s) between prior termination ({prior_term}) and current hire ({cur_hire}). FHA requires explanation for gaps < 6 months.",
                        "Obtain a written explanation letter from the borrower for the employment gap",
                    ))
            else:
                flags.append(_flag("5.1",
                    f"Employment Gap > 6 Months — Documentation Required ({who})",
                    "warning",
                    f"Gap of ~{gap_months} month(s) between prior termination ({prior_term}) and current hire ({cur_hire}). Requires documented 2-year history.",
                    "Document the 2-year employment history before the gap and verify income continuity",
                ))
    except Exception:
        pass


# ── Main tool ─────────────────────────────────────────────────────────────────

@tool
def review_urla_employment(
    tool_call_id: Annotated[str, InjectedToolCallId],
    state: Annotated[dict, InjectedState],
) -> Command:
    """Cross-check employment fields against VOE using BE01xx/BE02xx/BE03xx entries.

    Runs the full income/employment cross-check for BOTH the borrower and the
    co-borrower (each matched to its own VOE copy by employer name). For each:
    - Reads currentEmploymentIndicator to determine Current vs Prior.
    - Verifies LOS employment fields against the corresponding VOE doc fields
      (current_ prefix for Current employer, previous_ prefix for Prior employer):
      employer name, position, hire date, date terminated, and monthly base pay.
    - Emits a positive "Monthly Base Pay Match" info flag when income agrees.
    - Checks authorization checkbox, date terminated logic, and employment gaps.
    All flag titles are person-tagged (Borrower / Co-Borrower) so dedup keeps both.

    Call this tool during STEP_05 (1003 URLA Page 2) as substep 5.1.

    Writes: URLA.X201/X202 (Section 2c Does Not Apply, borr/co-borr) and
      URLA.X203/X204 (Section 2d Does Not Apply) — auto-checked (written as "true",
      reads back as "Y") when the corresponding additional/previous-employment section is empty.
    """
    loan_id = state.get("loan_id")
    if not loan_id:
        return Command(update={"messages": [ToolMessage(
            content=json.dumps({"error": "No loan_id in state. Run find_loan first."}),
            tool_call_id=tool_call_id,
        )]})

    logger.info(f"[REVIEW_URLA_EMPLOYMENT] Starting for loan {str(loan_id)[:8]}...")

    flags = []
    loan_type = _los(state, "loan_type") or ""

    # ── Fetch employment records from Encompass v3 API ─────────────────────────
    # GET /encompass/v3/loans/{loanId}/applications/{applicationId}/borrower/employment
    try:
        api_entries = read_employment(loan_id, state=state, applicant_type="borrower")
    except LookupError as e:
        # API returned "collection does not exist" — no rows created yet
        flags.append(_flag(
            "5.1",
            "VOE Form Not Populated in Encompass",
            "critical",
            f"Encompass v3 employment API returned 'collection does not exist': {e}. "
            "No employment rows have been created in the VOE form.",
            "Open the VOE form in Encompass and add the borrower's current (and prior, if < 2 years) employer entries.",
        ))
        result = {
            "success": False,
            "substep": "5.1",
            "tool": "review_urla_employment",
            "entries_found": 0,
            "flags_count": len(flags),
            "message": "Employment Verification blocked — VOE form not populated in Encompass",
        }
        return Command(update={
            "flags": flags,
            "messages": [ToolMessage(content=json.dumps(result), tool_call_id=tool_call_id)],
        })
    except Exception as e:
        logger.error(f"[REVIEW_URLA_EMPLOYMENT] Employment API failed: {e}")
        flags.append(_flag(
            "5.1",
            "Employment API Error",
            "warning",
            f"Could not fetch employment records from Encompass v3 API: {e}",
            "Check Encompass connectivity and retry.",
        ))
        api_entries = []

    # ── Guard: empty response ─────────────────────────────────────────────────
    if not api_entries:
        flags.append(_flag(
            "5.1",
            "VOE Form Not Populated in Encompass",
            "critical",
            "Encompass v3 employment API returned no records for this borrower.",
            "Open the VOE form in Encompass and add the borrower's current (and prior, if < 2 years) employer entries.",
        ))
        result = {
            "success": False,
            "substep": "5.1",
            "tool": "review_urla_employment",
            "entries_found": 0,
            "flags_count": len(flags),
            "message": "Employment Verification blocked — no VOE entries found",
        }
        return Command(update={
            "flags": flags,
            "messages": [ToolMessage(content=json.dumps(result), tool_call_id=tool_call_id)],
        })

    # ── Identify current / prior employment for borrower + co-borrower ─────────
    # currentEmploymentIndicator: True = Current, False = Prior
    current_entry, prior_entries = _split_current_prior(api_entries, flags, "Borrower")

    # Co-borrower employment (also drives the Does-Not-Apply checks below)
    coborr_fetch_failed = False
    try:
        coborr_api_entries = read_employment(loan_id, state=state, applicant_type="coborrower")
    except LookupError:
        # Expected when the co-borrower has no employment records.
        coborr_api_entries = []
    except Exception as e:  # noqa: BLE001 — record real failures rather than hide them
        logger.warning(f"[REVIEW_URLA_EMPLOYMENT] Co-borrower employment fetch failed: {e}")
        coborr_api_entries = []
        coborr_fetch_failed = True
    has_coborr = len(coborr_api_entries) > 0 or bool(_los(state, "coborrower_first_name"))
    if coborr_fetch_failed and has_coborr:
        flags.append(_flag("5.1",
            "Co-Borrower Employment Fetch Failed",
            "warning",
            "Could not read the co-borrower's employment records from Encompass, so the "
            "co-borrower employment cross-check may be incomplete.",
            "Re-run the review or verify the co-borrower's employment entries manually.",
        ))
    coborr_current, coborr_priors = _split_current_prior(coborr_api_entries, flags, "Co-Borrower")

    # ── Match each applicant to its own VOE copy (by employer name) ────────────
    # The VOE bucket may hold multiple attachments (one per employer/person). We
    # match each applicant's current employer to the right copy so a terminated or
    # other-person's VOE doesn't cause false mismatches.
    _voe_refs_all = _relevant_docs(state, doc_types=["VOE - non service provider"])

    def _voe_match(entry, fallback, require_match):
        name = (entry.get("employer_name") if entry else "") or ""
        flat, idx = _get_voe_copy_for_employer(state, name)
        matched = idx is not None
        # For the co-borrower, only trust the copy when its employer name actually
        # shares a token with the LOS employer — otherwise the helper's copy-0
        # fallback could point at the borrower's VOE and fabricate mismatches.
        if require_match:
            copy_emp = flat.get("current_employer_name") or ""
            if not (set(_normalize_name(name).split()) & set(_normalize_name(copy_emp).split())):
                flat, idx = {}, None
                matched = False
        refs = _relevant_docs(
            state,
            doc_types=["VOE - non service provider"],
            matched=({"VOE - non service provider": idx} if idx is not None else None),
        )
        # Borrower keeps the historical top-level _doc fallback; co-borrower uses
        # only its matched copy (global doc_fields are the borrower's). `matched`
        # is returned so the caller can flag an unmatched/missing co-borrower VOE
        # even after `flat` is cleared above.
        return _voe_values(flat, fallback_state=fallback), refs, matched

    borr_voe, borr_refs_cur, _ = _voe_match(current_entry, state, require_match=False)
    coborr_voe, coborr_refs_cur, coborr_voe_matched = _voe_match(coborr_current, None, require_match=True)

    # ── Section 1b/1c/1d income fields + Does Not Apply checkboxes ───────────
    borr_base_income        = _los(state, "borr_base_monthly_income")      # FE0119
    coborr_base_income      = _los(state, "coborr_base_monthly_income")    # FE0219
    borr_1b_dna             = _los(state, "borr_1b_dna")                   # URLA.X199
    coborr_1b_dna           = _los(state, "coborr_1b_dna")                 # URLA.X200
    borr_1c_employer        = _los(state, "borr_1c_employer_name")         # FE0302
    borr_1c_gross           = _los(state, "borr_1c_total_gross_income")    # FE0112
    borr_1c_monthly         = _los(state, "borr_1c_monthly_income")        # FE0156
    coborr_1c_employer      = _los(state, "coborr_1c_employer_name")       # FE0402
    coborr_1c_gross         = _los(state, "coborr_1c_total_gross_income")  # FE0212
    coborr_1c_monthly       = _los(state, "coborr_1c_monthly_income")      # FE0256
    borr_1c_dna             = _los(state, "borr_1c_dna")                   # URLA.X201
    coborr_1c_dna           = _los(state, "coborr_1c_dna")                 # URLA.X202
    borr_1d_employer        = _los(state, "borr_1d_employer_name")         # FE0502
    borr_1d_gross           = _los(state, "borr_1d_total_gross_income")    # FE0312
    borr_1d_monthly         = _los(state, "borr_1d_monthly_income")        # FE0356
    coborr_1d_employer      = _los(state, "coborr_1d_employer_name")       # FE0602
    coborr_1d_gross         = _los(state, "coborr_1d_total_gross_income")  # FE0412
    coborr_1d_monthly       = _los(state, "coborr_1d_monthly_income")      # FE0456
    borr_1d_dna             = _los(state, "borr_1d_dna")                   # URLA.X203
    coborr_1d_dna           = _los(state, "coborr_1d_dna")                 # URLA.X204

    # ── Employment cross-checks — borrower then co-borrower ───────────────────
    if current_entry:
        _check_current_employer("Borrower", current_entry, borr_voe, borr_refs_cur,
                                bool(prior_entries), flags)
    elif any(_entry_populated(e) for e in api_entries):
        flags.append(_flag("5.1",
            "No Current Employer Entry Found (Borrower)",
            "warning",
            "Employment entries exist but none are marked as Current.",
            "Mark the current employer entry type as 'Current' in Encompass (BE0109)",
        ))
    _check_prior_employers("Borrower", prior_entries, borr_voe, _voe_refs_all, flags)
    _check_employment_gap("Borrower", current_entry, prior_entries, loan_type, flags)

    if has_coborr and coborr_api_entries:
        if coborr_current:
            if not coborr_voe_matched:
                flags.append(_flag("5.1",
                    "VOE Not Matched — Current (Co-Borrower)",
                    "warning",
                    "No VOE copy could be matched to the co-borrower's current employer, "
                    "so the co-borrower income/employment cross-check could not be "
                    "completed against a verification of employment.",
                    "Confirm a VOE for the co-borrower's current employer is in the eFolder.",
                ))
            _check_current_employer("Co-Borrower", coborr_current, coborr_voe, coborr_refs_cur,
                                    bool(coborr_priors), flags)
        elif any(_entry_populated(e) for e in coborr_api_entries):
            flags.append(_flag("5.1",
                "No Current Employer Entry Found (Co-Borrower)",
                "warning",
                "Co-borrower employment entries exist but none are marked as Current.",
                "Mark the co-borrower current employer entry type as 'Current' in Encompass (BE0109)",
            ))
        _check_prior_employers("Co-Borrower", coborr_priors, coborr_voe, _voe_refs_all, flags)
        _check_employment_gap("Co-Borrower", coborr_current, coborr_priors, loan_type, flags)

    # ── Does Not Apply checkbox detection: sections 1b / 1c / 1d ────────────
    def _dna_checked(val) -> bool:
        return str(val or "").strip().lower() in ("true", "yes", "y", "1", "checked", "x")

    # ── 2b (URLA Part 2): Employee / Employer income (FE0119 / FE0219) ─────────
    if not _dna_checked(borr_1b_dna):
        if not (borr_base_income or "").strip():
            flags.append(_flag("5.1",
                "Section 2b Empty — 'Does Not Apply' Not Checked (Borrower)",
                "info",
                "Borrower current employment income (Section 2b on 1003 URLA Part 2) is blank and 'Does Not Apply' is not checked.",
                "Enter the base monthly income or check the 'Does Not Apply' box for Section 2b.",
            ))
    if has_coborr and not _dna_checked(coborr_1b_dna):
        if not (coborr_base_income or "").strip():
            flags.append(_flag("5.1",
                "Section 2b Empty — 'Does Not Apply' Not Checked (Co-Borrower)",
                "info",
                "Co-borrower current employment income (Section 2b on 1003 URLA Part 2) is blank and 'Does Not Apply' is not checked.",
                "Enter the co-borrower's base monthly income or check the 'Does Not Apply' box for Section 2b.",
            ))

    # ── 2c (URLA Part 2): Additional / Self-Employment income (FE0302 / FE0402) ──
    # Section 2c (additional/self employment) is commonly genuinely N/A. When the section is
    # empty and DNA isn't checked, auto-check the "Does Not Apply" box (URLA.X201/X202) rather
    # than leaving a manual info flag (per notes: "if any section is empty, click does not apply").
    if not _dna_checked(borr_1c_dna) and not (borr_1c_employer or "").strip():
        _write_fields(loan_id, {"URLA.X201": "true"}, "5.1", flags, state=state,
                      labels={"URLA.X201": "Section 2c 'Does Not Apply' (Borrower)"})
        flags.append(_flag("5.1",
            "Section 2c 'Does Not Apply' Auto-Checked (Borrower)",
            "info-overwrite",
            "Borrower additional employment (Section 2c on 1003 URLA Part 2) is blank — "
            "checked the 'Does Not Apply' box (URLA.X201).",
            "Verify the borrower has no additional/self employment; uncheck if 2c should be filled.",
        ))
    if has_coborr and not _dna_checked(coborr_1c_dna) and not (coborr_1c_employer or "").strip():
        _write_fields(loan_id, {"URLA.X202": "true"}, "5.1", flags, state=state,
                      labels={"URLA.X202": "Section 2c 'Does Not Apply' (Co-Borrower)"})
        flags.append(_flag("5.1",
            "Section 2c 'Does Not Apply' Auto-Checked (Co-Borrower)",
            "info-overwrite",
            "Co-borrower additional employment (Section 2c on 1003 URLA Part 2) is blank — "
            "checked the 'Does Not Apply' box (URLA.X202).",
            "Verify the co-borrower has no additional/self employment; uncheck if 2c should be filled.",
        ))

    # ── 2d (URLA Part 2): Previous Employment income (FE0502 / FE0602) ─────────
    # Section 2d (previous employment) is also commonly genuinely N/A. Same treatment as 2c:
    # auto-check the "Does Not Apply" box (URLA.X203/X204) when empty.
    if not _dna_checked(borr_1d_dna) and not (borr_1d_employer or "").strip():
        _write_fields(loan_id, {"URLA.X203": "true"}, "5.1", flags, state=state,
                      labels={"URLA.X203": "Section 2d 'Does Not Apply' (Borrower)"})
        flags.append(_flag("5.1",
            "Section 2d 'Does Not Apply' Auto-Checked (Borrower)",
            "info-overwrite",
            "Borrower previous employment (Section 2d on 1003 URLA Part 2) is blank — "
            "checked the 'Does Not Apply' box (URLA.X203).",
            "Verify the borrower has no previous employment to report; uncheck if 2d should be filled.",
        ))
    if has_coborr and not _dna_checked(coborr_1d_dna) and not (coborr_1d_employer or "").strip():
        _write_fields(loan_id, {"URLA.X204": "true"}, "5.1", flags, state=state,
                      labels={"URLA.X204": "Section 2d 'Does Not Apply' (Co-Borrower)"})
        flags.append(_flag("5.1",
            "Section 2d 'Does Not Apply' Auto-Checked (Co-Borrower)",
            "info-overwrite",
            "Co-borrower previous employment (Section 2d on 1003 URLA Part 2) is blank — "
            "checked the 'Does Not Apply' box (URLA.X204).",
            "Verify the co-borrower has no previous employment to report; uncheck if 2d should be filled.",
        ))

    # ── Gross income surfacing: 1c and 1d when section is populated ───────────
    def _fmt_income(gross, monthly) -> str:
        parts = []
        if gross:
            parts.append(f"total gross: {gross}")
        if monthly:
            parts.append(f"monthly: {monthly}")
        return ", ".join(parts) if parts else "(not entered)"

    if (borr_1c_employer or "").strip():
        flags.append(_flag("5.1",
            f"Section 2c Income — Borrower ({borr_1c_employer})",
            "info",
            f"2c (additional employment) populated. {_fmt_income(borr_1c_gross, borr_1c_monthly)}.",
            "Verify gross income and monthly amounts match the VOE / self-employment docs.",
            docs=_voe_refs_all,
        ))
    if (coborr_1c_employer or "").strip():
        flags.append(_flag("5.1",
            f"Section 2c Income — Co-Borrower ({coborr_1c_employer})",
            "info",
            f"2c (additional employment) populated. {_fmt_income(coborr_1c_gross, coborr_1c_monthly)}.",
            "Verify co-borrower gross income and monthly amounts match supporting docs.",
            docs=_voe_refs_all,
        ))
    if (borr_1d_employer or "").strip():
        flags.append(_flag("5.1",
            f"Section 2d Income — Borrower ({borr_1d_employer})",
            "info",
            f"2d (previous employment) populated. {_fmt_income(borr_1d_gross, borr_1d_monthly)}.",
            "Verify prior income amounts are consistent with employment history docs.",
            docs=_voe_refs_all,
        ))
    if (coborr_1d_employer or "").strip():
        flags.append(_flag("5.1",
            f"Section 2d Income — Co-Borrower ({coborr_1d_employer})",
            "info",
            f"2d (previous employment) populated. {_fmt_income(coborr_1d_gross, coborr_1d_monthly)}.",
            "Verify co-borrower prior income amounts match employment history docs.",
            docs=_voe_refs_all,
        ))

    # (Employment-gap analysis runs per-applicant above via _check_employment_gap.)

    # ── Rule: Married + Same Employer → copy tenure fields to co-borrower slot ──
    _borr_marital   = (_los(state, "borrower_marital_status") or "").strip().upper()
    _has_coborrower = bool(_los(state, "coborrower_first_name"))

    if _borr_marital == "MARRIED" and _has_coborrower:
        # Identify borrower and co-borrower current slots from pre-fetched BE fields.
        # BE0X08 = "Borrower" or "Co-Borrower"; BE0X09 = "Current" or "Prior".
        _borr_slot = _cobr_slot = None
        for _s in ("01", "02", "03"):
            _voe_for  = (_los(state, f"be{_s}_voe_is_for") or "").replace("-", "").replace(" ", "").lower()
            _emp_type = (_los(state, f"be{_s}_employment_type") or "").lower()
            _emp_name = _los(state, f"be{_s}_employer_name") or ""
            if not _emp_name:
                continue
            if "coborrower" in _voe_for and "current" in _emp_type:
                _cobr_slot = _s
            elif "borrower" in _voe_for and "current" in _emp_type and not _borr_slot:
                _borr_slot = _s

        if _borr_slot and _cobr_slot:
            _borr_emp = _normalize_name(_los(state, f"be{_borr_slot}_employer_name"))
            _cobr_emp = _normalize_name(_los(state, f"be{_cobr_slot}_employer_name"))

            if _borr_emp and _cobr_emp and _borr_emp == _cobr_emp:
                # Same employer — build field ID map using slot digit (01→1, 02→2, 03→3)
                _cd = _cobr_slot[-1]   # co-borrower slot digit
                _copy = {
                    f"BE0{_cd}51": _los(state, f"be{_borr_slot}_date_hired") or "",
                    f"BE0{_cd}13": str(_los(state, f"be{_borr_slot}_years_in_job") or ""),
                    f"BE0{_cd}33": str(_los(state, f"be{_borr_slot}_months_in_job") or ""),
                    f"BE0{_cd}16": str(_los(state, f"be{_borr_slot}_years_in_line_of_work") or ""),
                    f"BE0{_cd}52": str(_los(state, f"be{_borr_slot}_months_in_line_of_work") or ""),
                }
                _copy = {k: v for k, v in _copy.items() if v and v not in ("None", "0", "")}

                if _copy:
                    _write_fields(loan_id, _copy, "5.1", flags, state=state)
                    flags.append(_flag("5.1",
                        "Same-Employer Co-Borrower — Tenure Fields Copied",
                        "info-overwrite",
                        f"Borrower and co-borrower both work at "
                        f"'{_los(state, f'be{_borr_slot}_employer_name')}'. "
                        f"Copied date hired, years/months in job, years/months in line of work "
                        f"from borrower slot {_borr_slot} to co-borrower slot {_cobr_slot}.",
                        "Verify copied tenure values in the VOE form are correct for the co-borrower.",
                    ))
                else:
                    flags.append(_flag("5.1",
                        "Same-Employer Co-Borrower — Borrower Tenure Fields Empty",
                        "warning",
                        f"Borrower and co-borrower both at "
                        f"'{_los(state, f'be{_borr_slot}_employer_name')}' but borrower's "
                        "date hired / tenure fields are all blank.",
                        "Enter borrower hire date, years in job, and years in line of work "
                        "in the VOE form first.",
                    ))

    # ── Build result ──────────────────────────────────────────────────────────
    populated_entries = len(api_entries)
    result = {
        "success": True,
        "substep": "5.1",
        "tool": "review_urla_employment",
        "entries_found": populated_entries,
        "current_employer": current_entry.get("employer_name") if current_entry else None,
        "prior_employers": [e.get("employer_name") for e in prior_entries if e.get("employer_name")],
        "flags_count": len(flags),
        "message": (
            f"Employment Verification completed — {populated_entries} entr{'y' if populated_entries == 1 else 'ies'} reviewed"
            + (f", {len(flags)} flag(s)" if flags else ", no flags")
        ),
    }

    logger.info(f"[REVIEW_URLA_EMPLOYMENT] {result['message']}")

    update = {
        "messages": [ToolMessage(content=json.dumps(result), tool_call_id=tool_call_id)],
    }
    if flags:
        update["flags"] = flags

    return Command(update=update)
