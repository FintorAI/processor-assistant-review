"""update_transmittal_summary — Tool for substep 11.1: Update Transmittal Summary

Step 11 (STEP_11): Transmittal Summary
Phase: FORM_UPDATES

What this agent does:
  1. Rate check — compare Note Rate (field 3) vs Qualifying Rate (field 1014).
     Flag warning if they differ.
  2. Project Type info — read field 1553, surface as info flag.
  3. Condo pending flag — if property is Condo/PUD and project fields are blank,
     flag info that the computer-use agent must run Freddie Mac Condo Project Advisor.
  4. PUD field writes — consumes precomputed PUD signals from STEP_01 1.3
     (state['property_verification']['pud']). When strong signals are present,
     skip the "Not in a Project" auto-write (the Possible PUD flag was already
     raised at 1.3). When a Zillow subdivision is available, write Project Name
     (field 1298) write-only-if-blank.
  5. Number of Units — write "1" to field 16 when the property is confirmed
     single family (no HOA, not condo/PUD, not 2-4 unit).

What this agent does NOT do:
  - Populate CPM Project ID# (field 3050) — requires browser lookup (CUA).
  - Construction Status (field 1067) — moved to update_hud_transmittal.py
    (substep 12.2) per processor feedback; it still runs regardless of loan
    type, just filed under the HUD Transmittal tool instead of here.
  - Re-raise the Possible PUD flag — already surfaced once at STEP_01 1.3.
  See ARCHITECTURE.md "Transmittal Summary — Condo Split" for the full design.
"""
# FACTORY-LOCK: true

import json
import logging
from datetime import datetime, timezone
from typing import Annotated, Optional

from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.prebuilt import InjectedState
from langgraph.types import Command

from ._helpers import _get_or_detect_property_verification, _is_condo_or_pud, _los, _write_fields

logger = logging.getLogger(__name__)


def _is_condo(property_type: Optional[str]) -> bool:
    return _is_condo_or_pud(property_type)


def _is_actual_condo(property_type: Optional[str]) -> bool:
    """True only for an actual condominium (not a PUD).

    ``_is_condo`` matches both condo and PUD; the Freddie Mac Condo Project
    Advisor / CPM Project ID# (CUA) workflow only applies to condos, so PUDs must
    be excluded from that guidance."""
    if not property_type:
        return False
    return any(t in property_type.lower() for t in ("condo", "condominium"))


def _parse_rate(val: Optional[str]) -> Optional[float]:
    if val is None:
        return None
    try:
        return float(str(val).replace("%", "").strip())
    except (ValueError, TypeError):
        return None


def _parse_money(val: Optional[str]) -> Optional[float]:
    if val is None:
        return None
    try:
        cleaned = str(val).replace("$", "").replace(",", "").strip()
        return float(cleaned) if cleaned else None
    except (ValueError, TypeError):
        return None


