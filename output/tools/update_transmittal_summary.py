"""update_transmittal_summary — Tool for substep 10.1: Update Transmittal Summary

Step 10 (STEP_10): Transmittal Summary
Phase: FORM_UPDATES

What this agent does:
  1. Rate check — compare Note Rate (field 3) vs Qualifying Rate (field 1014).
     Flag warning if they differ.
  2. Project Type info — read field 1553, surface as info flag.
  3. Condo pending flag — if property is Condo/PUD and project fields are blank,
     flag info that the computer-use agent must run Freddie Mac Condo Project Advisor.
  4. PUD detection — for a non-condo property, look for PUD indicators from three
     sources: (a) document-backed appraisal Project Type; (b) heuristic of HOA
     dues (field 233) + Attached dwelling; (c) external Zillow public records via
     HasData (home/structure type, hasAttachedProperty, HOA dues) — this automates
     the manual "Go to Zillow" check and needs HASDATA_API_KEY (best-effort: falls
     back to a Zillow deep link if disabled/unavailable). When any signal is
     present, skip the "Not in a Project" auto-write and raise a flag-to-verify.
     No auto-write of property/project type — the processor confirms, since
     misclassification affects pricing/eligibility.
  5. Project Name — when PUD indicators are present, populate Project Name
     (CX.CONDO.PROJECT.NAME, write-only-if-blank) from the Zillow community /
     subdivision name returned by the HasData lookup (e.g. "Germantown View").

What this agent does NOT do:
  - Populate CPM Project ID# (CX.CONDO.PROJECT.ID) — requires browser lookup (CUA).
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

from urllib.parse import quote

from ._helpers import _doc, _los, _write_fields

logger = logging.getLogger(__name__)

CONDO_PROPERTY_TYPES = {"condo", "condominium", "pud", "planned unit development"}


def _is_condo(property_type: Optional[str]) -> bool:
    if not property_type:
        return False
    return any(t in property_type.lower() for t in CONDO_PROPERTY_TYPES)


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


def _is_attached(val: Optional[str]) -> bool:
    """True if a property/attachment type string indicates an Attached dwelling."""
    if not val:
        return False
    low = val.lower()
    # Guard against "Detached" matching the "attached" substring.
    return "attach" in low and "detach" not in low


def _zillow_search_url(
    street: Optional[str],
    city: Optional[str],
    state: Optional[str],
    zip_: Optional[str],
) -> Optional[str]:
    """Build a Zillow address-search deep link so a processor can visually verify
    whether the subject sits in a Planned Unit Development (matches the manual
    'Go to Zillow' workflow)."""
    parts = [p.strip() for p in (street, city, state, zip_) if p and str(p).strip()]
    if not parts:
        return None
    return "https://www.zillow.com/homes/" + quote(" ".join(parts)) + "_rb/"


@tool
def update_transmittal_summary(
    tool_call_id: Annotated[str, InjectedToolCallId],
    state: Annotated[dict, InjectedState],
) -> Command:
    """Review the 1008 Transmittal Summary: compare note rate vs qualifying rate,
    surface project type for info, and flag condo project fields as pending CUA.

    Call this tool during STEP_10 (Transmittal Summary) as substep 10.1.
    Reads LOS: note_rate, qualifying_rate, transmittal_project_type, property_type,
               condo_project_name, condo_project_id, hoa_dues_monthly,
               attachment_type, property_address/city/state/zip
    Reads DOC: appraisal_project_type
    Flags: Note Rate vs Qualifying Rate Mismatch (warning), Project Type (info),
           Condo Project Fields Pending (info), Possible PUD — Verify (warning/info)
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
    condo_project_name   = _los(state, "condo_project_name")    # CX.CONDO.PROJECT.NAME
    condo_project_id     = _los(state, "condo_project_id")      # CX.CONDO.PROJECT.ID
    hoa_dues             = _los(state, "hoa_dues_monthly")      # field 233
    attachment_type      = _los(state, "attachment_type")       # CX.ATTACHMENT.TYPE
    # Authoritative PUD signal when the appraisal (URAR/1004) has been extracted.
    appraisal_project_type = _doc(state, "appraisal_project_type")

    ts = datetime.now(timezone.utc).isoformat()

    # ── Rule: Note Rate vs Qualifying Rate ──────────────────────────────────
    note_rate_f = _parse_rate(note_rate)
    qual_rate_f = _parse_rate(qualifying_rate)

    if note_rate_f is not None and qual_rate_f is not None:
        if abs(note_rate_f - qual_rate_f) > 0.001:
            flags.append({
                "substep": "10.1",
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
            "substep": "10.1",
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

    # ── PUD detection (path c: document-backed + heuristic + external Zillow) ─
    # (a) document-backed: appraisal "Project Type" checkbox == PUD (authoritative)
    _appraisal_says_pud = any(
        t in (appraisal_project_type or "").lower()
        for t in ("pud", "planned unit")
    )
    # (b) heuristic: HOA dues present on a non-condo property + Attached dwelling
    _hoa_amt = _parse_money(hoa_dues)
    _hoa_present = _hoa_amt is not None and _hoa_amt > 0
    _attached = _is_attached(attachment_type) or _is_attached(property_type)

    pud_signals: list[str] = []

    if not _is_condo(property_type):
        if _appraisal_says_pud:
            pud_signals.append(f"Appraisal Project Type indicates PUD ({appraisal_project_type!r})")
        if _hoa_present:
            pud_signals.append(f"HOA dues present (field 233 = {hoa_dues})")
        if _attached:
            pud_signals.append("Property is Attached (Encompass)")

        # (c) external — Zillow public records (best-effort; needs HASDATA_API_KEY).
        # Automates the manual "Go to Zillow" check the processor does to eyeball
        # whether the subject is attached / in a community with HOA dues.
        zillow_url = None
        zillow_subdivision = None
        zillow_signals: list[str] = []
        try:
            from shared.zillow_client import is_pud_indicative, lookup_property_sync
            zf = lookup_property_sync(
                _los(state, "property_address"),
                _los(state, "property_city"),
                _los(state, "property_state"),
                _los(state, "property_zip"),
            )
            if zf.found:
                zillow_url = zf.url
                zillow_subdivision = zf.subdivision
                if zf.has_attached_property:
                    _attached = True
                _, zillow_signals = is_pud_indicative(zf)
                pud_signals.extend(zillow_signals)
        except Exception as e:  # noqa: BLE001 — a lookup must never break the agent
            logger.warning(f"[UPDATE_TRANSMITTAL_SUMMARY] Zillow lookup failed: {e}")

        if pud_signals:
            # Do NOT auto-stamp "Not in a Project" — the subject shows PUD indicators.
            # Flag-to-verify only: misclassifying property/project type affects
            # pricing/eligibility, so leave the final call to the processor.
            _strong = (
                _appraisal_says_pud
                or (_hoa_present and _attached)
                or len(zillow_signals) >= 2
            )
            link = zillow_url or _zillow_search_url(
                _los(state, "property_address"),
                _los(state, "property_city"),
                _los(state, "property_state"),
                _los(state, "property_zip"),
            )
            flags.append({
                "substep": "10.1",
                "title": "Possible PUD — Verify Property / Project Type",
                "severity": "warning" if _strong else "info",
                "details": (
                    "Subject property shows PUD indicators: "
                    + "; ".join(pud_signals)
                    + f". Field 1012 (Project Type) was NOT auto-set to "
                    f"'{_NOT_IN_PUD_VALUE}' because of these signals "
                    f"(current value: {project_type_1012 or 'blank'})."
                ),
                "suggestion": (
                    "Verify whether the subject sits in a Planned Unit Development"
                    + (f" — Zillow: {link}" if link else "")
                    + ". If it is a PUD, set Property Type (field 1041) = PUD and "
                    "Project Type (field 1012) = 'Other: P/PUD'. "
                    + (
                        "The appraisal (URAR) authoritatively indicates PUD."
                        if _appraisal_says_pud else
                        "Confirm via the appraisal Project Type checkbox once received."
                    )
                ),
                "resolved": False,
                "timestamp": ts,
            })
            logger.info(
                f"[UPDATE_TRANSMITTAL_SUMMARY] PUD signals present "
                f"({pud_signals}) — skipped 1012 'Not in a Project' auto-write."
            )

            # Project Name (CX.CONDO.PROJECT.NAME) — derive from the Zillow
            # community/subdivision (e.g. "Germantown View"). Write-only-if-blank;
            # this replaces the old browser/CUA lookup for the project name.
            if zillow_subdivision and not condo_project_name:
                _write_fields(
                    loan_id, {"CX.CONDO.PROJECT.NAME": zillow_subdivision}, "10.1",
                    flags, state=state, labels={"CX.CONDO.PROJECT.NAME": "Project Name"},
                )
                condo_project_name = zillow_subdivision
                logger.info(
                    f"[UPDATE_TRANSMITTAL_SUMMARY] Wrote Project Name "
                    f"(CX.CONDO.PROJECT.NAME) = {zillow_subdivision!r} from Zillow subdivision."
                )
        else:
            current_1012 = (project_type_1012 or "").strip()
            if not current_1012:
                # _write_fields emits its own audited "Auto-corrected" flag — no manual flag needed.
                _write_fields(loan_id, {"1012": _NOT_IN_PUD_VALUE}, "10.1", flags, state=state)
                logger.info(f"[UPDATE_TRANSMITTAL_SUMMARY] Wrote field 1012 = '{_NOT_IN_PUD_VALUE}'")
            elif _normalise_1012(current_1012) == _NOT_IN_PUD_NORM or _NOT_IN_PUD_VALUE.lower() in current_1012.lower():
                logger.info(f"[UPDATE_TRANSMITTAL_SUMMARY] Field 1012 already correct: {current_1012!r}")
            else:
                flags.append({
                    "substep": "10.1",
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
        # _write_fields emits its own audited "Auto-corrected" flag — no manual flag needed.
        _write_fields(loan_id, _form_writes, "10.1", flags, state=state)
    elif _current_form and _current_form != _expected_form:
        flags.append({
            "substep": "10.1",
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

    # Surface property review type (field 1541) as info
    if property_review_type:
        flags.append({
            "substep": "10.1",
            "title": "Level of Property Review",
            "severity": "info",
            "details": f"Field 1541 (Level of Property Review) = {property_review_type!r}.",
            "suggestion": "Confirm review type matches the appraisal ordered (Exterior = drive-by, Interior = full).",
            "resolved": False,
            "timestamp": ts,
        })

    # ── Rule: Project Type info ─────────────────────────────────────────────
    flags.append({
        "substep": "10.1",
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
        if not condo_project_name or not condo_project_id:
            missing_fields = []
            if not condo_project_name:
                missing_fields.append("Project Name (CX.CONDO.PROJECT.NAME)")
            if not condo_project_id:
                missing_fields.append("CPM Project ID# (CX.CONDO.PROJECT.ID)")
            flags.append({
                "substep": "10.1",
                "title": "Condo Project Fields Pending — CUA Required",
                "severity": "info",
                "details": (
                    f"Property type is {property_type!r} (Condo/PUD). "
                    f"Missing: {', '.join(missing_fields)}. "
                    f"These are populated by the computer-use agent after the "
                    f"Freddie Mac Condo Project Advisor browser lookup."
                ),
                "suggestion": "Ensure computer-use agent runs the Freddie Mac Condo Project Advisor substep.",
                "resolved": False,
                "timestamp": ts,
            })
        else:
            logger.info(
                f"[UPDATE_TRANSMITTAL_SUMMARY] Condo fields already populated: "
                f"name={condo_project_name!r}, id={condo_project_id!r}"
            )

    result = {
        "success": True,
        "substep": "10.1",
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
