"""review_urla_page1 — Tool for substep 4.1: Review 1003 URLA Page 1

Step 4 (STEP_04): 1003 URLA Page 1
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

from ._helpers import _los, _profile, _write_fields

logger = logging.getLogger(__name__)

_VALID_CITIZENSHIP = {"USCitizen", "PermanentResidentAlien", "NonPermanentResidentAlien"}
_VALID_BOOL = {"Y", "YES", "TRUE", "X", "1"}

# Residency / work-authorization documentation required for non-US-citizen
# borrowers (checklist 03 #17). Keyed by the Encompass citizenship enum value.
_RESIDENT_ALIEN_DOCS = {
    "PermanentResidentAlien":
        "a copy of the Permanent Resident Card (Green Card / Form I-551)",
    "NonPermanentResidentAlien":
        "a valid visa and/or Employment Authorization Document (EAD / Form I-766) "
        "plus proof of continued eligibility",
}
_VALID_LANG = {
    "EnglishIndicator", "SpanishIndicator", "ChineseIndicator", "KoreanIndicator",
    "TagalogIndicator", "VietnameseIndicator", "OtherIndicator", "DoNotWishToRespondIndicator",
}


def _flag(flags: list, substep: str, title: str, severity: str, details: str, suggestion: str = "") -> None:
    flags.append({
        "substep": substep,
        "title": title,
        "severity": severity,
        "details": details,
        "suggestion": suggestion,
        "resolved": False,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


def _resident_alien_doc_flag(flags: list, who: str, field_label: str, citizenship: str) -> None:
    """Flag a Resident Alien borrower for residency/work-authorization docs (03 #17).

    Emits a warning for Permanent or Non-Permanent Resident Alien so the processor
    obtains the Green Card / visa / EAD. Citizenship itself is never auto-populated.
    """
    doc = _RESIDENT_ALIEN_DOCS.get(citizenship)
    if not doc:
        return
    kind = ("Permanent" if citizenship == "PermanentResidentAlien" else "Non-Permanent")
    _flag(flags, "4.1",
          f"{who} Is a Resident Alien — Documentation Required",
          "warning",
          f"{field_label} = '{citizenship}' ({kind} Resident Alien). A non-US-citizen "
          f"borrower requires residency / work-authorization documentation in the file.",
          f"Obtain {doc} for the {who.lower()} and confirm it is current / unexpired.")


def _safe_int(val) -> int:
    try:
        return int(float(str(val).strip())) if val and str(val).strip() else 0
    except (ValueError, TypeError):
        return 0


# Canonical Encompass "Unit Type" values keyed by the designator words that may
# appear glued onto a unit number (e.g. "Unit 1313", "Apt. 4B").
_UNIT_DESIGNATORS = {
    "unit": "Unit",
    "apt": "Apartment", "apartment": "Apartment",
    "ste": "Suite", "suite": "Suite",
    "bldg": "Building", "building": "Building",
    "fl": "Floor", "floor": "Floor",
    "rm": "Room", "room": "Room",
    "spc": "Space", "space": "Space",
    "lot": "Lot",
    "trlr": "Trailer", "trailer": "Trailer",
    "ph": "Penthouse", "penthouse": "Penthouse",
    "dept": "Department", "department": "Department",
}

# (label, unit-type field, unit-type key, unit-# field, unit-# key)
_UNIT_ADDR_FIELDS = [
    ("Borrower Current", "FR0125", "borr_present_unit_type", "FR0127", "borr_present_unit_number"),
    ("Co-Borrower Current", "FR0225", "coborr_present_unit_type", "FR0227", "coborr_present_unit_number"),
    ("Borrower Former", "FR0325", "borr_former_unit_type", "FR0327", "borr_former_unit_number"),
    ("Co-Borrower Former", "FR0425", "coborr_former_unit_type", "FR0427", "coborr_former_unit_number"),
]


def _normalize_unit(unit_type, unit_number):
    """Split a designator word out of a unit number.

    "Unit 1313" -> ("Unit", "1313"); "Apt. 4B" -> ("Apartment", "4B");
    "#1313" -> (unit_type, "1313"). A bare identifier ("1313") is left untouched.
    Returns (new_type, new_number, changed).
    """
    raw = (unit_number or "").strip()
    if not raw:
        return unit_type, unit_number, False

    # Leading designator word: "Unit 1313", "Apt. 4B", "Suite #200"
    m = re.match(r"^([A-Za-z]+)\.?\s+#?\s*(\S.*)$", raw)
    if m and m.group(1).lower() in _UNIT_DESIGNATORS:
        return _UNIT_DESIGNATORS[m.group(1).lower()], m.group(2).strip(), True

    # Leading '#': "#1313" -> "1313" (type left as-is)
    m = re.match(r"^#\s*(\S.*)$", raw)
    if m:
        new_num = m.group(1).strip()
        return unit_type, new_num, new_num != raw

    return unit_type, unit_number, False


@tool
def review_urla_page1(
    tool_call_id: Annotated[str, InjectedToolCallId],
    state: Annotated[dict, InjectedState],
) -> Command:
    """Review 1003 URLA Page 1 for completeness. Skips mandatory personal info (owned by Borrower
    Summary). Checks citizenship (incl. a Resident-Alien documentation flag — Green Card / EAD —
    for Permanent or Non-Permanent Resident Aliens, checklist 03 #17), current/former address
    (with 2-year rule), mailing address, military service (VA-required), language preference,
    housing type/rent, dependents, and subject property # units.

    Call this tool during STEP_04 (1003 URLA Page 1) as substep 4.1.
    Does NOT re-check borrower name / SSN / DOB / marital status — those are owned by STEP_02.
    Writes: 1819 (borr mailing same), 1820 (coborr mailing same),
            URLA.X265 (borr former addr N/A), URLA.X266 (coborr former addr N/A),
            4533/4534 (P1 work phone backfilled from Part 2 phone FE0117/FE0217)
    """
    loan_id = state.get("loan_id")
    if not loan_id:
        return Command(update={"messages": [ToolMessage(
            content=json.dumps({"error": "No loan_id in state. Run find_loan first."}),
            tool_call_id=tool_call_id,
        )]})

    logger.info(f"[REVIEW_URLA_PAGE1] Starting for loan {str(loan_id)[:8]}...")

    flags = []
    loan_type = _profile(state, "loan_type") or ""
    is_va = loan_type.upper() == "VA"

    # Detect co-borrower from state
    coborrower_first = _los(state, "coborrower_first_name")
    coborrower_last  = _los(state, "coborrower_last_name")
    has_coborrower   = bool(coborrower_first and coborrower_last)

    # ── Rule: Citizenship ─────────────────────────────────────────────────
    # Never auto-populated — only flag for processor to fix.
    borr_citizenship = _los(state, "borrower_citizenship")
    if borr_citizenship in _VALID_CITIZENSHIP:
        _flag(flags, "4.1", "Borrower Citizenship", "info",
              f"URLA.X1 = '{borr_citizenship}'.", "Verify citizenship is correct.")
        _resident_alien_doc_flag(flags, "Borrower", "URLA.X1", borr_citizenship)
    else:
        _flag(flags, "4.1", "Borrower Citizenship Empty or Invalid", "warning",
              f"URLA.X1 = '{borr_citizenship or '(blank)'}'. Must be one of: "
              "US Citizen, Permanent Resident Alien, Non-Permanent Resident Alien. "
              "Agent does not auto-populate citizenship — processor must set it.",
              "Update borrower citizenship on 1003 URLA Part 1.")

    if has_coborrower:
        coborr_citizenship = _los(state, "coborrower_citizenship")
        if coborr_citizenship in _VALID_CITIZENSHIP:
            _flag(flags, "4.1", "Co-Borrower Citizenship", "info",
                  f"URLA.X2 = '{coborr_citizenship}'.", "Verify citizenship is correct.")
            _resident_alien_doc_flag(flags, "Co-Borrower", "URLA.X2", coborr_citizenship)
        else:
            _flag(flags, "4.1", "Co-Borrower Citizenship Empty or Invalid", "warning",
                  f"URLA.X2 = '{coborr_citizenship or '(blank)'}'. Must be one of: "
                  "US Citizen, Permanent Resident Alien, Non-Permanent Resident Alien.",
                  "Update co-borrower citizenship on 1003 URLA Part 1.")

    # ── Rule: Mailing Address Same as Present ─────────────────────────────
    borr_mailing = _los(state, "borr_mailing_same_as_present")
    if str(borr_mailing or "").strip().upper() not in _VALID_BOOL:
        _write_fields(loan_id=loan_id, updates={"1819": "True"}, substep="4.1",
                      flags=flags, state=state,
                      labels={"1819": "Borrower Mailing Address Same as Present"})

    if has_coborrower:
        coborr_mailing = _los(state, "coborr_mailing_same_as_present")
        if str(coborr_mailing or "").strip().upper() not in _VALID_BOOL:
            _write_fields(loan_id=loan_id, updates={"1820": "True"}, substep="4.1",
                          flags=flags, state=state,
                          labels={"1820": "Co-Borrower Mailing Address Same as Present"})

    # ── Rule: Current Address ─────────────────────────────────────────────
    borr_addr   = _los(state, "borr_present_addr")
    borr_city   = _los(state, "borr_present_city")
    borr_state_ = _los(state, "borr_present_state")
    borr_zip    = _los(state, "borr_present_zip")
    addr_fields_empty = [f for f, v in [
        ("FR0126", borr_addr), ("FR0106", borr_city),
        ("FR0107", borr_state_), ("FR0108", borr_zip),
    ] if not v]

    if not addr_fields_empty:
        _flag(flags, "4.1", "Borrower Current Address Present", "info",
              f"{borr_addr}, {borr_city}, {borr_state_} {borr_zip}.")
    else:
        _flag(flags, "4.1", "Borrower Current Address Incomplete", "warning",
              f"Present address fields empty: {', '.join(addr_fields_empty)}.",
              "Processor must populate current address on 1003 URLA Part 1.")

    # ── Rule: Former Address (2-year rule) ────────────────────────────────
    borr_yrs = _safe_int(_los(state, "borr_present_yrs"))
    borr_mos = _safe_int(_los(state, "borr_present_mos"))
    borr_total_months = borr_yrs * 12 + borr_mos

    if borr_total_months >= 24:
        x265 = str(_los(state, "borr_former_addr_does_not_apply") or "").strip().upper()
        if x265 in _VALID_BOOL:
            _flag(flags, "4.1", f"Borrower at Current Address ≥ 2 Years ({borr_yrs}Y {borr_mos}M) — Former Address N/A Already Checked", "info",
                  "URLA.X265 is already checked.")
        else:
            _write_fields(loan_id=loan_id, updates={"URLA.X265": "True"}, substep="4.1",
                          flags=flags, state=state,
                          labels={"URLA.X265": "Borrower Former Address Does Not Apply"})
    elif borr_total_months > 0:
        former_street = _los(state, "borr_former_addr")
        former_city   = _los(state, "borr_former_city")
        if former_street and former_city:
            _flag(flags, "4.1",
                  f"Borrower at Current Address < 2 Years ({borr_yrs}Y {borr_mos}M) — Former Address Present", "info",
                  f"Former: {former_street}, {former_city} {_los(state, 'borr_former_state') or ''} "
                  f"{_los(state, 'borr_former_zip') or ''}.")
        else:
            _flag(flags, "4.1",
                  f"Borrower at Current Address < 2 Years ({borr_yrs}Y {borr_mos}M) — Former Address Required", "warning",
                  "Borrower former address (FR0326/FR0306) is blank.",
                  "Processor must populate former address on 1003 URLA Part 1.")

    if has_coborrower:
        coborr_yrs = _safe_int(_los(state, "coborr_present_yrs"))
        coborr_mos = _safe_int(_los(state, "coborr_present_mos"))
        coborr_total_months = coborr_yrs * 12 + coborr_mos

        if coborr_total_months >= 24:
            x266 = str(_los(state, "coborr_former_addr_does_not_apply") or "").strip().upper()
            if x266 in _VALID_BOOL:
                _flag(flags, "4.1", f"Co-Borrower at Current Address ≥ 2 Years ({coborr_yrs}Y {coborr_mos}M) — Former Address N/A Already Checked", "info",
                      "URLA.X266 is already checked.")
            else:
                _write_fields(loan_id=loan_id, updates={"URLA.X266": "True"}, substep="4.1",
                              flags=flags, state=state,
                              labels={"URLA.X266": "Co-Borrower Former Address Does Not Apply"})
        elif coborr_total_months > 0:
            coborr_former_street = _los(state, "coborr_former_addr")
            coborr_former_city   = _los(state, "coborr_former_city")
            if coborr_former_street and coborr_former_city:
                _flag(flags, "4.1",
                      f"Co-Borrower at Current Address < 2 Years ({coborr_yrs}Y {coborr_mos}M) — Former Address Present", "info",
                      f"Co-Borr former: {coborr_former_street}, {coborr_former_city}.")
            else:
                _flag(flags, "4.1",
                      f"Co-Borrower at Current Address < 2 Years ({coborr_yrs}Y {coborr_mos}M) — Former Address Required", "warning",
                      "Co-Borrower former address (FR0426/FR0406) is blank.",
                      "Processor must populate co-borrower former address on 1003 URLA Part 1.")

    # ── Rule: Normalize Address Unit # / Unit Type ────────────────────────
    # The Unit # field sometimes carries a glued-on designator (e.g. "Unit 1313").
    # Split it: Unit # becomes the bare identifier ("1313") and Unit Type becomes
    # the designator ("Unit"). Applies to current + former, borrower + co-borrower.
    for _label, _type_fid, _type_key, _num_fid, _num_key in _UNIT_ADDR_FIELDS:
        if _label.startswith("Co-Borrower") and not has_coborrower:
            continue
        cur_type = _los(state, _type_key)
        cur_num = _los(state, _num_key)
        new_type, new_num, changed = _normalize_unit(cur_type, cur_num)
        if not changed:
            continue
        updates = {}
        if (new_num or "") != (cur_num or ""):
            updates[_num_fid] = new_num
        if (new_type or "") != (cur_type or ""):
            updates[_type_fid] = new_type
        if not updates:
            continue
        _write_fields(loan_id=loan_id, updates=updates, substep="4.1",
                      flags=flags, state=state,
                      labels={_num_fid: f"{_label} Address — Unit #",
                              _type_fid: f"{_label} Address — Unit Type"})

    # ── Rule: P1 Work Phone backfill from Part 2 phone ────────────────────
    # If the URLA Page 1 work phone (4533/4534) is empty, copy the Part 2 phone
    # (FE0117/FE0217) into it. Only fills when P1 is blank — never overwrites an
    # existing P1 value, and only writes when there is a Part 2 value to copy.
    _phone_backfill = [
        ("Borrower", "borr_p1_work_phone", "4533", "borr_part2_phone", "FE0117"),
    ]
    if has_coborrower:
        _phone_backfill.append(
            ("Co-Borrower", "coborr_p1_work_phone", "4534", "coborr_part2_phone", "FE0217")
        )
    for _who, _dest_key, _dest_fid, _src_key, _src_fid in _phone_backfill:
        _dest_val = str(_los(state, _dest_key) or "").strip()
        if _dest_val:
            continue  # P1 work phone already populated — leave it
        _src_val = str(_los(state, _src_key) or "").strip()
        if not _src_val:
            continue  # nothing to copy from Part 2
        _write_fields(loan_id=loan_id, updates={_dest_fid: _src_val}, substep="4.1",
                      flags=flags, state=state,
                      labels={_dest_fid: f"{_who} Work Phone (P1) — copied from Part 2 phone ({_src_fid})"})

    # ── Rule: Housing Type / Rent Amount ──────────────────────────────────
    _housing_checks = [
        ("Borrower Current", "borr_housing_type", "borr_housing_amount"),
        ("Borrower Former",  "borr_former_housing_type", "borr_former_housing_amount"),
    ]
    if has_coborrower:
        _housing_checks.append(("Co-Borrower Current", "coborr_housing_type", "coborr_housing_amount"))

    for _hlabel, _htype_key, _hamount_key in _housing_checks:
        _htype   = str(_los(state, _htype_key) or "").strip()
        _hamount = str(_los(state, _hamount_key) or "").strip()
        if _htype and "rent" in _htype.lower():
            if _hamount:
                _flag(flags, "4.1", f"{_hlabel} Housing = Rent, Amount Present", "info",
                      f"Housing type = '{_htype}', amount = {_hamount}.")
            else:
                _flag(flags, "4.1", f"{_hlabel} Housing = Rent but Amount Empty", "warning",
                      f"Housing type = '{_htype}' but expense amount is blank.",
                      "Enter the monthly rent amount.")

    # ── Rule: Military Service ────────────────────────────────────────────
    borr_military = str(_los(state, "borr_military_service") or "").strip().upper()
    borr_military_yes = borr_military in _VALID_BOOL
    borr_military_sub = [
        label for key, label in [
            ("borr_military_active_duty",      "Currently Serving on Active Duty"),
            ("borr_military_retired",          "Retired/Discharged/Separated"),
            ("borr_military_reserve",          "Non-Activated Reserve/National Guard"),
            ("borr_military_surviving_spouse", "Surviving Spouse"),
        ] if str(_los(state, key) or "").strip().upper() in _VALID_BOOL
    ]

    if is_va:
        if not borr_military_yes:
            _flag(flags, "4.1", "VA Loan — Borrower Military Service Must Be Yes", "warning",
                  f"URLA.X13 = '{_los(state, 'borr_military_service') or 'blank'}'. Must be Yes for VA loans.",
                  "Set Borrower Military Service to Yes on 1003 URLA Part 1.")
        if not borr_military_sub:
            _flag(flags, "4.1", "VA Loan — Borrower Military Service Type Not Selected", "warning",
                  "At least one sub-option must be checked (Active Duty / Retired / Reserve / Surviving Spouse).",
                  "Select the applicable military service type on 1003 URLA Part 1.")
        elif borr_military_yes:
            _flag(flags, "4.1", "Borrower Military Service", "info",
                  f"URLA.X13 = Yes. Sub-options: {', '.join(borr_military_sub)}.")

    if has_coborrower:
        coborr_military = str(_los(state, "coborr_military_service") or "").strip().upper()
        coborr_military_yes = coborr_military in _VALID_BOOL
        coborr_military_sub = [
            label for key, label in [
                ("coborr_military_active_duty",      "Currently Serving on Active Duty"),
                ("coborr_military_retired",          "Retired/Discharged/Separated"),
                ("coborr_military_reserve",          "Non-Activated Reserve/National Guard"),
                ("coborr_military_surviving_spouse", "Surviving Spouse"),
            ] if str(_los(state, key) or "").strip().upper() in _VALID_BOOL
        ]
        if is_va:
            if not coborr_military_yes:
                _flag(flags, "4.1", "VA Loan — Co-Borrower Military Service Must Be Yes", "warning",
                      f"URLA.X14 = '{_los(state, 'coborr_military_service') or 'blank'}'. Must be Yes for VA loans.",
                      "Set Co-Borrower Military Service to Yes on 1003 URLA Part 1.")
            if not coborr_military_sub:
                _flag(flags, "4.1", "VA Loan — Co-Borrower Military Service Type Not Selected", "warning",
                      "At least one co-borrower sub-option must be checked.",
                      "Select applicable co-borrower military service type on 1003 URLA Part 1.")

    # ── Rule: Language Preference ─────────────────────────────────────────
    for who, key, fid in [
        ("Borrower",     "borr_language_preference",  "URLA.X21"),
        ("Co-Borrower",  "coborr_language_preference", "URLA.X22"),
    ]:
        if who == "Co-Borrower" and not has_coborrower:
            continue
        val = str(_los(state, key) or "").strip()
        if val in _VALID_LANG:
            _flag(flags, "4.1", f"{who} Language Preference", "info",
                  f"{fid} = '{val}'.", "Verify with borrower if not English.")
        else:
            _flag(flags, "4.1", f"{who} Language Preference Empty — Manual Verification Required", "warning",
                  f"{fid} = '{val or '(blank)'}'. Defaulting to English — processor must verify with {who.lower()}.",
                  f"Set {who.lower()} language preference on 1003 URLA Part 1; update if not English.")
            _write_fields(loan_id=loan_id, updates={fid: "EnglishIndicator"}, substep="4.1",
                          flags=flags, state=state,
                          labels={fid: f"{who} Language Preference"})

    # ── Rule: Dependents ──────────────────────────────────────────────────
    borr_dep_count = _los(state, "borrower_dependents_count")
    borr_dep_ages  = _los(state, "borrower_dependent_ages")
    if borr_dep_count and str(borr_dep_count).strip() not in ("0", ""):
        if borr_dep_ages:
            _flag(flags, "4.1", "Borrower Dependents", "info",
                  f"Count (53) = {borr_dep_count}, Ages (54) = '{borr_dep_ages}'.")
        else:
            _flag(flags, "4.1", "Borrower Dependents Ages Missing", "warning",
                  f"Dependent count (53) = {borr_dep_count} but ages (54) is blank.",
                  "Enter dependent ages on 1003 URLA Part 1.")

    if has_coborrower:
        coborr_dep_count = _los(state, "coborr_dependents_count")
        coborr_dep_ages  = _los(state, "coborr_dependents_ages")
        if coborr_dep_count and str(coborr_dep_count).strip() not in ("0", ""):
            if coborr_dep_ages:
                _flag(flags, "4.1", "Co-Borrower Dependents", "info",
                      f"Count (85) = {coborr_dep_count}, Ages (86) = '{coborr_dep_ages}'.")
            else:
                _flag(flags, "4.1", "Co-Borrower Dependents Ages Missing", "warning",
                      f"Co-Borrower dependent count (85) = {coborr_dep_count} but ages (86) is blank.",
                      "Enter co-borrower dependent ages on 1003 URLA Part 1.")

    # ── Rule: Subject Property # Units ────────────────────────────────────
    prop_units = _los(state, "property_units")
    if prop_units and str(prop_units).strip():
        _flag(flags, "4.1", "Subject Property # Units", "info",
              f"Field 16 = '{prop_units}'.", "Verify # units is correct.")
    else:
        _flag(flags, "4.1", "Subject Property # Units Empty", "warning",
              "Subject Property # Units (16) is blank.",
              "Processor must populate # Units on 1003 URLA Part 1.")

    # ── Build result ──────────────────────────────────────────────────────
    result = {
        "success": True,
        "substep": "4.1",
        "tool": "review_urla_page1",
        "has_coborrower": has_coborrower,
        "is_va": is_va,
        "flags_count": len(flags),
        "message": "Review 1003 URLA Page 1 completed" + (f" with {len(flags)} flags" if flags else ""),
    }

    logger.info(f"[REVIEW_URLA_PAGE1] {result['message']}")

    update = {
        "messages": [ToolMessage(content=json.dumps(result), tool_call_id=tool_call_id)],
    }
    if flags:
        update["flags"] = flags

    return Command(update=update)
