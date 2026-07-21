"""update_fha_management — Tool for substep 12.1: FHA Management

Step 12 (STEP_12): FHA-Specific Forms
Phase: FORM_UPDATES

Three parts:
  1. Property Type (field 2996, "FHA Management" screen) — write "1 Unit" when
     the subject property is confirmed as a standard 1-unit property. Runs for
     EVERY loan type (verified live on a Conventional loan), because field 2996
     is a shared field that reflects on other forms, not an FHA-only value.
  2. CAIVRS — write the per-applicant CAIVRS Authorization Number extracted from
     the CAIVRS document into the Encompass CAIVRS fields (write-only-if-blank).
     FHA-only.
  3. FHA Case Number — write the assigned FHA Case Number (field 1040) from the
     FHA Government Documents extraction when 1040 is blank. Field 1040 is the
     same case-number field shown on the HUD-92900-LT, so one write covers both
     forms. ADP code is 703 for a standard 1-unit property. FHA-only.

CAIVRS and FHA Case Number are no-ops when loan_type != FHA; the Property Type
(2996) write always runs.

CAIVRS field IDs (verified, FHA Management → Tracking tab): borrower 1018,
co-borrower 1144. When a number is written, the update is stamped with CAIVRS
Date Updated (3067, MM/DD/YYYY) and CAIVRS Updated By (3068). Applicants beyond
borrower/co-borrower have no Encompass CAIVRS field and are flagged for manual
entry.
"""
# FACTORY-LOCK: true

import json
import logging
from datetime import datetime, timezone
from typing import Annotated

from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.prebuilt import InjectedState
from langgraph.types import Command

from ._helpers import _doc, _los, _profile, _write_fields

logger = logging.getLogger(__name__)

# ── CAIVRS Encompass field map (FHA Management → Tracking tab) ──────────────
# Verified field IDs: borrower 1018, co-borrower 1144.
CAIVRS_FIELDS_VERIFIED = True
CAIVRS_FIELDS: dict[str, dict[str, str | None]] = {
    "borrower":   {"field_id": "1018", "doc_key": "borrower_authorization_number",   "label": "Borrower CAIVRS Number"},
    "coborrower": {"field_id": "1144", "doc_key": "coborrower_authorization_number", "label": "Co-Borrower CAIVRS Number"},
}

# Additional applicants extracted from the document that have no dedicated
# Encompass CAIVRS field — surfaced for manual entry.
EXTRA_CAIVRS: list[dict[str, str]] = [
    {"doc_key": "coborrower2_authorization_number", "label": "Co-Borrower 2 CAIVRS Number"},
    {"doc_key": "coborrower3_authorization_number", "label": "Co-Borrower 3 CAIVRS Number"},
]

# Audit stamp written alongside any CAIVRS number update.
CAIVRS_DATE_FIELD = "3067"   # CAIVRS Date Updated (MM/DD/YYYY)
CAIVRS_BY_FIELD = "3068"     # CAIVRS Updated By
CAIVRS_UPDATED_BY = "adesai"


def _is_fha(state: dict) -> bool:
    """True when the loan is FHA.

    Checks BOTH the LOS Mortgage Type (field 1172, authoritative) and the Step-0
    loan_profile. The profile defaults to "Conventional" when the preflight type
    is blank, so it must never override an FHA value coming from the LOS field —
    treat the loan as FHA if either source says FHA.
    """
    los_lt = str(_los(state, "loan_type") or "").lower()
    prof_lt = str(_profile(state, "loan_type") or "").lower()
    return "fha" in los_lt or "fha" in prof_lt


def _clean(val) -> str | None:
    """Normalize an extracted CAIVRS number; treat blanks/placeholders as None."""
    if val is None:
        return None
    s = str(val).strip()
    if not s or s.lower() in {"n/a", "na", "none", "null", "-"}:
        return None
    return s


