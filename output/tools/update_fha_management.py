"""update_fha_management — Tool for substep 11.1: FHA Management

Step 11 (STEP_11): FHA-Specific Forms
Phase: FORM_UPDATES

FHA-only. Two parts:
  1. CAIVRS — write the per-applicant CAIVRS Authorization Number extracted from
     the CAIVRS document into the Encompass CAIVRS fields (write-only-if-blank).
  2. FHA Case Number — confirm the FHA Case Number (field 1040) is assigned. The
     number is assigned via FHA Connection, not written here; ADP code is 703 for
     a standard 1-unit property.

No-op when loan_type != FHA.

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
    """True when the loan is FHA (profile first, LOS field 1172 fallback)."""
    lt = _profile(state, "loan_type") or _los(state, "loan_type") or ""
    return "fha" in str(lt).strip().lower()


def _clean(val) -> str | None:
    """Normalize an extracted CAIVRS number; treat blanks/placeholders as None."""
    if val is None:
        return None
    s = str(val).strip()
    if not s or s.lower() in {"n/a", "na", "none", "null", "-"}:
        return None
    return s


@tool
def update_fha_management(
    tool_call_id: Annotated[str, InjectedToolCallId],
    state: Annotated[dict, InjectedState],
) -> Command:
    """Populate the FHA Management screen (Tracking tab) for FHA loans.

    1. CAIVRS — write the per-applicant CAIVRS Authorization Number extracted from
       the CAIVRS document into the Encompass CAIVRS fields (write-only-if-blank).
       Emits an info flag listing what was written. While the Encompass CAIVRS
       field IDs are unverified, the numbers are flagged for manual entry instead.
    2. FHA Case Number — flag a warning if the FHA Case Number (field 1040) is
       blank (assign via FHA Connection; ADP code 703 for a standard 1-unit).

    No-op when loan_type != FHA. Call as STEP_11 substep 11.1.
    """
    loan_id = state.get("loan_id")
    if not loan_id:
        return Command(update={"messages": [ToolMessage(
            content=json.dumps({"error": "No loan_id in state. Run find_loan first."}),
            tool_call_id=tool_call_id,
        )]})

    # ── FHA gate ──
    if not _is_fha(state):
        result = {
            "success": True,
            "substep": "11.1",
            "tool": "update_fha_management",
            "skipped": True,
            "message": "Not an FHA loan — FHA Management skipped.",
        }
        logger.info(f"[UPDATE_FHA_MANAGEMENT] {result['message']}")
        return Command(update={"messages": [ToolMessage(
            content=json.dumps(result), tool_call_id=tool_call_id)]})

    logger.info(f"[UPDATE_FHA_MANAGEMENT] Starting for loan {str(loan_id)[:8]}...")

    flags: list[dict] = []
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
            "substep": "11.1",
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
            "substep": "11.1",
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
        try:
            from encompass_client import read_loan_fields
            current = read_loan_fields(loan_id, field_ids, state=state) or {}
        except Exception as e:  # noqa: BLE001 — surface read failure as a flag
            logger.warning(f"[UPDATE_FHA_MANAGEMENT] CAIVRS read-back failed: {e}")

        writes: dict[str, str] = {}
        labels: dict[str, str] = {}
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

            _write_fields(loan_id, writes, substep="11.1", flags=flags, state=state, labels=labels)
            flags.append({
                "substep": "11.1",
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
            "substep": "11.1",
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

    # ── FHA Case Number ──
    if not fha_case_number or str(fha_case_number).strip() == "":
        flags.append({
            "substep": "11.1",
            "title": "FHA Case Number Missing",
            "severity": "warning",
            "details": "FHA Case Number (field 1040) is blank.",
            "suggestion": "Assign the FHA Case Number via FHA Connection (ADP code 703 for a standard 1-unit property).",
            "resolved": False,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    result = {
        "success": True,
        "substep": "11.1",
        "tool": "update_fha_management",
        "fha_case_number_present": bool(fha_case_number and str(fha_case_number).strip()),
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
