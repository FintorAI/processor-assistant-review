"""update_processor_closing — Tool for substep 14.2: Processor Closing Update

Step 14 (STEP_14): Processor Workflow and Closing
Phase: FORM_UPDATES

Date alignment (Est Closing Date, field 763, is the SOURCE OF TRUTH):
  - If Signing Date (CUST50FV) and Wire Requested Date (CX.WIREDATELO) both
    already equal 763, write nothing.
  - Otherwise write only the blank/mismatched date(s) FROM 763.
  - Field 763 itself is never written.
  - Michigan exception: never auto-set CX.WIREDATELO (wire timing differs from
    the MD same-day pattern) — flag + manual-entry row instead.

Certifications section (all custom checkbox fields, format X — verified by
live round-trip write 2026-07-23):
  - CX.VESTINGVERIFTITLE / CX.VESTINGVERIFBOR — always checked ("X")
  - CX.WIREINSTINFILE / CX.ESCROWEOINFILE / CX.CPLINFILE — checked when a
    matching document bucket with attachments exists in the live eFolder
    listing (fuzzy title match)
  - CX.HOIEFFECTIVE, CX.TAXES — never auto-written; registered as
    manual-entry rows in state['manual_fields'] for the dashboard
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

from ._helpers import _los, _manual_field, _write_fields

logger = logging.getLogger(__name__)

FIELD_LABELS = {
    "CUST50FV":             "Signing Date",
    "CX.WIREDATELO":        "Wire Requested Date",
    "CX.VESTINGVERIFTITLE": "Vesting Verified - Title",
    "CX.VESTINGVERIFBOR":   "Vesting Verified - Borrower",
    "CX.WIREINSTINFILE":    "Escrow wire instructions in file",
    "CX.ESCROWEOINFILE":    "Escrow E&O insurance in file",
    "CX.CPLINFILE":         "CPL in file (correct names, loan #, addressed to AWM)",
}

# Fuzzy bucket-title matchers for the eFolder-presence certification checkboxes.
# A checkbox is checked when ANY non-empty bucket title contains one of its
# substrings (case-insensitive).
_CERT_DOC_MATCHERS: dict[str, tuple[str, ...]] = {
    "CX.WIREINSTINFILE": ("wire instruction", "wiring instruction"),
    "CX.ESCROWEOINFILE": ("escrow e&o", "e&o insurance", "errors & omissions", "errors and omissions"),
    "CX.CPLINFILE":      ("closing protection", "cpl", "insured closing letter"),
}


def _to_iso_date(value) -> str | None:
    """Normalize a date string to ISO ``yyyy-MM-dd``.

    CUST50FV / CX.WIREDATELO are UTC date fields that require ISO format with no
    timezone offset. Encompass field 763 returns MM/DD/YYYY (e.g. ``06/18/2026``).
    Returns None if the value can't be parsed as a date.
    """
    if not value:
        return None
    raw = str(value).strip()
    # Drop a trailing time component if present (e.g. "06/18/2026 00:00:00").
    raw = raw.split("T")[0].split(" ")[0]
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def _list_efolder_bucket_titles(loan_id: str, state: dict) -> list[str] | None:
    """Live eFolder bucket listing: titles of buckets that have >=1 attachment.

    Uses GET /encompass/v3/loans/{id}/documents directly (same call as
    data_gathering's bucket map) because the wire-instructions / E&O / CPL
    buckets are not registered extraction doc types. Returns None on error so
    callers can distinguish "listing failed" from "no matching bucket".
    """
    import requests as _requests

    from encompass_client import get_encompass_client

    try:
        enc_client = get_encompass_client(state=state)
        headers = {
            "Authorization": f"Bearer {enc_client.access_token}",
            "Accept": "application/json",
        }
        url = f"{enc_client.api_base_url}/encompass/v3/loans/{loan_id}/documents"
        resp = _requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        return [
            d.get("title", "")
            for d in resp.json()
            if d.get("title") and (d.get("attachments") or [])
        ]
    except Exception as e:  # noqa: BLE001 — presence check must never break the substep
        logger.warning(f"[UPDATE_PROCESSOR_CLOSING] eFolder listing failed: {e}")
        return None


def _is_checked(value) -> bool:
    return str(value or "").strip().upper() in ("X", "Y", "YES", "TRUE", "1")


@tool
def update_processor_closing(
    tool_call_id: Annotated[str, InjectedToolCallId],
    state: Annotated[dict, InjectedState],
) -> Command:
    """Fill the Processor Closing screen: align Signing Date and Wire Requested
    Date to Est Closing Date (field 763, source of truth — only mismatched or
    blank dates are written, 763 itself never is), and complete the
    Certifications section (vesting-verified always checked; wire instructions
    / escrow E&O / CPL checked from eFolder presence; HOI-effective and Taxes
    left for manual entry).

    Call this tool during STEP_14 (Processor Workflow and Closing) as substep 14.2.
    Reads LOS: closing_date, signing_date, wire_requested_date, loan_purpose,
    property_state, cert_* checkbox fields, taxes_dropdown
    Flags: Closing Date Not Set (warning), Closing Dates Already Aligned (info),
           Michigan Wire Date Needs Manual Confirmation (info),
           Certification Document Not Found in eFolder (info)
    """
    loan_id = state.get("loan_id")
    if not loan_id:
        return Command(update={"messages": [ToolMessage(
            content=json.dumps({"error": "No loan_id in state. Run find_loan first."}),
            tool_call_id=tool_call_id,
        )]})

    logger.info(f"[UPDATE_PROCESSOR_CLOSING] Starting for loan {str(loan_id)[:8]}...")

    flags: list = []
    manual_rows: list = []
    writes: dict[str, str] = {}

    closing_date   = _los(state, "closing_date")         # field 763 — source of truth, never written
    signing_date   = _los(state, "signing_date")         # CUST50FV
    wire_date      = _los(state, "wire_requested_date")  # CX.WIREDATELO
    loan_purpose   = _los(state, "loan_purpose")         # field 19
    property_state = (_los(state, "property_state") or "").strip().upper()  # field 14
    is_purchase    = (loan_purpose or "").strip().lower() == "purchase"
    # Michigan is called out separately by the processor (video 6 feedback) —
    # same-day signing/wire/closing is confirmed for Maryland, but Michigan's
    # wire timing is different, so don't blindly equate it to closing date there.
    is_michigan    = property_state == "MI"

    # ── Date alignment: 763 is the source of truth ────────────────────────
    dates_already_aligned = False
    if is_purchase:
        iso_closing = _to_iso_date(closing_date)
        if not closing_date:
            flags.append({
                "substep": "14.2",
                "title": "Closing Date Not Set",
                "severity": "warning",
                "details": "Field 763 (Est Closing Date) is blank — cannot align Signing Date or Wire Requested Date.",
                "suggestion": "Set the closing date in Encompass before running this step.",
                "resolved": False,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            _manual_field(manual_rows, "14.2", "763", "Est Closing Date",
                          current_value=None,
                          reason="Blank — source-of-truth date must be set by the processor")
        elif not iso_closing:
            flags.append({
                "substep": "14.2",
                "title": "Closing Date Unparseable",
                "severity": "warning",
                "details": (
                    f"Field 763 (Est Closing Date) = {closing_date!r} could not be parsed "
                    "to ISO yyyy-MM-dd — Signing Date / Wire Requested Date not aligned."
                ),
                "suggestion": "Verify the closing date format in Encompass (expected MM/DD/YYYY).",
                "resolved": False,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
        else:
            signing_matches = _to_iso_date(signing_date) == iso_closing
            wire_matches = _to_iso_date(wire_date) == iso_closing

            if not signing_matches:
                writes["CUST50FV"] = iso_closing

            if not wire_matches:
                if is_michigan:
                    # Do NOT auto-set Wire Requested Date for Michigan — flag instead.
                    flags.append({
                        "substep": "14.2",
                        "title": "Michigan Wire Date Needs Manual Confirmation",
                        "severity": "info",
                        "details": (
                            "Michigan purchase loan — Wire Requested Date (CX.WIREDATELO) was "
                            "NOT auto-set to match Est Closing Date. The same-day "
                            "signing/wire/closing pattern confirmed for Maryland does not "
                            "apply to Michigan."
                        ),
                        "suggestion": "Confirm the correct Wire Requested Date for this Michigan closing with the closing team.",
                        "resolved": False,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    })
                    _manual_field(manual_rows, "14.2", "CX.WIREDATELO", "Wire Requested Date",
                                  current_value=wire_date,
                                  reason="Michigan purchase — wire timing differs from the MD same-day pattern",
                                  suggested_value=iso_closing)
                else:
                    writes["CX.WIREDATELO"] = iso_closing

            if signing_matches and wire_matches:
                dates_already_aligned = True
                flags.append({
                    "substep": "14.2",
                    "title": "Closing Dates Already Aligned",
                    "severity": "info",
                    "details": (
                        f"Signing Date, Wire Requested Date, and Est Closing Date all match "
                        f"({iso_closing}) — no date writes needed."
                    ),
                    "suggestion": "No action needed.",
                    "resolved": True,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
    else:
        # Non-purchase: flag if signing date is blank, don't auto-fill
        if not signing_date:
            flags.append({
                "substep": "14.2",
                "title": "Signing Date Not Set",
                "severity": "warning",
                "details": f"Loan purpose is {loan_purpose!r} (not Purchase) — signing date must be set manually.",
                "suggestion": "Set the signing date on the Processor Closing screen.",
                "resolved": False,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            _manual_field(manual_rows, "14.2", "CUST50FV", "Signing Date",
                          current_value=None,
                          reason=f"Non-purchase loan ({loan_purpose!r}) — date logic is purchase-only")

    # ── Certifications: vesting verified — always checked ─────────────────
    if not _is_checked(_los(state, "cert_vesting_verif_title")):
        writes["CX.VESTINGVERIFTITLE"] = "X"
    if not _is_checked(_los(state, "cert_vesting_verif_borrower")):
        writes["CX.VESTINGVERIFBOR"] = "X"

    # ── Certifications: eFolder-presence checkboxes ───────────────────────
    bucket_titles = _list_efolder_bucket_titles(loan_id, state)
    cert_presence: dict[str, bool] = {}
    if bucket_titles is None:
        flags.append({
            "substep": "14.2",
            "title": "eFolder Listing Unavailable — Certification Checkboxes Skipped",
            "severity": "warning",
            "details": (
                "Could not list eFolder buckets (GET /documents failed) — "
                "Wire Instructions / Escrow E&O / CPL certification checkboxes were not evaluated."
            ),
            "suggestion": "Re-run the substep, or check the certification boxes manually.",
            "resolved": False,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
    else:
        titles_lower = [t.lower() for t in bucket_titles]
        for field_id, needles in _CERT_DOC_MATCHERS.items():
            present = any(n in t for t in titles_lower for n in needles)
            cert_presence[field_id] = present
            key = {
                "CX.WIREINSTINFILE": "cert_wire_instructions_in_file",
                "CX.ESCROWEOINFILE": "cert_escrow_eo_in_file",
                "CX.CPLINFILE": "cert_cpl_in_file",
            }[field_id]
            already = _is_checked(_los(state, key))
            if present and not already:
                writes[field_id] = "X"
            elif not present and not already:
                flags.append({
                    "substep": "14.2",
                    "title": f"Certification Document Not Found in eFolder — {FIELD_LABELS[field_id]}",
                    "severity": "info",
                    "details": (
                        f"No non-empty eFolder bucket matched {needles} — "
                        f"{field_id} left unchecked."
                    ),
                    "suggestion": "Upload the document to the eFolder, then check the certification box.",
                    "resolved": False,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })

    # ── Certifications: manual-entry fields (never auto-written) ──────────
    _manual_field(manual_rows, "14.2", "CX.HOIEFFECTIVE",
                  "HOI is effective on or before Note Date (Wet State) or Funding Date (Dry State)",
                  current_value=_los(state, "cert_hoi_effective"),
                  reason="Judgment call (wet/dry state + HOI effective date) — left blank by design")
    _manual_field(manual_rows, "14.2", "CX.TAXES", "Taxes",
                  current_value=_los(state, "taxes_dropdown"),
                  reason="Left blank per processor guidance — dropdown: Unimproved / Improved")

    if writes:
        _write_fields(loan_id, writes, substep="14.2", flags=flags, state=state, labels=FIELD_LABELS)

    result = {
        "success": True,
        "substep": "14.2",
        "tool": "update_processor_closing",
        "loan_purpose": loan_purpose,
        "closing_date": closing_date,
        "dates_already_aligned": dates_already_aligned,
        "certification_doc_presence": cert_presence,
        "fields_written": list(writes.keys()),
        "manual_fields_registered": [row["field_id"] for row in manual_rows],
        "flags_count": len(flags),
        "message": (
            "Processor Closing: "
            + (
                "dates already aligned (no date writes)" if dates_already_aligned
                else f"aligned {[f for f in writes if f in ('CUST50FV', 'CX.WIREDATELO')]} to Est Closing Date"
                if any(f in writes for f in ("CUST50FV", "CX.WIREDATELO"))
                else "no date writes"
            )
            + f"; certifications written: {[f for f in writes if f.startswith('CX.')]}"
            + f"; manual-entry rows: {[row['field_id'] for row in manual_rows]}"
            + (f" with {len(flags)} flag(s)" if flags else "")
        ),
    }

    logger.info(f"[UPDATE_PROCESSOR_CLOSING] {result['message']}")

    update: dict = {"messages": [ToolMessage(content=json.dumps(result), tool_call_id=tool_call_id)]}
    if flags:
        update["flags"] = flags
    if manual_rows:
        update["manual_fields"] = manual_rows

    return Command(update=update)