# Property Type (1041) values that indicate a standard 1-unit property.
_ONE_UNIT_PROPERTY_TYPES = {"detached", "attached", "condominium", "pud"}
# Property Type / Project Type values or unit counts that indicate 2-4 units.
_MULTI_UNIT_MARKERS = {"2-4 unit", "2-4unit", "duplex", "triplex", "fourplex"}


def _is_one_unit(property_type: str | None, property_units: str | None) -> bool | None:
    """Best-effort check that the subject property is a standard 1-unit property.

    Returns True/False when determinable from Property Type (1041) and/or
    Number of Units (16), or None when neither field has a usable value.
    """
    pt = (property_type or "").strip().lower()
    units = (property_units or "").strip()

    if units and units not in ("1",):
        return False
    if pt and any(marker in pt for marker in _MULTI_UNIT_MARKERS):
        return False
    if units == "1":
        return True
    if pt and pt in _ONE_UNIT_PROPERTY_TYPES:
        return True
    return None


def _parse_money(val) -> float | None:
    if val is None:
        return None
    s = str(val).strip().replace("$", "").replace(",", "")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _confirmed_single_unit(
    property_type: str | None, property_units: str | None, hoa_dues: str | None,
) -> bool:
    """Stricter single-unit confirmation used for the field 2996 write.

    Mirrors the "single family, no HOA" heuristic the processor uses on Zillow
    (notes.txt) and the Transmittal Summary's field-16 logic: excludes condo/
    PUD and any HOA dues, not just multi-unit signals, since field 2996 is a
    shared value other forms rely on.
    """
    pt = (property_type or "").strip().lower()
    hoa_amount = _parse_money(hoa_dues)
    has_hoa = bool(hoa_amount and hoa_amount > 0)

    if has_hoa:
        return False
    if pt and any(marker in pt for marker in ("condo", "condominium", "pud", "planned unit development")):
        return False
    return _is_one_unit(property_type, property_units) is True


