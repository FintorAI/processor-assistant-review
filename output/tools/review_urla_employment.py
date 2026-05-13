"""review_urla_employment — Tool for substep 4.1: Employment Verification (1b VOE)

Step 4 (STEP_04): 1003 URLA Page 2
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

from ._helpers import _los, _doc, _profile

logger = logging.getLogger(__name__)

# ── Helpers ────────────────────────────────────────────────────────────────────

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


def _normalize_name(v: Optional[str]) -> str:
    """Lowercase, strip punctuation for fuzzy employer name comparison."""
    if not v:
        return ""
    return re.sub(r"[^a-z0-9 ]", "", v.lower()).strip()


def _parse_amount(v: Optional[str]) -> Optional[float]:
    """Parse dollar string like '$7,500.00' or '7500' to float."""
    if not v:
        return None
    cleaned = re.sub(r"[^0-9.]", "", v)
    try:
        return float(cleaned)
    except ValueError:
        return None


def _total_months(years_str: Optional[str], months_str: Optional[str]) -> Optional[int]:
    """Convert years+months strings to total months. Returns None if both empty."""
    y = int(years_str) if years_str and years_str.strip().isdigit() else None
    m = int(months_str) if months_str and months_str.strip().isdigit() else None
    if y is None and m is None:
        return None
    return (y or 0) * 12 + (m or 0)


# ── Employment entry parser ────────────────────────────────────────────────────

def _read_entry(state: dict, prefix: str) -> dict:
    """Read all BE fields for a given entry prefix (e.g. 'be01')."""
    return {
        "employment_type":    _los(state, f"{prefix}_employment_type"),   # BE0x09
        "voe_is_for":         _los(state, f"{prefix}_voe_is_for"),        # BE0x08
        "foreign_address":    _los(state, f"{prefix}_foreign_address"),   # BE0x80
        "employer_name":      _los(state, f"{prefix}_employer_name"),     # BE0x02
        "employer_phone":     _los(state, f"{prefix}_employer_phone"),    # BE0x17
        "employer_street":    _los(state, f"{prefix}_employer_street"),   # BE0x60
        "employer_unit_type": _los(state, f"{prefix}_employer_unit_type"),# BE0x58
        "employer_unit_num":  _los(state, f"{prefix}_employer_unit_number"),# BE0x59
        "employer_city":      _los(state, f"{prefix}_employer_city"),     # BE0x05
        "employer_state":     _los(state, f"{prefix}_employer_state"),    # BE0x06
        "employer_zip":       _los(state, f"{prefix}_employer_zip"),      # BE0x07
        "position_title":     _los(state, f"{prefix}_position_title"),    # BE0x10
        "date_hired":         _los(state, f"{prefix}_date_hired"),        # BE0x51
        "date_terminated":    _los(state, f"{prefix}_date_terminated"),   # BE0x14
        "years_in_job":       _los(state, f"{prefix}_years_in_job"),      # BE0x13
        "months_in_job":      _los(state, f"{prefix}_months_in_job"),     # BE0x33
        "years_in_line_of_work":  _los(state, f"{prefix}_years_in_line_of_work"), # BE0x16
        "months_in_line_of_work": _los(state, f"{prefix}_months_in_line_of_work"),# BE0x52
        "monthly_base_pay":   _los(state, f"{prefix}_monthly_base_pay"), # BE0x19
        "authorization_printed": _los(state, f"{prefix}_authorization_printed") if prefix == "be01" else None,  # BE0236 only on entry 1
    }


def _entry_populated(entry: dict) -> bool:
    """True if at least employer_name or employment_type is set."""
    return bool(entry.get("employer_name") or entry.get("employment_type"))


# ── Main tool ─────────────────────────────────────────────────────────────────

@tool
def review_urla_employment(
    tool_call_id: Annotated[str, InjectedToolCallId],
    state: Annotated[dict, InjectedState],
) -> Command:
    """Cross-check employment fields against VOE using BE01xx/BE02xx/BE03xx entries.

    For each populated employment entry:
    - Reads BE0x09 (employment type) to determine if Current or Prior.
    - Verifies LOS employment fields against the corresponding VOE doc fields
      (current_ prefix for Current employer, previous_ prefix for Prior employer).
    - Checks authorization checkbox (BE0236), date terminated logic, and
      employment duration for gap analysis.

    Call this tool during STEP_04 (1003 URLA Page 2) as substep 4.1.
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

    # ── Read all three employment entries ─────────────────────────────────────
    entries = {
        "be01": _read_entry(state, "be01"),
        "be02": _read_entry(state, "be02"),
        "be03": _read_entry(state, "be03"),
    }

    # ── Guard: no VOE rows created at all ─────────────────────────────────────
    # This mirrors the Encompass API behaviour where fetching /voes returns a
    # "collection does not exist" error when the form has never been filled in.
    # At the LOS field level, all BE01xx/BE02xx/BE03xx fields will be empty.
    if not any(_entry_populated(e) for e in entries.values()):
        flags.append(_flag(
            "4.1",
            "VOE Form Not Populated in Encompass",
            "blocking",
            "No employment entries found (BE0102/BE0209 all empty). "
            "The VOE form has not been filled in — equivalent to the Encompass API "
            "returning 'collection does not exist' for /applications/{id}/voes.",
            "Open the VOE form in Encompass and add the borrower's current (and prior, if < 2 years) employer entries.",
        ))
        result = {
            "success": False,
            "substep": "4.1",
            "tool": "review_urla_employment",
            "entries_found": 0,
            "flags_count": len(flags),
            "message": "Employment Verification blocked — no VOE entries in Encompass",
        }
        return Command(update={
            "flags": flags,
            "messages": [ToolMessage(content=json.dumps(result), tool_call_id=tool_call_id)],
        })

    # ── Identify current and prior entries ────────────────────────────────────
    # BE0109 values: "Current" or "Prior" (Encompass uses these exact labels)
    current_entry = None
    prior_entries = []

    for key, e in entries.items():
        if not _entry_populated(e):
            continue
        emp_type = (e.get("employment_type") or "").strip()
        if not emp_type:
            flags.append(_flag("4.1",
                "Employment Type Not Set (BE0109)",
                "blocking",
                f"Entry {key.upper()} has employer '{e.get('employer_name')}' but BE0109 (employment type) is empty.",
                "Set employment type to Current or Prior in Encompass",
            ))
            continue
        if emp_type.lower() == "current":
            if current_entry:
                # Multiple current entries — flag but use first
                flags.append(_flag("4.1",
                    "Multiple Current Employers",
                    "warning",
                    f"Both {list(entries.keys())[0].upper()} and {key.upper()} are marked Current.",
                    "Verify only one entry should be Current; mark additional entries as Prior if applicable",
                ))
            else:
                current_entry = e
        elif emp_type.lower() in ("prior", "previous"):
            prior_entries.append(e)

    # ── Read VOE doc fields ───────────────────────────────────────────────────
    # We read both current_ and previous_ from the extracted VOE doc
    voe_cur_name       = _doc(state, "current_employer_name")
    voe_cur_phone      = _doc(state, "current_employer_phone")
    voe_cur_street     = _doc(state, "current_employer_street")
    voe_cur_city       = _doc(state, "current_employer_city")
    voe_cur_state      = _doc(state, "current_employer_state")
    voe_cur_zip        = _doc(state, "current_employer_zip")
    voe_cur_hire       = _doc(state, "current_original_hire_date")
    voe_cur_terminated = _doc(state, "current_date_terminated")
    voe_cur_years      = _doc(state, "current_years_in_job")
    voe_cur_months     = _doc(state, "current_months_in_job")
    voe_cur_yrs_line   = _doc(state, "current_years_in_line_of_work")
    voe_cur_mos_line   = _doc(state, "current_months_in_line_of_work")
    voe_cur_base_pay   = _doc(state, "current_monthly_base_pay")
    voe_cur_position   = _doc(state, "current_position_title")
    voe_cur_auth       = _doc(state, "authorization_printed")

    # ── Section 1b income totals + Does Not Apply checkboxes ─────────────
    borr_base_income        = _los(state, "borr_base_monthly_income")      # FE0119
    coborr_base_income      = _los(state, "coborr_base_monthly_income")    # FE0219
    borr_dna                = _los(state, "borr_income_does_not_apply")    # URLA.X201
    coborr_dna              = _los(state, "coborr_income_does_not_apply")  # URLA.X202

    voe_prev_position  = _doc(state, "previous_position_title")
    voe_prev_name      = _doc(state, "previous_employer_name")
    voe_prev_hire      = _doc(state, "previous_original_hire_date")
    voe_prev_terminated= _doc(state, "previous_date_terminated")
    voe_prev_years     = _doc(state, "previous_years_in_job")
    voe_prev_months    = _doc(state, "previous_months_in_job")
    voe_prev_base_pay  = _doc(state, "previous_monthly_base_pay")

    # ── Current employer cross-checks ─────────────────────────────────────────
    if current_entry:
        e = current_entry

        # VOE is for — must be Borrower or Co-Borrower
        voe_is_for = (e.get("voe_is_for") or "").strip()
        if voe_is_for and voe_is_for not in ("Borrower", "Co-Borrower"):
            flags.append(_flag("4.1",
                "VOE Is For — Invalid Value",
                "warning",
                f"BE0108 (VOE is for) has unexpected value '{voe_is_for}'. Expected Borrower or Co-Borrower.",
                "Correct the 'VOE is for' field in Encompass",
            ))

        # Authorization checkbox (BE0236) — must be checked for current employer
        auth_val = (e.get("authorization_printed") or "").strip().lower()
        if auth_val in ("false", "no", "0", "unchecked", ""):
            if auth_val == "":
                # May not be populated — flag as advisory
                flags.append(_flag("4.1",
                    "Authorization Checkbox Not Verified (BE0236)",
                    "warning",
                    "BE0236 (Print see attached borrower's authorization) could not be confirmed as checked.",
                    "Verify the authorization checkbox is checked in Encompass for the current employer",
                ))
            else:
                flags.append(_flag("4.1",
                    "Authorization Checkbox Not Checked (BE0236)",
                    "warning",
                    "BE0236 is unchecked. The 'see attached borrower's authorization' reference will not print on the signature line.",
                    "Check the authorization checkbox in Encompass for the current employer",
                ))

        # Employer name
        if voe_cur_name:
            los_name = _normalize_name(e.get("employer_name"))
            doc_name = _normalize_name(voe_cur_name)
            if los_name and doc_name and los_name != doc_name:
                flags.append(_flag("4.1",
                    "Employer Name Mismatch (VOE vs 1003)",
                    "warning",
                    f"LOS: '{e.get('employer_name')}' | VOE: '{voe_cur_name}'",
                    "Correct the employer name in Encompass to match the VOE",
                ))
        elif not e.get("employer_name"):
            flags.append(_flag("4.1",
                "Current Employer Name Missing",
                "warning",
                "BE0102 (employer name) is empty for the Current employment entry.",
                "Enter the current employer name in Encompass",
            ))

        # Position / title / type of business (BE0110) — cross-check vs VOE
        if voe_cur_position and e.get("position_title"):
            if _normalize_name(e["position_title"]) != _normalize_name(voe_cur_position):
                flags.append(_flag("4.1",
                    "Position Title Mismatch — Current (VOE vs 1003)",
                    "warning",
                    f"LOS: '{e['position_title']}' | VOE: '{voe_cur_position}'",
                    "Correct the position/title in Encompass to match the VOE",
                ))

        # Employer phone — presence only
        if not e.get("employer_phone"):
            flags.append(_flag("4.1",
                "Current Employer Phone Missing (BE0117)",
                "warning",
                "BE0117 (employer phone) is empty for the current employer.",
                "Enter the current employer phone number in Encompass",
            ))

        # Date hired vs VOE
        if voe_cur_hire and e.get("date_hired"):
            if e["date_hired"].strip() != voe_cur_hire.strip():
                flags.append(_flag("4.1",
                    "Hire Date Mismatch — Current (VOE vs 1003)",
                    "warning",
                    f"LOS hire date: '{e['date_hired']}' | VOE original hire date: '{voe_cur_hire}'",
                    "Correct the hire date in Encompass to match the VOE",
                ))

        # Date terminated — must be null/empty for current employer
        if e.get("date_terminated"):
            flags.append(_flag("4.1",
                "Date Terminated Populated for Current Employer (BE0114)",
                "warning",
                f"BE0114 shows termination date '{e['date_terminated']}' but employment type is Current.",
                "Clear the termination date for the current employer in Encompass",
            ))

        # Monthly base pay vs VOE
        if voe_cur_base_pay or e.get("monthly_base_pay"):
            los_pay = _parse_amount(e.get("monthly_base_pay"))
            doc_pay = _parse_amount(voe_cur_base_pay)
            if los_pay is not None and doc_pay is not None:
                if abs(los_pay - doc_pay) > 1.00:  # allow $1 rounding tolerance
                    flags.append(_flag("4.1",
                        "Monthly Base Pay Mismatch — Current (VOE vs 1003)",
                        "warning",
                        f"LOS: ${los_pay:,.2f} | VOE: ${doc_pay:,.2f}",
                        "Reconcile the monthly base pay figure with the VOE",
                    ))

        # Employment duration — trigger gap check if < 2 years total
        total_mos = _total_months(e.get("years_in_job"), e.get("months_in_job"))
        if total_mos is not None and total_mos < 24:
            if not prior_entries:
                flags.append(_flag("4.1",
                    "Employment History Gap — No Prior Employer (< 2 Years Current)",
                    "warning",
                    f"Current employment is {total_mos} months (< 2 years) and no prior employer entry found.",
                    "Add prior employment history entries in Encompass to document 2-year history",
                ))

    else:
        # No current employer found among populated entries
        if any(_entry_populated(e) for e in entries.values()):
            flags.append(_flag("4.1",
                "No Current Employer Entry Found",
                "warning",
                "Employment entries exist but none are marked as Current.",
                "Mark the current employer entry type as 'Current' in Encompass (BE0109)",
            ))

    # ── Prior employer cross-checks ───────────────────────────────────────────
    for e in prior_entries:
        # Employer name vs VOE previous
        if voe_prev_name and e.get("employer_name"):
            los_name = _normalize_name(e["employer_name"])
            doc_name = _normalize_name(voe_prev_name)
            if los_name and doc_name and los_name != doc_name:
                flags.append(_flag("4.1",
                    "Employer Name Mismatch — Prior (VOE vs 1003)",
                    "warning",
                    f"LOS prior employer: '{e['employer_name']}' | VOE previous: '{voe_prev_name}'",
                    "Correct the prior employer name in Encompass to match the VOE",
                ))

        # Date terminated must be populated for prior employer
        if not e.get("date_terminated"):
            flags.append(_flag("4.1",
                "Date Terminated Missing for Prior Employer (BE0114)",
                "warning",
                f"Employment entry for '{e.get('employer_name', 'unknown')}' is Prior but date terminated is empty.",
                "Enter the termination date for the prior employer in Encompass",
            ))

        # Monthly base pay vs VOE previous
        if voe_prev_base_pay or e.get("monthly_base_pay"):
            los_pay = _parse_amount(e.get("monthly_base_pay"))
            doc_pay = _parse_amount(voe_prev_base_pay)
            if los_pay is not None and doc_pay is not None:
                if abs(los_pay - doc_pay) > 1.00:
                    flags.append(_flag("4.1",
                        "Monthly Base Pay Mismatch — Prior (VOE vs 1003)",
                        "warning",
                        f"LOS: ${los_pay:,.2f} | VOE previous: ${doc_pay:,.2f}",
                        "Reconcile prior employer base pay with the VOE",
                    ))

    # ── Section 1b: FE0119 / FE0219 base monthly income + URLA.X201/X202 ────────
    # Borrower
    borr_dna_checked = str(borr_dna or "").strip().lower() in ("true", "yes", "1", "checked")
    if not borr_dna_checked:
        if not borr_base_income or not borr_base_income.strip():
            flags.append(_flag("4.1",
                "Borrower Base Monthly Income Missing (FE0119)",
                "warning",
                "FE0119 (borrower base monthly income, Section 1b) is empty and URLA.X201 (does not apply) is not checked.",
                "Enter the borrower's base monthly income or check the 'Does Not Apply' box (URLA.X201)",
            ))

    # Co-borrower — only check if a co-borrower entry exists in the loan
    coborr_dna_checked = str(coborr_dna or "").strip().lower() in ("true", "yes", "1", "checked")
    has_coborr = any(
        (e.get("voe_is_for") or "").strip().lower() == "co-borrower"
        for e in entries.values() if _entry_populated(e)
    )
    if has_coborr and not coborr_dna_checked:
        if not coborr_base_income or not coborr_base_income.strip():
            flags.append(_flag("4.1",
                "Co-Borrower Base Monthly Income Missing (FE0219)",
                "warning",
                "FE0219 (co-borrower base monthly income, Section 1b) is empty and URLA.X202 (does not apply) is not checked.",
                "Enter the co-borrower's base monthly income or check the 'Does Not Apply' box (URLA.X202)",
            ))

    # ── Employment gap checks ─────────────────────────────────────────────────
    if current_entry and prior_entries:
        cur_hire = current_entry.get("date_hired")
        for prior in prior_entries:
            prior_term = prior.get("date_terminated")
            if cur_hire and prior_term:
                try:
                    fmt_options = ["%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y"]
                    d_hire = d_term = None
                    for fmt in fmt_options:
                        try:
                            d_hire = datetime.strptime(cur_hire.strip(), fmt)
                            d_term = datetime.strptime(prior_term.strip(), fmt)
                            break
                        except ValueError:
                            continue
                    if d_hire and d_term:
                        gap_days = (d_hire - d_term).days
                        if gap_days > 30:
                            gap_months = gap_days // 30
                            if gap_months < 6:
                                if loan_type.upper() == "FHA":
                                    flags.append(_flag("4.1",
                                        "FHA Employment Gap — Explanation Required",
                                        "warning",
                                        f"Gap of ~{gap_months} month(s) between prior termination ({prior_term}) and current hire ({cur_hire}). FHA requires explanation for gaps < 6 months.",
                                        "Obtain a written explanation letter from the borrower for the employment gap",
                                    ))
                            else:
                                flags.append(_flag("4.1",
                                    "Employment Gap > 6 Months — Documentation Required",
                                    "warning",
                                    f"Gap of ~{gap_months} month(s) between prior termination ({prior_term}) and current hire ({cur_hire}). Requires documented 2-year history.",
                                    "Document the 2-year employment history before the gap and verify income continuity",
                                ))
                except Exception:
                    pass

    # ── Build result ──────────────────────────────────────────────────────────
    populated_entries = sum(1 for e in entries.values() if _entry_populated(e))
    result = {
        "success": True,
        "substep": "4.1",
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
