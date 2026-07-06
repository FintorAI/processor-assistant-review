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

Hazard-insurance verification (checklist section 13) is scaffolded via the YAML
doc_types for future build-out.

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


@tool
def review_flood_hazard_insurance(
    tool_call_id: Annotated[str, InjectedToolCallId],
    state: Annotated[dict, InjectedState],
) -> Command:
    """Review the Flood Certificate against Encompass (checklist 12 #2 + 12 #4).

    Confirms the flood cert property address vs the USPS-validated subject and the
    flood cert borrower name vs the Encompass applicant surname(s) (12 #2), and
    reconciles the flood zone designation against Encompass field 541 with SFHA
    insurance-required checks (12 #4). Auto-corrects field 541 from the cert only
    when the extracted zone maps to a value the field-541 dropdown accepts;
    otherwise warns and leaves it for manual entry.

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