@tool
def update_transmittal_summary(
    tool_call_id: Annotated[str, InjectedToolCallId],
    state: Annotated[dict, InjectedState],
) -> Command:
    """Review the 1008 Transmittal Summary: compare note rate vs qualifying rate,
    surface project type for info, and flag condo project fields as pending CUA.

    Call this tool during STEP_11 (Transmittal Summary) as substep 11.1.
    Reads LOS: note_rate, qualifying_rate, transmittal_project_type, property_type,
               condo_project_name, condo_project_id, hoa_dues_monthly,
               attachment_type, property_address/city/state/zip, property_units
    Consumes: state['property_verification']['pud'] from STEP_01 1.3
    Flags: Note Rate vs Qualifying Rate Mismatch (warning), Project Type (info),
           Condo Project Fields Pending (info),
           Number of Units Set to 1 (info-overwrite) / Unexpected Value (warning)
    """
    loan_id = state.get("loan_id")
    if not loan_id:
        return Command(update={"messages": [ToolMessage(
            content=json.dumps({"error": "No loan_id in state. Run find_loan first."}),
            tool_call_id=tool_call_id,
        )]})

    logger.info(f"[UPDATE_TRANSMITTAL_SUMMARY] Starting for loan {str(loan_id)[:8]}...")

    flags = []

    note_rate            = _los(state, "note_rate")             # field 3
    qualifying_rate      = _los(state, "qualifying_rate")       # field 1014
    project_type         = _los(state, "transmittal_project_type")  # field 1553
    project_type_1012    = _los(state, "project_type_1012")     # field 1012 — project type dropdown
    property_review_type = _los(state, "property_review_type")  # field 1541
    appraisal_form_number = _los(state, "appraisal_form_number") # field 1542 — UNVERIFIED field ID
    property_form_type   = _los(state, "property_form_type")    # TSUM.PropertyFormType
    property_type        = _los(state, "property_type")         # field 1041
    condo_project_name   = _los(state, "condo_project_name")    # field 1298
    condo_project_id     = _los(state, "condo_project_id")      # field 3050
    ltv                  = _los(state, "ltv")                   # field 353
    hoa_dues             = _los(state, "hoa_dues_monthly")      # field 233
    property_units       = _los(state, "property_units")        # field 16

    # Precomputed at STEP_01 1.3 (falls back to a live lookup if 1.3 was skipped).
    pv = _get_or_detect_property_verification(state)
    pud = pv.get("pud") or {}
    pud_signals = list(pud.get("pud_signals") or [])
    zillow_subdivision = pud.get("zillow_subdivision")
    _strong = bool(pud.get("strong"))

    ts = datetime.now(timezone.utc).isoformat()

    # ── Rule: Note Rate vs Qualifying Rate ──────────────────────────────────
    note_rate_f = _parse_rate(note_rate)
    qual_rate_f = _parse_rate(qualifying_rate)

    if note_rate_f is not None and qual_rate_f is not None:
        if abs(note_rate_f - qual_rate_f) > 0.001:
            flags.append({
                "substep": "11.1",
                "title": "Note Rate vs Qualifying Rate Mismatch",
                "severity": "warning",
                "details": (
                    f"Note Rate (field 3) = {note_rate_f:.3f}% "
                    f"but Qualifying Rate (field 1014) = {qual_rate_f:.3f}%. "
                    f"For fixed-rate loans these must match."
                ),
                "suggestion": "Reconcile rates — qualifying rate should equal note rate for fixed-rate loans.",
                "resolved": False,
                "timestamp": ts,
            })
        else:
            logger.info(f"[UPDATE_TRANSMITTAL_SUMMARY] Rates match: {note_rate_f:.3f}%")
    elif note_rate_f is None or qual_rate_f is None:
        missing = []
        if note_rate_f is None:
            missing.append("Note Rate (field 3)")
        if qual_rate_f is None:
            missing.append("Qualifying Rate (field 1014)")
        flags.append({
            "substep": "11.1",
            "title": "Rate Fields Not Populated",
            "severity": "warning",
            "details": f"Cannot compare rates — {', '.join(missing)} is blank.",
            "suggestion": "Ensure note rate and qualifying rate are populated in Encompass.",
            "resolved": False,
            "timestamp": ts,
        })

    # ── Rule: Project Type (field 1012) — set to G/Not in PUD for non-condo/PUD ──
    _NOT_IN_PUD_VALUE = "Other: G/Not in a Project or Development"
    # Encompass returns the raw enum "G_NotInAProjectOrDevelopment" on read; both
    # forms are semantically identical — normalise before comparing.
    _NOT_IN_PUD_NORM = "gnotinaprojectordevelopment"

    def _normalise_1012(val: str) -> str:
        return val.lower().replace(" ", "").replace("_", "").replace("/", "").replace(":", "").replace("-", "").replace(".", "")

    # Consume precomputed PUD signals from 1.3. Do NOT re-raise the Possible PUD
    # flag here — it already fired once under substep 1.3.
    if not _is_condo(property_type):
        if _strong:
            logger.info(
                f"[UPDATE_TRANSMITTAL_SUMMARY] Strong PUD signals from 1.3 "
                f"({pud_signals}) — skipped 1012 'Not in a Project' auto-write."
            )
            # Project Name (field 1298) — derive from the Zillow community /
            # subdivision. Write-only-if-blank.
            if zillow_subdivision and not condo_project_name:
                _before_pn = len(flags)
                _write_fields(
                    loan_id, {"1298": zillow_subdivision}, "11.1",
                    flags, state=state, labels={"1298": "Project Name"},
                )
                if any(f.get("title") == "Auto-corrected: Project Name" for f in flags[_before_pn:]):
                    condo_project_name = zillow_subdivision
                    logger.info(
                        f"[UPDATE_TRANSMITTAL_SUMMARY] Wrote Project Name "
                        f"(field 1298) = {zillow_subdivision!r} from Zillow subdivision."
                    )
        else:
            if pud_signals:
                logger.info(
                    f"[UPDATE_TRANSMITTAL_SUMMARY] Weak PUD hints from 1.3 "
                    f"({pud_signals}) — still writing default non-PUD 1012."
                )
            current_1012 = (project_type_1012 or "").strip()
            if not current_1012:
                _write_fields(loan_id, {"1012": _NOT_IN_PUD_VALUE}, "11.1", flags, state=state)
                logger.info(f"[UPDATE_TRANSMITTAL_SUMMARY] Wrote field 1012 = '{_NOT_IN_PUD_VALUE}'")
            elif _normalise_1012(current_1012) == _NOT_IN_PUD_NORM or _NOT_IN_PUD_VALUE.lower() in current_1012.lower():
                logger.info(f"[UPDATE_TRANSMITTAL_SUMMARY] Field 1012 already correct: {current_1012!r}")
            else:
                flags.append({
                    "substep": "11.1",
                    "title": "Project Type (1012) — Unexpected Value",
                    "severity": "warning",
                    "details": (
                        f"Field 1012 = {current_1012!r}. Expected '{_NOT_IN_PUD_VALUE}' "
                        f"for a non-condo/PUD property ({property_type!r})."
                    ),
                    "suggestion": "Verify whether this property is in a PUD or development project. Correct field 1012 if needed.",
                    "resolved": False,
                    "timestamp": ts,
                })

    # ── Rule: Appraisal Form Number (field 1542) + Property Form Type ──────
    # NOTE: Field ID 1542 has NOT been verified against live Encompass.
    # Logic is correct but writes will silently no-op if 1542 maps to a different field.
    _prop_lower = (property_type or "").lower()
    if "condo" in _prop_lower or "condominium" in _prop_lower:
        _expected_form = "1073"
        _expected_form_type = "Individual Condominium Unit Appraisal Report"
    elif any(u in _prop_lower for u in ("2 unit", "2-unit", "3 unit", "3-unit", "4 unit", "4-unit")):
        _expected_form = "1025"
        _expected_form_type = "Small Residential Income Property Appraisal Report"
    else:
        # Single-family, 1-unit, detached, townhouse — default
        _expected_form = "1004"
        _expected_form_type = "Uniform Residential Appraisal Report"

    _current_form = (appraisal_form_number or "").strip()
    _current_form_type = (property_form_type or "").strip()

    _form_writes: dict = {}
    if not _current_form:
        _form_writes["1542"] = _expected_form
    if not _current_form_type:
        _form_writes["TSUM.PropertyFormType"] = _expected_form_type

    if _form_writes:
        _write_fields(loan_id, _form_writes, "11.1", flags, state=state)
    elif _current_form and _current_form != _expected_form:
        flags.append({
            "substep": "11.1",
            "title": "Appraisal Form Number — Unexpected Value",
            "severity": "warning",
            "details": (
                f"Field 1542 = {_current_form!r}, expected {_expected_form!r} "
                f"for property type {property_type!r}."
            ),
            "suggestion": f"Verify and correct field 1542 to '{_expected_form}' if appropriate.",
            "resolved": False,
            "timestamp": ts,
        })

    # ── Rule: Level of Property Review (field 1541) ─────────────────────────
    # Per processor feedback: LTV >= 80% requires a full Exterior/Interior
    # property review (no drive-by/exterior-only, no appraisal waiver — see
    # the matching PIW-vs-LTV cross-check in review_borrower_summary.py 2.1,
    # which uses this same >= 80% threshold).
    _EXPECTED_REVIEW_HIGH_LTV = "Exterior / Interior"
    _ltv_val = _parse_rate(ltv)
    _current_review = (property_review_type or "").strip()

    def _is_ext_int(val: str) -> bool:
        v = val.lower()
        return "exterior" in v and "interior" in v

    if _ltv_val is not None and _ltv_val >= 80:
        if not _current_review:
            _write_fields(loan_id, {"1541": _EXPECTED_REVIEW_HIGH_LTV}, "11.1", flags, state=state)
            logger.info(f"[UPDATE_TRANSMITTAL_SUMMARY] Wrote field 1541 = '{_EXPECTED_REVIEW_HIGH_LTV}' (LTV {_ltv_val:.3g}% >= 80%)")
        elif not _is_ext_int(_current_review):
            flags.append({
                "substep": "11.1",
                "title": "Level of Property Review — Unexpected Value",
                "severity": "warning",
                "details": (
                    f"Field 1541 (Level of Property Review) = {_current_review!r}, but LTV is "
                    f"{_ltv_val:.3g}% (>= 80%), which requires a full Exterior/Interior review."
                ),
                "suggestion": f"Verify and correct field 1541 to '{_EXPECTED_REVIEW_HIGH_LTV}' if appropriate.",
                "resolved": False,
                "timestamp": ts,
            })
        else:
            flags.append({
                "substep": "11.1",
                "title": "Level of Property Review",
                "severity": "info",
                "details": (
                    f"Field 1541 (Level of Property Review) = {_current_review!r}, consistent "
                    f"with LTV {_ltv_val:.3g}% (>= 80%)."
                ),
                "suggestion": "No action needed.",
                "resolved": False,
                "timestamp": ts,
            })
    elif _current_review:
        # LTV < 80% (or unavailable) — no enforced value; just surface current review type.
        flags.append({
            "substep": "11.1",
            "title": "Level of Property Review",
            "severity": "info",
            "details": f"Field 1541 (Level of Property Review) = {_current_review!r}.",
            "suggestion": "Confirm review type matches the appraisal ordered (Exterior = drive-by, Interior = full).",
            "resolved": False,
            "timestamp": ts,
        })

    # ── Rule: Project Type info ─────────────────────────────────────────────
    flags.append({
        "substep": "11.1",
        "title": "Project Type",
        "severity": "info",
        "details": (
            f"Transmittal Summary Project Type (field 1553) = {project_type!r}."
            if project_type else
            "Transmittal Summary Project Type (field 1553) is blank."
        ),
        "suggestion": "Verify project type is correct for this property.",
        "resolved": False,
        "timestamp": ts,
    })

    # ── Rule: Condo project fields pending CUA ──────────────────────────────
    if _is_condo(property_type):
        # Project Name (field 1298) can be auto-filled from the Zillow
        # community/subdivision. The non-condo PUD-detection block above never
        # runs once the property is already classified Condo/PUD, so write here
        # too. Write-only-if-blank. CPM Project ID# still needs the CUA browser lookup.
        if not condo_project_name and zillow_subdivision:
            _before = len(flags)
            _write_fields(
                loan_id, {"1298": zillow_subdivision}, "11.1", flags,
                state=state, labels={"1298": "Project Name"},
            )
            _wrote_ok = any(
                f.get("title") == "Auto-corrected: Project Name"
                for f in flags[_before:]
            )
            if _wrote_ok:
                condo_project_name = zillow_subdivision
                logger.info(
                    f"[UPDATE_TRANSMITTAL_SUMMARY] Wrote Project Name "
                    f"(field 1298) = {zillow_subdivision!r} from Zillow subdivision."
                )

        # CPM Project ID# / Freddie Mac Condo Project Advisor (CUA) guidance is
        # condo-only — PUDs share _is_condo() but do NOT use the CPM workflow.
        if _is_actual_condo(property_type):
            if not condo_project_name or not condo_project_id:
                missing_fields = []
                if not condo_project_name:
                    missing_fields.append("Project Name (field 1298)")
                if not condo_project_id:
                    missing_fields.append("CPM Project ID# (field 3050)")
                flags.append({
                    "substep": "11.1",
                    "title": "Condo Project Fields Pending — CUA Required",
                    "severity": "info",
                    "details": (
                        f"Property type is {property_type!r} (Condominium). "
                        f"Missing: {', '.join(missing_fields)}. "
                        f"CPM Project ID# requires the computer-use agent's Freddie Mac "
                        f"Condo Project Advisor browser lookup; Project Name is auto-filled "
                        f"from the Zillow subdivision when available."
                    ),
                    "suggestion": "Ensure computer-use agent runs the Freddie Mac Condo Project Advisor substep for the CPM Project ID#.",
                    "resolved": False,
                    "timestamp": ts,
                })
            else:
                logger.info(
                    f"[UPDATE_TRANSMITTAL_SUMMARY] Condo fields already populated: "
                    f"name={condo_project_name!r}, id={condo_project_id!r}"
                )

    # ── Rule: Number of Units (field 16) defaults to 1 for confirmed single family ──
    _is_multi_unit = any(
        u in _prop_lower for u in ("2 unit", "2-unit", "3 unit", "3-unit", "4 unit", "4-unit", "2-4 unit")
    )
    _hoa_amount = _parse_money(hoa_dues)
    _has_hoa = bool(_hoa_amount and _hoa_amount > 0)
    # Also treat strong PUD signals from 1.3 as blocking the "1 unit" write.
    _confirmed_single_family = (
        bool(property_type)  # blank property type can't confirm single-family
        and not _is_condo(property_type)  # excludes PUD + Condo
        and not _is_multi_unit
        and not _has_hoa
        and not _strong
    )
    _units_current = (property_units or "").strip()
    if not _units_current:
        if _confirmed_single_family:
            _write_fields(loan_id, {"16": "1"}, "11.1", flags, state=state, labels={"16": "Number of Units"})
            logger.info("[UPDATE_TRANSMITTAL_SUMMARY] Wrote field 16 (Number of Units) = '1'")
        else:
            logger.info(
                "[UPDATE_TRANSMITTAL_SUMMARY] Field 16 blank but property not confirmed "
                f"single-family (condo/PUD={_is_condo(property_type)}, multi_unit={_is_multi_unit}, "
                f"hoa=${_hoa_amount or 0:,.2f}, pud_strong={_strong}) — not auto-writing."
            )
    elif _units_current != "1" and _confirmed_single_family:
        flags.append({
            "substep": "11.1",
            "title": "Number of Units — Unexpected Value",
            "severity": "warning",
            "details": (
                f"Field 16 (Number of Units) = {_units_current!r}, but the property is "
                f"confirmed single family (no HOA, not condo/PUD, property_type={property_type!r})."
            ),
            "suggestion": "Verify and correct Number of Units (field 16) — expected 1.",
            "resolved": False,
            "timestamp": ts,
        })
    elif _units_current == "1" and _is_multi_unit:
        flags.append({
            "substep": "11.1",
            "title": "Number of Units — Unexpected Value",
            "severity": "warning",
            "details": (
                f"Field 16 (Number of Units) = '1', but property type "
                f"{property_type!r} indicates a 2-4 unit property."
            ),
            "suggestion": "Verify and correct Number of Units (field 16) to match the actual unit count.",
            "resolved": False,
            "timestamp": ts,
        })

    result = {
        "success": True,
        "substep": "11.1",
        "tool": "update_transmittal_summary",
        "note_rate": note_rate,
        "qualifying_rate": qualifying_rate,
        "project_type": project_type,
        "is_condo": _is_condo(property_type),
        "pud_signals": pud_signals,
        "flags_count": len(flags),
        "message": (
            f"Transmittal Summary: note_rate={note_rate}, qualifying_rate={qualifying_rate}, "
            f"project_type={project_type!r}"
            + (f" with {len(flags)} flag(s)" if flags else "")
        ),
    }

    logger.info(f"[UPDATE_TRANSMITTAL_SUMMARY] {result['message']}")

    update = {"messages": [ToolMessage(content=json.dumps(result), tool_call_id=tool_call_id)]}
    if flags:
        update["flags"] = flags

    return Command(update=update)