@tool
def update_fha_management(
    tool_call_id: Annotated[str, InjectedToolCallId],
    state: Annotated[dict, InjectedState],
) -> Command:
    """Populate the FHA Management screen (Tracking tab).

    1. Property Type (field 2996) — write "1 Unit" (exact string, verified
       live) when the property is confirmed a standard single-unit property.
       Runs on EVERY loan type — field 2996 is a shared field, not FHA-only.
    2. CAIVRS — write the per-applicant CAIVRS Authorization Number extracted from
       the CAIVRS document into the Encompass CAIVRS fields (write-only-if-blank).
       Emits an info flag listing what was written. While the Encompass CAIVRS
       field IDs are unverified, the numbers are flagged for manual entry instead.
       FHA-only.
    3. FHA Case Number — write the assigned case number (field 1040) from the FHA
       Government Documents extraction when 1040 is blank (same field as the
       HUD-92900-LT); flag a warning if blank with nothing to write. FHA-only.

    Parts 2 and 3 are no-ops when loan_type != FHA; part 1 always runs.
    Call as STEP_12 substep 12.1.
    """
    loan_id = state.get("loan_id")
    if not loan_id:
        return Command(update={"messages": [ToolMessage(
            content=json.dumps({"error": "No loan_id in state. Run find_loan first."}),
            tool_call_id=tool_call_id,
        )]})

    logger.info(f"[UPDATE_FHA_MANAGEMENT] Starting for loan {str(loan_id)[:8]}...")

    flags: list[dict] = []
    property_type = _los(state, "property_type")
    property_units = _los(state, "property_units")
    hoa_dues = _los(state, "hoa_dues_monthly")
    fha_property_type_units = _los(state, "fha_property_type_units")  # field 2996

    # ── Property Type (field 2996, FHA Management screen) — runs regardless
    # of loan type. Verified live value: exact string "1 Unit". This is a
    # shared field other forms reflect, so it is NOT gated on loan_type == FHA. ──
    fha_2996_current = (fha_property_type_units or "").strip()
    one_unit_2996 = _confirmed_single_unit(property_type, property_units, hoa_dues)
    if not fha_2996_current:
        if one_unit_2996:
            _write_fields(
                loan_id, {"2996": "1 Unit"}, substep="12.1", flags=flags,
                state=state, labels={"2996": "FHA Management — Property Type"},
            )
            flags.append({
                "substep": "12.1",
                "title": "FHA Management Property Type Set to 1 Unit",
                "severity": "info",
                "details": (
                    f"Field 2996 (FHA Management — Property Type) was blank. Property "
                    f"Type (1041) = '{property_type or 'n/a'}', Number of Units (16) = "
                    f"'{property_units or 'n/a'}', HOA Dues (233) = '{hoa_dues or 'n/a'}' "
                    "confirm a standard single-unit property — wrote '1 Unit'."
                ),
                "suggestion": "Verify Property Type = \"1 Unit\" on the FHA Management screen.",
                "resolved": True,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            logger.info("[UPDATE_FHA_MANAGEMENT] Wrote field 2996 (Property Type) = '1 Unit'")
        else:
            flags.append({
                "substep": "12.1",
                "title": "FHA Management Property Type — Not Confirmed 1 Unit",
                "severity": "warning",
                "details": (
                    f"Field 2996 (FHA Management — Property Type) is blank, but the "
                    f"property could not be confirmed single-unit: Property Type (1041) "
                    f"= '{property_type or 'n/a'}', Number of Units (16) = "
                    f"'{property_units or 'n/a'}', HOA Dues (233) = '{hoa_dues or 'n/a'}'."
                ),
                "suggestion": (
                    "Confirm the property's unit count (check Zillow/appraisal) and set "
                    "Property Type on the FHA Management screen manually."
                ),
                "resolved": False,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
    else:
        logger.info(f"[UPDATE_FHA_MANAGEMENT] Field 2996 already populated: {fha_2996_current!r}")

    # ── FHA gate — CAIVRS + FHA Case Number are FHA-only ──
    if not _is_fha(state):
        wrote_2996 = bool(not fha_2996_current and one_unit_2996)
        result = {
            "success": True,
            "substep": "12.1",
            "tool": "update_fha_management",
            "skipped_fha_only_sections": True,
            "fha_property_type_units": "1 Unit" if wrote_2996 else fha_property_type_units,
            "flags_count": len(flags),
            "message": (
                "Not an FHA loan — CAIVRS/FHA Case Number skipped; Property Type (2996) "
                "check still ran." + (f" {len(flags)} flag(s)." if flags else "")
            ),
        }
        logger.info(f"[UPDATE_FHA_MANAGEMENT] {result['message']}")
        update: dict = {"messages": [ToolMessage(content=json.dumps(result), tool_call_id=tool_call_id)]}
        if flags:
            update["flags"] = flags
        return Command(update=update)

    fha_case_number = _los(state, "fha_case_number")

    # ── CAIVRS Authorization Numbers ──
    # Collect per-applicant numbers present in the extracted document.
    present: list[tuple[str, str]] = []  # (label, number)
    for cfg in CAIVRS_FIELDS.values():
        num = _clean(_doc(state, str(cfg["doc_key"])))
        if num:
            present.append((str(cfg["label"]), num))

    # Extra applicants (coborrower2/3) have no Encompass CAIVRS field.
    extras: list[tuple[str, str]] = []
    for x in EXTRA_CAIVRS:
        num = _clean(_doc(state, x["doc_key"]))
        if num:
            extras.append((x["label"], num))

    written_labels: list[str] = []

    if not present:
        flags.append({
            "substep": "12.1",
            "title": "CAIVRS Document Missing",
            "severity": "warning",
            "details": "No CAIVRS Authorization Number was extracted for any applicant on this FHA loan.",
            "suggestion": "Run CAIVRS Authorization in FHA Connection and upload the result to the eFolder.",
            "resolved": False,
            "relevant_documents": ["CAIVRS"],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
    elif not CAIVRS_FIELDS_VERIFIED:
        # IDs unverified — surface the numbers for manual entry, never write.
        listed = "\n".join(f"  • {label}: {num}" for label, num in present)
        flags.append({
            "substep": "12.1",
            "title": "CAIVRS Numbers Pending Manual Entry",
            "severity": "warning",
            "details": (
                "CAIVRS Authorization Number(s) were extracted but the Encompass "
                "CAIVRS field IDs are not yet verified, so they were not written "
                f"automatically:\n{listed}"
            ),
            "suggestion": "Enter the CAIVRS Authorization Number(s) on the FHA Management Tracking tab.",
            "resolved": False,
            "relevant_documents": ["CAIVRS"],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
    else:
        # Verified path: write-only-if-blank against the live Encompass values.
        field_ids = [str(c["field_id"]) for c in CAIVRS_FIELDS.values() if c["field_id"]]
        current: dict[str, object] = {}
        read_ok = True
        try:
            from encompass_client import read_loan_fields
            current = read_loan_fields(loan_id, field_ids, state=state) or {}
        except Exception as e:  # noqa: BLE001 — surface read failure as a flag
            read_ok = False
            logger.warning(f"[UPDATE_FHA_MANAGEMENT] CAIVRS read-back failed: {e}")

        if not read_ok:
            # We could not read the current CAIVRS values, so we cannot honor
            # write-only-if-blank. Abort the write path rather than risk
            # overwriting a manually-entered number with stale doc data.
            flags.append({
                "substep": "12.1",
                "title": "CAIVRS Read-Back Failed — Not Written",
                "severity": "warning",
                "details": (
                    "Could not read the current Encompass CAIVRS field values, so the "
                    "write-only-if-blank guard could not be applied and no CAIVRS "
                    "numbers were written automatically."
                ),
                "suggestion": "Verify/enter the CAIVRS Authorization Number(s) manually on the FHA Management Tracking tab.",
                "resolved": False,
                "relevant_documents": ["CAIVRS"],
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            writes, labels = {}, {}
        else:
            writes = {}
            labels = {}
            for cfg in CAIVRS_FIELDS.values():
                fid = cfg["field_id"]
                num = _clean(_doc(state, str(cfg["doc_key"])))
                if not fid or not num:
                    continue
                fid = str(fid)
                labels[fid] = str(cfg["label"])
                existing = current.get(fid)
                if existing is None or str(existing).strip() == "":
                    writes[fid] = num
                    written_labels.append(f"{cfg['label']}: {num}")

            if writes:
                # Stamp who/when the CAIVRS fields were updated.
                today = datetime.now(timezone.utc).strftime("%m/%d/%Y")
                writes[CAIVRS_DATE_FIELD] = today
                writes[CAIVRS_BY_FIELD] = CAIVRS_UPDATED_BY
                labels[CAIVRS_DATE_FIELD] = "CAIVRS Date Updated"
                labels[CAIVRS_BY_FIELD] = "CAIVRS Updated By"

                _write_fields(loan_id, writes, substep="12.1", flags=flags, state=state, labels=labels)
                flags.append({
                    "substep": "12.1",
                    "title": "CAIVRS Numbers Written",
                    "severity": "info",
                    "details": (
                        "Wrote CAIVRS Authorization Number(s):\n"
                        + "\n".join(f"  • {x}" for x in written_labels)
                        + f"\nStamped CAIVRS Date Updated = {today}, Updated By = {CAIVRS_UPDATED_BY}."
                    ),
                    "suggestion": "Verify the CAIVRS numbers on the FHA Management screen against the document.",
                    "resolved": True,
                    "relevant_documents": ["CAIVRS"],
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })

    # ── Extra applicants with no Encompass CAIVRS field ──
    if extras:
        listed = "\n".join(f"  • {label}: {num}" for label, num in extras)
        flags.append({
            "substep": "12.1",
            "title": "Additional CAIVRS Numbers — Manual Entry",
            "severity": "warning",
            "details": (
                "The CAIVRS document has Authorization Number(s) for applicant(s) with "
                f"no dedicated Encompass CAIVRS field:\n{listed}"
            ),
            "suggestion": "Enter these CAIVRS Authorization Number(s) manually on the FHA Management Tracking tab.",
            "resolved": False,
            "relevant_documents": ["CAIVRS"],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    # ── FHA Case Number (field 1040 — same field on FHA Management + HUD-92900-LT) ──
    case_present = bool(fha_case_number and str(fha_case_number).strip())
    if not case_present:
        # Write-when-missing: source the assigned case number from the FHA
        # Government Documents extraction.
        case_doc = _clean(_doc(state, "fha_assigned_case_number"))
        adp_doc = _clean(_doc(state, "fha_adp_code"))
        if case_doc:
            _write_fields(
                loan_id, {"1040": case_doc}, substep="12.1", flags=flags,
                state=state, labels={"1040": "FHA Case Number"},
            )
            case_present = True
            adp_note = f" (ADP code {adp_doc})" if adp_doc else ""
            flags.append({
                "substep": "12.1",
                "title": "FHA Case Number Written",
                "severity": "info",
                "details": (
                    f"Field 1040 was blank — wrote FHA Case Number {case_doc}{adp_note} "
                    "from FHA Government Documents. This is the same field shown on the "
                    "HUD-92900-LT."
                ),
                "suggestion": "Verify the FHA Case Number on the FHA Management screen against the document.",
                "resolved": True,
                "relevant_documents": ["FHA Government Documents"],
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
        else:
            flags.append({
                "substep": "12.1",
                "title": "FHA Case Number Missing",
                "severity": "warning",
                "details": "FHA Case Number (field 1040) is blank and none was extracted from FHA Government Documents.",
                "suggestion": "Assign the FHA Case Number via FHA Connection (ADP code 703 for a standard 1-unit property).",
                "resolved": False,
                "relevant_documents": ["FHA Government Documents"],
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

    # ── Property Type — confirm 1-unit before assuming ADP code 703 ──
    one_unit = _is_one_unit(property_type, property_units)
    if one_unit is True:
        flags.append({
            "substep": "12.1",
            "title": "Property Type Confirmed — 1 Unit",
            "severity": "info",
            "details": (
                f"Property Type (1041) = '{property_type or 'n/a'}', Number of Units "
                f"(16) = '{property_units or 'n/a'}' — confirms a standard 1-unit "
                "property, so ADP code 703 applies."
            ),
            "suggestion": "No action needed.",
            "resolved": True,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
    elif one_unit is False:
        flags.append({
            "substep": "12.1",
            "title": "Property Is Not 1-Unit — ADP Code 703 Does Not Apply",
            "severity": "warning",
            "details": (
                f"Property Type (1041) = '{property_type or 'n/a'}', Number of Units "
                f"(16) = '{property_units or 'n/a'}' — this is not a standard 1-unit "
                "property, so the 703 ADP code referenced for FHA Case Number "
                "assignment does not apply."
            ),
            "suggestion": "Select the correct ADP code for a 2-4 unit property in FHA Connection instead of 703.",
            "resolved": False,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
    # one_unit is None: Property Type/Units not yet available — no flag, avoid noise.

    result = {
        "success": True,
        "substep": "12.1",
        "tool": "update_fha_management",
        "property_type": property_type,
        "property_units": property_units,
        "fha_property_type_units": (
            "1 Unit" if (not fha_2996_current and one_unit_2996) else fha_property_type_units
        ),
        "fha_case_number_present": case_present,
        "caivrs_numbers_found": len(present),
        "caivrs_numbers_written": len(written_labels),
        "caivrs_extra_numbers": len(extras),
        "caivrs_fields_verified": CAIVRS_FIELDS_VERIFIED,
        "flags_count": len(flags),
        "message": "FHA Management completed" + (f" with {len(flags)} flags" if flags else ""),
    }
    logger.info(f"[UPDATE_FHA_MANAGEMENT] {result['message']}")

    update: dict = {"messages": [ToolMessage(content=json.dumps(result), tool_call_id=tool_call_id)]}
    if flags:
        update["flags"] = flags
    return Command(update=update)
