"""update_hud_addendum — Tool for substep 3.2: Complete 1003 HUD Addendum

Step 3 (STEP_03): 1003 URLA Lender
Phase: DATA_REVIEW

Verify and auto-write the 1003 HUD Addendum for FHA and VA loans:
  - Agency Type (1711), Lender ID Code (1059), Section of the Act (1039, FHA),
    Part IV 22a (900), and 25(2) Occupancy (1065).
  - Flags Agency Case # (1040), appraised value (356), and Part IV 25 fields
    that require manual completion.

No-op when loan_type is not FHA or VA.

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

from ._helpers import _los, _profile, _write_fields

logger = logging.getLogger(__name__)

SUBSTEP = "3.2"
VA_LENDER_ID = "9495690000"

_FHA_OFFICE_IDS = [
    ("7771800008", "8345 W SUNSET ROAD STE 380", "LAS VEGAS", "NV", "89113"),
    ("7771800361", "2550 W UNION HILLS DR STE 350", "PHOENIX", "AZ", "85027"),
    ("7771800378", "3715 W ANTHEM WAY SUITE 110", "ANTHEM", "AZ", "85086"),
    ("7771800332", "10413 11TH STREET CT E", "EDGEWOOD", "WA", "98372"),
    ("7771800300", "295 HOLCOMB AVE STE 250", "RENO", "NV", "89502"),
    ("7771800312", "16420 N 92ND ST SUITE E105", "SCOTTSDALE", "AZ", "85260"),
    ("7771800303", "19015 N CREEK PKWY STE 101", "BOTHELL", "WA", "98011"),
    ("7771800355", "7501 TULE SPRINGS RD", "LAS VEGAS", "NV", "89131"),
    ("7771800264", "8000 FAIR OAKS PKWY STE 102", "FAIR OAKS RANCH", "TX", "78015"),
    ("7771800020", "2275 CORPORATE CIRCLE STE 280", "HENDERSON", "NV", "89074"),
    ("7771800145", "1118 12TH ST SE", "SALEM", "OR", "97302"),
    ("7771800253", "100 COREY AVE", "ST PETE BEACH", "FL", "33706"),
    ("7771800122", "874 4TH ST 2ND FL D2 SUITE 2", "SAN RAFAEL", "CA", "94901"),
    ("7771800411", "41593 WINCHESTER RD SUITE 301", "TEMECULA", "CA", "92590"),
    ("7771800405", "101 E EAGLE GLEN LANE", "EAGLE", "ID", "83616"),
    ("7771800390", "10777 W TWAIN AVE STE 220", "LAS VEGAS", "NV", "89135"),
    ("7771800180", "4835 E CACTUS ROAD STE 333", "SCOTTSDALE", "AZ", "85254"),
    ("7771800072", "7800 E UNION AVE STE 920", "DENVER", "CO", "80237"),
    ("7771800384", "5325 RENO CORPORATE DR", "RENO", "NV", "89511"),
]

OCC_WILL_OCCUPY = "ActuallyOccupyPropertyWithin60DaysContinueAtLeast1Year"
OCC_NOW_OCCUPY = "ActuallyOccupyPropertyAsMyHome"
OCC_NOT_OCCUPY = "DoNotIntendToOccupyPropertyAsMyHome"

OCC_LABELS = {
    OCC_WILL_OCCUPY: "Will occupy within 60 days (purchase/primary)",
    OCC_NOW_OCCUPY: "Currently occupies as home (refi/primary)",
    OCC_NOT_OCCUPY: "Does not intend to occupy (investment)",
}

_FIELD_LABELS = {
    "1711": "Agency Type",
    "1059": "Lender ID Code",
    "1039": "Section of the Act",
    "900": "22a - Own/Sold Other Real Estate",
    "1065": "25(2) Occupancy",
}


def _field_status(val: str, suffix: str = "") -> str:
    if not val:
        return "is blank"
    return f"= '{val}'{suffix}"


def _flag(title, severity, details, suggestion="", resolved=False):
    return {
        "substep": SUBSTEP,
        "title": title,
        "severity": severity,
        "details": details,
        "suggestion": suggestion,
        "resolved": resolved,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def _normalize_fha_address(addr: str) -> str:
    s = (addr or "").upper()
    s = s.replace(".", "").replace(",", "")
    s = s.replace("#", "STE ")
    s = re.sub(r"\bSTREET\b", "ST", s)
    s = re.sub(r"\bROAD\b", "RD", s)
    s = re.sub(r"\bAVENUE\b", "AVE", s)
    s = re.sub(r"\bDRIVE\b", "DR", s)
    s = re.sub(r"\bSUITE\b", "STE", s)
    s = re.sub(r"\bPARKWAY\b", "PKWY", s)
    s = re.sub(r"\bPKWAY\b", "PKWY", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _lookup_fha_office_id(branch_address: str) -> tuple[str, bool]:
    norm_input = _normalize_fha_address(branch_address)
    input_tokens = norm_input.split()
    input_street_num = input_tokens[0] if input_tokens else ""

    for office_id, street, _city, _state, _zipcode in _FHA_OFFICE_IDS:
        norm_list = _normalize_fha_address(street)
        list_tokens = norm_list.split()
        list_street_num = list_tokens[0] if list_tokens else ""
        if input_street_num and input_street_num == list_street_num:
            matching = sum(1 for t in list_tokens if t in input_tokens)
            if matching >= min(3, len(list_tokens)):
                return office_id, True

    return _FHA_OFFICE_IDS[0][0], False


def _detect_gov_loan_type(state: dict) -> str | None:
    los_lt = str(_los(state, "loan_type") or "").upper()
    prof_lt = str(_profile(state, "loan_type") or "").upper()
    combined = los_lt or prof_lt
    if "FHA" in combined:
        return "FHA"
    if "VA" in combined:
        return "VA"
    return None


def _expected_occupancy(state: dict) -> tuple[str | None, str]:
    occupancy_loan = str(_los(state, "occupancy") or "").strip()
    loan_purpose = str(_los(state, "loan_purpose") or "").strip()
    has_coborrower = bool(_los(state, "coborrower_first_name"))

    occ_loan_upper = occupancy_loan.upper()
    purpose_upper = loan_purpose.upper()

    is_purchase = "PURCHASE" in purpose_upper or purpose_upper == "1"
    is_refi = "REFI" in purpose_upper or "REFINANCE" in purpose_upper
    is_primary = (
        "PRIMARY" in occ_loan_upper
        or occ_loan_upper == "P"
        or "PRIMARYRESIDENCE" in occ_loan_upper.replace(" ", "")
    )
    is_investment = "INVESTMENT" in occ_loan_upper or occ_loan_upper == "I"

    if is_investment:
        return OCC_NOT_OCCUPY, "Investment property — borrower does not intend to occupy"
    if is_purchase and is_primary:
        suffix = ", co-borrower present" if has_coborrower else ""
        return OCC_WILL_OCCUPY, f"Purchase loan, primary residence{suffix}"
    if is_refi and is_primary:
        return OCC_NOW_OCCUPY, "Refinance, primary residence — borrower already occupies"
    if is_primary:
        return OCC_WILL_OCCUPY, f"Primary residence (loan purpose: '{loan_purpose or 'unknown'}')"
    return None, ""


@tool
def update_hud_addendum(
    tool_call_id: Annotated[str, InjectedToolCallId],
    state: Annotated[dict, InjectedState],
) -> Command:
    """Complete the 1003 HUD Addendum for FHA and VA loans.

    Auto-writes Agency Type (1711), Lender ID Code (1059), Section of the Act
    (1039, FHA only), Part IV 22a (900), and 25(2) Occupancy (1065). Flags blank
    Agency Case #, appraised value, and Part IV fields requiring manual entry.

    No-op when loan_type is not FHA or VA. Call as STEP_03 substep 3.2.
    """
    loan_id = state.get("loan_id")
    if not loan_id:
        return Command(update={"messages": [ToolMessage(
            content=json.dumps({"error": "No loan_id in state. Run find_loan first."}),
            tool_call_id=tool_call_id,
        )]})

    loan_type = _detect_gov_loan_type(state)
    if not loan_type:
        detected = str(_los(state, "loan_type") or _profile(state, "loan_type") or "unknown")
        result = {
            "success": True,
            "substep": SUBSTEP,
            "tool": "update_hud_addendum",
            "skipped": True,
            "loan_type": detected,
            "message": f"HUD 1003 Addendum not applicable for {detected} loans (FHA/VA only).",
        }
        logger.info(f"[UPDATE_HUD_ADDENDUM] {result['message']}")
        return Command(update={"messages": [ToolMessage(
            content=json.dumps(result), tool_call_id=tool_call_id)]})

    logger.info(f"[UPDATE_HUD_ADDENDUM] Starting for loan {str(loan_id)[:8]}... type={loan_type}")
    flags: list[dict] = []
    is_fha = loan_type == "FHA"
    is_va = loan_type == "VA"
    expected_agency = "VA" if is_va else "HUD / FHA"

    agency_type = str(_los(state, "hud_agency_type") or "").strip()
    if not agency_type or agency_type.upper() != expected_agency.upper():
        flags.append(_flag(
            "§03 #21 Agency Type Will Be Updated" if not agency_type else "§03 #21 Agency Type Mismatch",
            "warning",
            (
                f"Agency Type (1711) {_field_status(agency_type)} — "
                f"expected '{expected_agency}' for {loan_type} loans."
            ),
            f"Auto-writing Agency Type to '{expected_agency}'.",
        ))
    else:
        flags.append(_flag(
            "§03 #21 Agency Type Correct",
            "info",
            f"Agency Type (1711) = '{agency_type}' ✓",
            resolved=True,
        ))

    agency_case = str(_los(state, "fha_case_number") or "").strip()
    if not agency_case:
        flags.append(_flag(
            "§03 #21 Agency Case # Not Yet Assigned",
            "info",
            f"Agency Case # (1040) is empty for {loan_type} loan — typically assigned externally by FHA/VA.",
        ))
    else:
        flags.append(_flag(
            "§03 #21 Agency Case # Present",
            "info",
            f"Agency Case # (1040) = '{agency_case}'",
            resolved=True,
        ))

    lender_id = str(_los(state, "hud_lender_id_code") or "").strip()
    expected_lender_id = None
    lender_note = ""

    if is_va:
        expected_lender_id = VA_LENDER_ID
        lender_note = f"VA Lender ID Code must be '{VA_LENDER_ID}'"
    else:
        branch_address = str(_los(state, "branch_street_address") or "").strip()
        expected_lender_id, addr_matched = _lookup_fha_office_id(branch_address)
        lender_note = (
            f"Branch address '{branch_address}' matched FHA office list → Office ID '{expected_lender_id}'"
            if addr_matched
            else (
                f"Branch address '{branch_address or '(blank)'}' not found in FHA office list — "
                f"using default Office ID '{expected_lender_id}'"
            )
        )

    if not lender_id or lender_id != expected_lender_id:
        flags.append(_flag(
            "§03 #21 Lender ID Code Will Be Updated" if not lender_id else "§03 #21 Lender ID Code Mismatch",
            "warning",
            (
                f"Lender ID Code (1059) {_field_status(lender_id)} — "
                f"expected '{expected_lender_id}'. {lender_note}."
            ),
            f"Auto-writing Lender ID Code to '{expected_lender_id}'.",
        ))
    else:
        flags.append(_flag(
            "§03 #21 Lender ID Code Correct",
            "info",
            f"Lender ID Code (1059) = '{lender_id}' ✓ ({lender_note})",
            resolved=True,
        ))

    if is_fha:
        section_of_act = str(_los(state, "hud_section_of_act") or "").strip()
        if not section_of_act or section_of_act != "203B":
            flags.append(_flag(
                "§03 #21 Section of the Act Will Be Set to 203B",
                "warning",
                (
                    f"Section of the Act (1039) {_field_status(section_of_act)} — "
                    "must be '203B' for FHA loans."
                ),
                "Auto-writing Section of the Act to '203B'.",
            ))
        else:
            flags.append(_flag(
                "§03 #21 Section of the Act Correct",
                "info",
                f"Section of the Act (1039) = '{section_of_act}' ✓",
                resolved=True,
            ))

    own_sold_re = str(_los(state, "hud_22a_own_sold_re") or "").strip()
    own_sold_no = own_sold_re.upper() in {"N", "NO", "FALSE"} if own_sold_re else False
    if not own_sold_re or not own_sold_no:
        flags.append(_flag(
            "§03 #21 22a Will Be Set to No",
            "warning",
            (
                f"Question 22a (900) {_field_status(own_sold_re, ', expected No' if own_sold_re else '')} — "
                f"setting to 'No' for {loan_type} loans."
            ),
            "Auto-writing 22a to 'No'.",
        ))
    else:
        flags.append(_flag(
            "§03 #21 22a Correctly Set to No",
            "info",
            f"22a (900) = '{own_sold_re}' ✓",
            resolved=True,
        ))

    occupancy_25 = str(_los(state, "hud_occupancy_cert") or "").strip()
    expected_occupancy, occupancy_reason = _expected_occupancy(state)
    if expected_occupancy:
        if not occupancy_25 or occupancy_25 != expected_occupancy:
            flags.append(_flag(
                "§03 #21 25(2) Occupancy Will Be Updated",
                "warning",
                (
                    f"Section 25(2) Occupancy (1065) "
                    f"{_field_status(occupancy_25) if not occupancy_25 else _field_status(OCC_LABELS.get(occupancy_25, occupancy_25[:60]))}. "
                    f"Setting to: '{OCC_LABELS.get(expected_occupancy, expected_occupancy)}' ({occupancy_reason})."
                ),
                f"Auto-writing occupancy: {occupancy_reason}.",
            ))
        else:
            flags.append(_flag(
                "§03 #21 25(2) Occupancy Correct",
                "info",
                f"Section 25(2) (1065) = '{OCC_LABELS.get(occupancy_25, occupancy_25)}' ✓ ({occupancy_reason})",
                resolved=True,
            ))
    elif occupancy_25:
        flags.append(_flag(
            "§03 #21 25(2) Occupancy Selected",
            "info",
            f"Section 25(2) (1065) = '{OCC_LABELS.get(occupancy_25, occupancy_25)}' "
            "(could not determine expected value to validate against).",
            resolved=True,
        ))
    else:
        flags.append(_flag(
            "§03 #21 25(2) Occupancy Not Selected — Manual Update Required",
            "warning",
            (
                "Section 25(2) Occupancy (1065) is blank and loan context insufficient to auto-select "
                f"(Purpose: '{_los(state, 'loan_purpose') or ''}', "
                f"Occupancy: '{_los(state, 'occupancy') or ''}')."
            ),
            "Processor must manually select the appropriate occupancy statement from the dropdown.",
        ))

    appraised_val = str(_los(state, "appraised_value") or "").strip()
    if not appraised_val:
        flags.append(_flag(
            "§03 #21 25(3) Appraised Value Empty",
            "warning",
            "Section 25(3) Appraised Value (356) is empty.",
            "Verify appraised value is populated from the appraisal report.",
        ))
    else:
        flags.append(_flag(
            "§03 #21 25(3) Appraised Value Present",
            "info",
            f"Section 25(3) Appraised Value (356) = '{appraised_val}'",
            resolved=True,
        ))

    value_determination = str(_los(state, "hud_value_determination") or "").strip()
    if value_determination:
        flags.append(_flag(
            "§03 #21 25 Value Determination Set",
            "info",
            f"Section 25 Value Determination (1639) = '{value_determination}'",
            resolved=True,
        ))

    valuation_aware = str(_los(state, "hud_valuation_awareness") or "").strip()
    if valuation_aware:
        flags.append(_flag(
            "§03 #21 25 Valuation Awareness Selected",
            "info",
            f"Section 25 Valuation Awareness (1399) = '{valuation_aware}'",
            resolved=True,
        ))

    if is_fha:
        lead_paint = str(_los(state, "hud_lead_paint") or "").strip()
        if lead_paint:
            flags.append(_flag(
                "§03 #21 25(6) Lead Paint Disclosure Answered",
                "info",
                f"Section 25(6) Lead Paint (1400) = '{lead_paint}'",
                resolved=True,
            ))

    field_updates: dict[str, str] = {}
    if not agency_type or agency_type.upper() != expected_agency.upper():
        field_updates["1711"] = expected_agency
    if expected_lender_id and (not lender_id or lender_id != expected_lender_id):
        field_updates["1059"] = expected_lender_id
    if is_fha:
        section_of_act = str(_los(state, "hud_section_of_act") or "").strip()
        if not section_of_act or section_of_act != "203B":
            field_updates["1039"] = "203B"
    if not own_sold_re or not own_sold_no:
        field_updates["900"] = "N"
    if expected_occupancy:
        if not occupancy_25 or occupancy_25 != expected_occupancy:
            field_updates["1065"] = expected_occupancy

    if field_updates:
        _write_fields(loan_id, field_updates, substep=SUBSTEP, flags=flags,
                      state=state, labels=_FIELD_LABELS)

    warning_count = sum(1 for f in flags if f["severity"] == "warning")
    info_count = sum(1 for f in flags if f["severity"] in {"info", "info-overwrite"})
    result = {
        "success": True,
        "substep": SUBSTEP,
        "tool": "update_hud_addendum",
        "loan_type": loan_type,
        "fields_written": list(field_updates.keys()),
        "flags_count": len(flags),
        "warning_count": warning_count,
        "message": (
            f"HUD 1003 Addendum ({loan_type}): {len(field_updates)} field(s) written, "
            f"{warning_count} warning(s), {info_count} info flag(s)."
        ),
    }
    logger.info(f"[UPDATE_HUD_ADDENDUM] {result['message']}")

    update: dict = {"messages": [ToolMessage(content=json.dumps(result), tool_call_id=tool_call_id)]}
    if flags:
        update["flags"] = flags
    return Command(update=update)
