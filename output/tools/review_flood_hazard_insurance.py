"""review_flood_hazard_insurance — Tool for substep 8.1: Review Flood & Hazard Insurance

Step 8 (STEP_08): Flood & Hazard Insurance
Phase: DATA_REVIEW

Reviews the Flood Certificate against Encompass:
  • 12 #2 — flood cert property address vs USPS-validated subject, and flood cert
    borrower name vs the Encompass applicant surname(s).
  • 12 #4 — flood zone designation: classify SFHA (A/V) vs non-hazard (X), confirm
    a flood policy is on file when in an SFHA, and reconcile the extracted zone
    against the Encompass Flood Zone on the Flood Information form (field 541),
    auto-correcting on mismatch/blank when the zone maps to a recognized FEMA
    designation.

Reviews the Hazard Insurance policy (Evidence of Insurance) against Encompass —
checklist section 13, all warn/info (no writes), no-op when no policy on file:
  • 13 #2 loan # on policy   • 13 #3 applicant names   • 13 #4 property address
  • 13 #5 effective date on/before closing (in force through closing)
  • 13 #6 insurable coverage ≥ minimum (loan amount / replacement cost)
  • 13 #7 paid-in-full / due-at-closing (surface premium)
  • 13 #8 deductible vs guideline (≤ 5% of coverage)
  • 13 #9 mortgagee clause present   • 13 #10 rent-loss coverage when investment.

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

from ._helpers import _los, _doc, _doc_all, _write_fields, _relevant_docs, _efolder_present

logger = logging.getLogger(__name__)

SUBSTEP = "8.1"


def _flag(flags, title, severity, details, suggestion, docs=None) -> None:
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
    flags.append(f)


def _is_checked(val) -> bool:
    """Return True if an Encompass checkbox / boolean field is truthy."""
    if val is None:
        return False
    return str(val).strip().lower() in ("y", "yes", "true", "1")


def _norm_addr(val) -> str:
    """Lowercase, punctuation-stripped, single-spaced address for loose compare."""
    return re.sub(r"[^a-z0-9]+", " ", str(val or "").lower()).strip()


def _addr_match(a, b) -> bool:
    """Loose address equality: same house number AND street-name token overlap.

    Returns True when we cannot meaningfully compare (missing data) so a
    mismatch is only raised on a genuine house-number/street disagreement."""
    na, nb = _norm_addr(a), _norm_addr(b)
    if not na or not nb:
        return True
    ta, tb = na.split(), nb.split()
    an = ta[0] if ta else ""
    bn = tb[0] if tb else ""
    if an.isdigit() and bn.isdigit() and an != bn:
        return False
    return len(set(ta) & set(tb)) >= 2


def _sourced_property_address(state: dict, source_hint: str) -> Optional[str]:
    """A ``property_address`` copy whose provenance matches ``source_hint``.

    The ``property_address`` key is shared across the Title Report, Flood
    Certificate, Lock Confirmation, etc., so we only accept the copy whose
    ``source_document`` names the document we want (e.g. 'flood')."""
    for c in _doc_all(state, "property_address"):
        if source_hint in (c.get("source_document") or "").lower() and c.get("value"):
            return c.get("value")
    return None


# Exact dropdown values accepted by Encompass Flood Zone (field 541, Flood
# Information form). Numbered A/V zones collapse to the "A1-A30" / "V1-V30"
# range options the dropdown offers (there are no per-number entries).
_FLOOD_ZONE_ENUM = {
    "A", "A99", "AE", "AH", "AO", "AR", "AR/A", "AR/AE", "AR/AH", "AR/AO",
    "B", "C", "D", "V", "VE", "V0", "X", "X500", "XS", "XU",
    "A1-A30", "V1-V30",
}


def _valid_flood_zone(zone) -> Optional[str]:
    """Map an extracted flood zone to the exact value Encompass field 541 accepts.

    Field 541 is a dropdown (Flood Information form). Returns the matching
    dropdown value (e.g. "AE", "X", "A1-A30", "AR/AE") or None when the extracted
    value is not a recognized designation. Only a value that maps to an
    Encompass-acceptable standard is ever written; anything else keeps the
    warning and is left for manual entry."""
    s = str(zone or "").strip().upper()
    if not s:
        return None
    s = s.split()[0].strip().rstrip(".,").replace(" ", "")
    m = re.match(r"^A0*([1-9]|[12]\d|30)$", s)
    if m:
        return "A1-A30"
    m = re.match(r"^V0*([1-9]|[12]\d|30)$", s)
    if m:
        return "V1-V30"
    return s if s in _FLOOD_ZONE_ENUM else None


_DATE_FORMATS = (
    "%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%m-%d-%Y",
    "%b %d, %Y", "%B %d, %Y", "%Y/%m/%d", "%m/%d/%Y %H:%M:%S",
)


def _parse_date(val):
    """Best-effort date parse across the common LOS/extraction formats."""
    s = str(val or "").strip()
    if not s:
        return None
    s = s.split("T")[0].strip()  # tolerate ISO datetime
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _parse_money(val):
    """Currency-tolerant float parse ('$250,000.00' -> 250000.0); None if empty."""
    s = re.sub(r"[^0-9.]", "", str(val or ""))
    if not s or s == ".":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _digits(val) -> str:
    return re.sub(r"\D", "", str(val or ""))


def _loan_num_match(a, b) -> bool:
    """Loose loan-number equality: equal digit strings, or one is a suffix of the
    other (policies often drop leading zeros / a prefix)."""
    da, db = _digits(a), _digits(b)
    if not da or not db:
        return False
    return da == db or (len(min(da, db, key=len)) >= 5 and (da.endswith(db) or db.endswith(da)))


@tool
def review_flood_hazard_insurance(
    tool_call_id: Annotated[str, InjectedToolCallId],
    state: Annotated[dict, InjectedState],
) -> Command:
    """Review Flood Certificate + Hazard Insurance vs Encompass (checklist 12 + 13).

    Flood (12 #2/#4): flood cert property address vs the USPS-validated subject and
    flood cert borrower name vs the applicant surname(s); flood zone reconciled
    against Encompass field 541 (auto-correct on blank/mismatch for recognized
    FEMA designations) with SFHA insurance-required checks.

    Hazard (13 #2–#10): the Evidence of Insurance policy is cross-checked against
    the loan — loan #, applicant names, property address, effective date vs
    closing, coverage adequacy, deductible, mortgagee clause, and rent-loss
    coverage for investment properties. Warn/info only; no writes.

    Call this tool during STEP_08 (Flood & Hazard Insurance) as substep 8.1.
    """
    loan_id = state.get("loan_id")
    if not loan_id:
        return Command(update={"messages": [ToolMessage(
            content=json.dumps({"error": "No loan_id in state. Run find_loan first."}),
            tool_call_id=tool_call_id,
        )]})

    logger.info(f"[REVIEW_FLOOD_HAZARD_INSURANCE] Starting for loan {str(loan_id)[:8]}...")

    flags: list = []

    # ── Subject property + applicant context ──
    property_address = _los(state, "property_address")
    property_city = _los(state, "property_city")
    property_state = _los(state, "property_state")
    property_zip = _los(state, "property_zip")
    borrower_last_name = _los(state, "borrower_last_name")
    coborrower_last_name = _los(state, "coborrower_last_name")
    los_flood_zone = _los(state, "los_flood_zone")
    addr_val = state.get("address_validation", {})

    # §12.2a — Flood cert property address vs the USPS-validated subject address.
    # The flood cert lists property_address generically, so accept only the copy
    # whose source is the flood cert. Warn-only; never auto-corrected.
    _usps_subject = (addr_val.get("normalized") if addr_val else None) or ", ".join(
        p for p in (property_address, property_city, property_state, property_zip) if p)
    _flood_addr = _sourced_property_address(state, "flood")
    if _usps_subject and _flood_addr:
        if not _addr_match(_flood_addr, _usps_subject):
            _flag(flags, "Flood Certificate Address vs USPS", "warning",
                  f"Flood Certificate subject address ('{_flood_addr}') does not match the "
                  f"USPS-validated subject address ('{_usps_subject}').",
                  "Verify the flood certificate was issued for the correct subject property.",
                  docs=_relevant_docs(state, "property_address", doc_types=["Flood Certificate"]))
        else:
            _flag(flags, "Flood Certificate Address Confirmed", "info",
                  f"Flood Certificate subject address matches the subject property ('{_flood_addr}').",
                  "No action needed — address matches the subject property.")

    # §12.2b — Flood certificate applicant match. Confirm the borrower name(s) on
    # the flood cert overlap the Encompass applicant surname(s). Warn-only.
    _flood_borr = None
    for _c in _doc_all(state, "borrower_name"):
        if "flood" in (_c.get("source_document") or "").lower() and _c.get("value"):
            _flood_borr = _c.get("value")
            break
    if _flood_borr:
        _applicant_last = {
            (borrower_last_name or "").strip().lower(),
            (coborrower_last_name or "").strip().lower(),
        } - {""}
        _flood_low = str(_flood_borr).lower()
        if _applicant_last and not any(ln in _flood_low for ln in _applicant_last):
            _flag(flags, "Flood Cert Applicant Mismatch", "warning",
                  f"Flood certificate borrower name ('{_flood_borr}') does not match the "
                  f"Encompass applicant(s) ({', '.join(sorted(_applicant_last))}).",
                  "Verify the flood certificate was ordered for the correct borrower / loan.",
                  docs=_relevant_docs(state, "borrower_name", doc_types=["Flood Certificate"]))
        else:
            _flag(flags, "Flood Cert Applicant Confirmed", "info",
                  f"Flood certificate borrower name ('{_flood_borr}') matches the Encompass applicant(s).",
                  "No action needed — flood cert names the correct borrower.")

    # §12.4 — Flood zone designation. Classify the flood-cert zone (SFHA A/V vs
    # non-hazard X), confirm flood insurance is on file when in an SFHA, and
    # reconcile the extracted zone against the Encompass Flood Zone (field 541).
    _flood_zone_doc = _doc(state, "flood_zone")
    _in_sfha_doc = _doc(state, "in_sfha")
    _flood_docs = _relevant_docs(state, "flood_zone", "in_sfha",
                                 doc_types=["Flood Certificate"])
    if _flood_zone_doc or _in_sfha_doc is not None:
        _zone_norm = str(_flood_zone_doc or "").strip().upper()
        _in_sfha = _is_checked(_in_sfha_doc) or _zone_norm.startswith(("A", "V"))

        # Doc-vs-LOS flood zone comparison (field 541, Flood Information form).
        # The flood determination is authoritative: when the extracted zone maps
        # to a value Encompass field 541 accepts, auto-correct on blank OR
        # mismatch (the write emits its own info-overwrite audit flag — no
        # warning). A zone that does not map to a standard is left unwritten and
        # warned. Compared against the normalized dropdown value so e.g. cert
        # "A7" ↔ LOS "A1-A30" is treated as a match.
        _valid_zone = _valid_flood_zone(_zone_norm)
        _los_zone_cmp = str(los_flood_zone or "").strip().upper()
        if _zone_norm and los_flood_zone:
            if (_valid_zone or _zone_norm) != _los_zone_cmp:
                if _valid_zone:
                    _write_fields(loan_id=loan_id, updates={"541": _valid_zone},
                                  substep=SUBSTEP, flags=flags, state=state,
                                  labels={"541": "Flood Zone"})
                    _flag(flags, "Flood Zone Corrected", "info",
                          f"Encompass Flood Zone (541) = '{los_flood_zone}' did not match the "
                          f"flood certificate ('{_valid_zone}'); field 541 updated to '{_valid_zone}'.",
                          "No action needed — flood zone set from the flood determination.",
                          docs=_flood_docs)
                else:
                    _flag(flags, "Flood Zone Mismatch", "warning",
                          f"Flood certificate zone ('{_zone_norm}') does not match Encompass Flood "
                          f"Zone (541) = '{los_flood_zone}' and is not a recognized FEMA "
                          f"designation — not auto-corrected.",
                          "Verify the correct flood zone and update Encompass field 541 manually.",
                          docs=_flood_docs)
            else:
                _flag(flags, "Flood Zone Confirmed", "info",
                      f"Flood zone ('{_zone_norm}') matches Encompass Flood Zone (541).",
                      "No action needed — flood zone designation confirmed.")
        elif _zone_norm and not los_flood_zone:
            if _valid_zone:
                _write_fields(loan_id=loan_id, updates={"541": _valid_zone},
                              substep=SUBSTEP, flags=flags, state=state,
                              labels={"541": "Flood Zone"})
                _flag(flags, "Flood Zone Populated", "info",
                      f"Encompass Flood Zone (541) was blank; set to '{_valid_zone}' from the "
                      f"flood determination.",
                      "No action needed — flood zone populated from the flood determination.",
                      docs=_flood_docs)
            else:
                _flag(flags, "Flood Zone Not in Encompass", "warning",
                      f"Flood certificate shows zone '{_zone_norm}' (not a recognized FEMA "
                      f"designation) and Encompass Flood Zone (541) is blank — not auto-populated.",
                      "Enter the correct flood zone into Encompass field 541 manually.",
                      docs=_flood_docs)

        # SFHA → flood insurance required; confirm a flood policy is on file.
        if _in_sfha:
            _flood_ins_present = (
                _efolder_present(state, "Flood Insurance")
                or bool(_doc(state, "flood_policy_number"))
                or bool(_doc(state, "flood_annual_premium"))
            )
            if not _flood_ins_present:
                _flag(flags, "Flood Insurance Required (SFHA)", "warning",
                      f"Property is in a Special Flood Hazard Area (zone '{_zone_norm or 'A/V'}') "
                      f"but no flood insurance policy is on file.",
                      "Obtain a flood insurance policy meeting NFIP coverage requirements.",
                      docs=_flood_docs)
            else:
                _flag(flags, "Flood Insurance on File", "info",
                      f"Property is in an SFHA (zone '{_zone_norm or 'A/V'}') and a flood "
                      f"insurance policy is on file.",
                      "Verify coverage amount meets NFIP / lender requirements.",
                      docs=_flood_docs)
        elif _zone_norm:
            _flag(flags, "Flood Zone Non-Hazard", "info",
                  f"Flood zone '{_zone_norm}' is not a Special Flood Hazard Area — flood "
                  f"insurance is not required.",
                  "No action needed — confirm the zone matches the appraisal / determination.",
                  docs=_flood_docs)

    # ── §13 Hazard Insurance review (Evidence of Insurance) ──
    # Every input comes from the extracted Evidence of Insurance policy; this block
    # is warn/info only (no writes) and a no-op when no policy is on file / extracted.
    _hoi_present = (
        _efolder_present(state, "Evidence of Insurance")
        or _is_checked(_doc(state, "hazard_insurance_present"))
        or bool(_doc(state, "policy_number"))
        or bool(_doc(state, "hazard_insurance_coverage"))
    )
    if _hoi_present:
        _hoi_docs = _relevant_docs(state, "insured_name", "policy_number",
                                   "hazard_insurance_coverage",
                                   doc_types=["Evidence of Insurance"])
        loan_number = _los(state, "loan_number")
        loan_amount = _parse_money(_los(state, "loan_amount"))
        occupancy = str(_los(state, "occupancy") or "").strip().lower()
        closing_dt = _parse_date(_los(state, "closing_date") or _los(state, "borrower_est_closing_date"))

        insured_name = _doc(state, "insured_name")
        insured_location = _doc(state, "insured_location") or _doc(state, "insured_mailing_address")
        mortgagee_loan_number = _doc(state, "mortgagee_loan_number")
        mortgagee_name = _doc(state, "mortgagee_name")
        cov_start = _parse_date(_doc(state, "coverage_start_date"))
        cov_end = _parse_date(_doc(state, "coverage_end_date"))
        coverage_amt = _parse_money(_doc(state, "hazard_insurance_coverage"))
        replacement_cost = _parse_money(_doc(state, "replacement_cost"))
        premium = _parse_money(_doc(state, "hazard_insurance_premium"))
        deductible = _parse_money(_doc(state, "deductible"))
        loss_of_use = _doc(state, "loss_of_use_coverage")

        # 13 #2 — Loan number shown on the policy matches the loan.
        if loan_number and mortgagee_loan_number:
            if _loan_num_match(mortgagee_loan_number, loan_number):
                _flag(flags, "Hazard Policy Loan # Confirmed", "info",
                      f"Loan number on the hazard policy ('{mortgagee_loan_number}') matches the loan.",
                      "No action needed — loan number matches.", docs=_hoi_docs)
            else:
                _flag(flags, "Hazard Policy Loan # Mismatch", "warning",
                      f"Loan number on the hazard policy ('{mortgagee_loan_number}') does not match "
                      f"the Encompass loan number ('{loan_number}').",
                      "Have the insurance agent correct the loan number on the policy / mortgagee clause.",
                      docs=_hoi_docs)
        elif loan_number and not mortgagee_loan_number:
            _flag(flags, "Hazard Policy Missing Loan #", "warning",
                  "The hazard policy does not show the loan number in the mortgagee clause.",
                  "Request an updated policy / evidence of insurance that lists the loan number.",
                  docs=_hoi_docs)

        # 13 #3 — Applicant name(s) on the policy.
        if insured_name:
            _applicant_last = {
                (_los(state, "borrower_last_name") or "").strip().lower(),
                (_los(state, "coborrower_last_name") or "").strip().lower(),
            } - {""}
            if _applicant_last and not any(ln in str(insured_name).lower() for ln in _applicant_last):
                _flag(flags, "Hazard Policy Applicant Mismatch", "warning",
                      f"Insured name on the hazard policy ('{insured_name}') does not match the "
                      f"Encompass applicant(s) ({', '.join(sorted(_applicant_last))}).",
                      "Verify the policy names the borrower(s); request a corrected policy if needed.",
                      docs=_hoi_docs)
            else:
                _flag(flags, "Hazard Policy Applicant Confirmed", "info",
                      f"Insured name on the hazard policy ('{insured_name}') matches the applicant(s).",
                      "No action needed — policy names the correct borrower(s).", docs=_hoi_docs)

        # 13 #4 — Property address on the policy matches the subject.
        if insured_location and _usps_subject:
            if not _addr_match(insured_location, _usps_subject):
                _flag(flags, "Hazard Policy Address Mismatch", "warning",
                      f"Property address on the hazard policy ('{insured_location}') does not match "
                      f"the subject property ('{_usps_subject}').",
                      "Verify the policy was issued for the subject property; request correction if needed.",
                      docs=_hoi_docs)
            else:
                _flag(flags, "Hazard Policy Address Confirmed", "info",
                      f"Property address on the hazard policy matches the subject ('{insured_location}').",
                      "No action needed — policy covers the subject property.", docs=_hoi_docs)

        # 13 #5 — Effective date on/before closing and in force through closing.
        if closing_dt and cov_start and cov_start > closing_dt:
            _flag(flags, "Hazard Policy Effective After Closing", "warning",
                  f"Hazard policy effective date ({cov_start.isoformat()}) is after the estimated "
                  f"closing date ({closing_dt.isoformat()}).",
                  "Obtain coverage effective on or before the note/closing date.", docs=_hoi_docs)
        elif closing_dt and cov_end and cov_end < closing_dt:
            _flag(flags, "Hazard Policy Expires Before Closing", "warning",
                  f"Hazard policy expiration ({cov_end.isoformat()}) is before the estimated closing "
                  f"date ({closing_dt.isoformat()}).",
                  "Obtain a policy that remains in force through the note/closing date.", docs=_hoi_docs)
        elif cov_start or cov_end:
            _flag(flags, "Hazard Policy Effective Dates", "info",
                  f"Hazard policy period: {cov_start.isoformat() if cov_start else '?'} → "
                  f"{cov_end.isoformat() if cov_end else '?'}.",
                  "Confirm the policy is effective on/before closing and in force through closing.",
                  docs=_hoi_docs)

        # 13 #6 — Insurable coverage meets the minimum (loan amount or replacement cost).
        if coverage_amt is not None and (loan_amount or replacement_cost):
            _meets = (loan_amount and coverage_amt >= loan_amount) or \
                     (replacement_cost and coverage_amt >= replacement_cost)
            if _meets:
                _flag(flags, "Hazard Coverage Adequate", "info",
                      f"Dwelling coverage (${coverage_amt:,.0f}) meets the minimum "
                      f"(loan ${loan_amount:,.0f}" + (f" / replacement ${replacement_cost:,.0f}" if replacement_cost else "") + ").",
                      "No action needed — coverage meets the lender minimum.", docs=_hoi_docs)
            else:
                _flag(flags, "Hazard Coverage May Be Insufficient", "warning",
                      f"Dwelling coverage (${coverage_amt:,.0f}) is below the loan amount "
                      f"(${loan_amount:,.0f})" + (f" and the replacement cost (${replacement_cost:,.0f})" if replacement_cost else "") + ".",
                      "Confirm coverage meets the lesser of loan amount or 100% replacement cost.",
                      docs=_hoi_docs)

        # 13 #7 — Premium paid-in-full / due-at-closing (surface for confirmation).
        if premium is not None:
            _flag(flags, "Hazard Premium", "info",
                  f"Annual hazard premium is ${premium:,.0f}.",
                  "Confirm the premium is paid in full (or shown due at closing on the CD).",
                  docs=_hoi_docs)

        # 13 #8 — Deductible within guideline (≤ 5% of dwelling coverage).
        if deductible is not None and coverage_amt:
            if deductible > 0.05 * coverage_amt:
                _flag(flags, "Hazard Deductible Exceeds Guideline", "warning",
                      f"Deductible (${deductible:,.0f}) exceeds 5% of dwelling coverage "
                      f"(${coverage_amt:,.0f}).",
                      "Confirm the deductible is within program guidelines (typically ≤ 5% of coverage).",
                      docs=_hoi_docs)
            else:
                _flag(flags, "Hazard Deductible OK", "info",
                      f"Deductible (${deductible:,.0f}) is within 5% of dwelling coverage.",
                      "No action needed — deductible within guideline.", docs=_hoi_docs)

        # 13 #9 — Mortgagee clause present on the policy.
        if mortgagee_name:
            _flag(flags, "Mortgagee Clause Present", "info",
                  f"Policy shows a mortgagee clause ('{mortgagee_name}').",
                  "Verify the mortgagee clause matches the lender's standard language.",
                  docs=_hoi_docs)
        else:
            _flag(flags, "Mortgagee Clause Missing", "warning",
                  "The hazard policy does not show a mortgagee clause.",
                  "Request an updated policy showing the lender's standard mortgagee clause.",
                  docs=_hoi_docs)

        # 13 #10 — Rent-loss (loss of use) coverage when the subject is investment.
        if "invest" in occupancy:
            if not loss_of_use:
                _flag(flags, "Rent-Loss Coverage Missing (Investment)", "warning",
                      "Subject is an investment property but the hazard policy shows no rent-loss "
                      "(loss-of-use / fair-rental-value) coverage.",
                      "When rental income is used, obtain rent-loss coverage per program guidelines.",
                      docs=_hoi_docs)
            else:
                _flag(flags, "Rent-Loss Coverage Present", "info",
                      f"Investment property hazard policy shows rent-loss / loss-of-use coverage ('{loss_of_use}').",
                      "Confirm the amount meets program requirements.", docs=_hoi_docs)

    # ── Build result ──
    result = {
        "success": True,
        "substep": SUBSTEP,
        "tool": "review_flood_hazard_insurance",
        "flags_count": len(flags),
        "message": (
            "Flood & Hazard Insurance review complete"
            + (f" — {len(flags)} flag(s) raised" if flags else "")
        ),
    }

    logger.info(f"[REVIEW_FLOOD_HAZARD_INSURANCE] {result['message']}")

    update = {
        "messages": [ToolMessage(content=json.dumps(result), tool_call_id=tool_call_id)],
    }
    if flags:
        update["flags"] = flags

    return Command(update=update)
