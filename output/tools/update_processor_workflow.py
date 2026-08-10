"""update_processor_workflow — Tool for substep 14.1: Processor Workflow Update

Step 14 (STEP_14): Processor Workflow and Closing
Phase: FORM_UPDATES

Fills the Processor Workflow screen in Encompass:
  - CX.PRODUCTTYPE        — derived from loan type (Conventional → Conforming, etc.)
  - CX.DOCUMENTATIONTYPE  — Full Doc (for conventional/conforming)
  - CUST42FV              — "NO" (Non-Del Inv. Approval / "Prior Approval")
  - CUST69FV              — AKAs from the Credit Report (all bureaus), write-if-blank

CUST42FV verified in EC UI + live round-trip write 2026-07-23; its dropdown
options are uppercase YES / NO. The previously-guessed CX.NONDEL.INV.APPROVAL
does not exist in the prod instance (absent from the customFields schema).
CUST69FV (Processor Workflow AKA field) verified in Encompass UI 2026-08-10.
"""
# FACTORY-LOCK: true

import json
import logging
import re
from datetime import datetime, timezone
from typing import Annotated, Optional

from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.prebuilt import InjectedState
from langgraph.types import Command

from ._helpers import (
    _clean_doc_akas,
    _doc,
    _efolder_present,
    _los,
    _parse_aka_values,
    _profile,
    _relevant_docs,
    _write_fields,
)

logger = logging.getLogger(__name__)

# Loan type → CX.PRODUCTTYPE value mapping
LOAN_TYPE_TO_PRODUCT: dict[str, str] = {
    "conventional": "Conforming",
    "conforming":   "Conforming",
    "fha":          "FHA",
    "va":           "VA",
    "usda":         "USDA",
    "rural housing":"USDA",
    "jumbo":        "Jumbo",
    "nonqm":        "NonQM",
    "non qm":       "NonQM",
    "non-qm":       "NonQM",
    "reverse":      "Reverse",
    "construction": "Construction",
    "bridge":       "Bridge",
    "private":      "Private",
}

FIELD_LABELS = {
    "CX.PRODUCTTYPE":       "Product Type",
    "CX.DOCUMENTATIONTYPE": "Documentation Type",
    "CUST42FV":             "Non-Del Inv. Approval (Prior Approval)",
    "CUST69FV":             "Processor Workflow AKAs",
}


def _map_product_type(loan_type: Optional[str]) -> Optional[str]:
    if not loan_type:
        return None
    return LOAN_TYPE_TO_PRODUCT.get(loan_type.strip().lower())


def _collect_credit_report_akas(state: dict) -> tuple[set, set]:
    """Union AKAs from all credit-report sources: the extractor's flat
    borrower_aka/coborrower_aka lists plus each bureau's own AKA list from
    credit_score_factors (which preserves original bureau formatting that the
    flat merge sometimes shreds into single tokens).

    Returns (borrower_raw_akas, coborrower_raw_akas) — uncleaned name strings.
    Bureau file IDs ending in B2 (e.g. 'EQX-B2') belong to the co-borrower.
    """
    raw_borr = _parse_aka_values(_doc(state, "borrower_aka"))
    raw_co = _parse_aka_values(_doc(state, "coborrower_aka"))

    csf = _doc(state, "credit_score_factors")
    if isinstance(csf, list):
        for entry in csf:
            if not isinstance(entry, dict):
                continue
            bureau_akas = _parse_aka_values(entry.get("AKA") or entry.get("aka"))
            if not bureau_akas:
                continue
            repository = str(entry.get("repository") or "")
            if re.search(r"B2\b", repository, re.IGNORECASE):
                raw_co |= bureau_akas
            else:
                raw_borr |= bureau_akas

    return raw_borr, raw_co


@tool
def update_processor_workflow(
    tool_call_id: Annotated[str, InjectedToolCallId],
    state: Annotated[dict, InjectedState],
) -> Command:
    """Fill the Processor Workflow screen: set Product Type (from loan type),
    Non-Del Inv. Approval (No), Documentation Type (Full Doc), and the AKA
    field (write-if-blank from Credit Report AKAs aggregated across all bureaus).

    Call this tool during STEP_14 (Processor Workflow and Closing) as substep 14.1.
    Reads LOS: loan_type, product_type, doc_type_submission, non_del_inv_approval,
               processor_workflow_aka, borrower/co-borrower names
    Reads Docs: Credit Report (borrower_aka, coborrower_aka, credit_score_factors)
    Flags: Product Type Not Set (warning), Documentation Type Not Set (warning),
           Unknown Loan Type (warning), AKA Not Verified (warning),
           Auto-corrected: Processor Workflow AKAs (info-overwrite)
    """
    loan_id = state.get("loan_id")
    if not loan_id:
        return Command(update={"messages": [ToolMessage(
            content=json.dumps({"error": "No loan_id in state. Run find_loan first."}),
            tool_call_id=tool_call_id,
        )]})

    logger.info(f"[UPDATE_PROCESSOR_WORKFLOW] Starting for loan {str(loan_id)[:8]}...")

    flags = []

    # ── Read current values ──
    loan_type           = _los(state, "loan_type")           # field 1172
    current_product     = _los(state, "product_type")        # CX.PRODUCTTYPE
    current_doc_type    = _los(state, "doc_type_submission") # CX.DOCUMENTATIONTYPE
    current_non_del     = _los(state, "non_del_inv_approval")# CUST42FV
    current_pw_aka      = _los(state, "processor_workflow_aka")  # CUST69FV

    # ── Derive product type from loan type ──
    derived_product = _map_product_type(loan_type)

    if not derived_product:
        flags.append({
            "substep": "14.1",
            "title": "Unknown Loan Type — Product Type Not Mapped",
            "severity": "warning",
            "details": f"Loan type {loan_type!r} does not match any known product type mapping. "
                       f"Current CX.PRODUCTTYPE = {current_product!r}.",
            "suggestion": "Manually set Product Type on the Processor Workflow screen.",
            "resolved": False,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    # ── Write fields ──
    writes: dict[str, str] = {}

    if derived_product:
        writes["CX.PRODUCTTYPE"] = derived_product

    # Documentation Type — Full Doc for conventional/conforming
    # Non-QM loans may need a different value; for now always write Full Doc
    writes["CX.DOCUMENTATIONTYPE"] = "Full Doc"

    # Non-Del Inv. Approval — standard is NO for conforming (YES only when the
    # underwriter already approved). Dropdown enum is uppercase YES/NO.
    writes["CUST42FV"] = "NO"

    # ── AKAs from the Credit Report (all bureaus) → CUST69FV, write-if-blank ──
    borrower_first  = _los(state, "borrower_first_name")
    borrower_middle = _los(state, "borrower_middle_name")
    borrower_last   = _los(state, "borrower_last_name")
    coborrower_first  = _los(state, "coborrower_first_name")
    coborrower_middle = _los(state, "coborrower_middle_name")
    coborrower_last   = _los(state, "coborrower_last_name")
    has_coborrower = bool(coborrower_first and coborrower_last)

    raw_borr_akas, raw_co_akas = _collect_credit_report_akas(state)

    borrower_akas = _clean_doc_akas(
        raw_borr_akas, borrower_last, borrower_first, borrower_middle,
        other_first=coborrower_first, other_middle=coborrower_middle,
        other_last=coborrower_last,
    )
    coborrower_akas = set()
    if has_coborrower:
        coborrower_akas = _clean_doc_akas(
            raw_co_akas, coborrower_last, coborrower_first, coborrower_middle,
            other_first=borrower_first, other_middle=borrower_middle,
            other_last=borrower_last,
        )

    all_akas = sorted(borrower_akas | coborrower_akas)
    aka_already_populated = bool(current_pw_aka and str(current_pw_aka).strip())

    if all_akas and not aka_already_populated:
        # _write_fields emits the info-overwrite audit flag (labelled via FIELD_LABELS).
        writes["CUST69FV"] = "; ".join(all_akas)
    elif all_akas:
        # Do not log the field value itself — AKAs are borrower PII.
        logger.info(
            f"[UPDATE_PROCESSOR_WORKFLOW] CUST69FV already populated "
            f"({len(str(current_pw_aka))} chars) — write-if-blank, leaving as-is."
        )

    _write_fields(loan_id, writes, substep="14.1", flags=flags, state=state, labels=FIELD_LABELS)

    if not all_akas:
        credit_report_present = _efolder_present(state, "Credit Report")
        flags.append({
            "substep": "14.1",
            "title": "AKA Not Verified — No AKA Extracted from Credit Report",
            "severity": "warning",
            "details": (
                "No AKA names could be aggregated from the Credit Report bureau sections, "
                "so the Processor Workflow AKA field (CUST69FV) was not updated. "
                + ("The Credit Report is in the eFolder but its extraction returned no "
                   "usable AKA entries (bureau AKA sections may be empty)."
                   if credit_report_present
                   else "No Credit Report was found in the eFolder.")
            ),
            "suggestion": (
                "Review the credit report's per-bureau AKA sections manually and fill the "
                "AKA field on the Processor Workflow screen if alternate names are listed."
            ),
            "relevant_documents": _relevant_docs(
                state, "borrower_aka", "coborrower_aka", doc_types=["Credit Report"]),
            "resolved": False,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    # ── Post-write check: flag if product type still blank ──
    if not derived_product and not current_product:
        flags.append({
            "substep": "14.1",
            "title": "Product Type Not Set",
            "severity": "warning",
            "details": "CX.PRODUCTTYPE is blank and could not be derived from loan type.",
            "suggestion": "Manually set Product Type on the Processor Workflow screen.",
            "resolved": False,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    if "CUST69FV" in writes:
        aka_summary = f"AKAs={len(all_akas)} written"
    elif all_akas and aka_already_populated:
        aka_summary = "AKAs already populated (left as-is)"
    else:
        aka_summary = "no AKAs extracted"

    result = {
        "success": True,
        "substep": "14.1",
        "tool": "update_processor_workflow",
        "loan_type": loan_type,
        "derived_product_type": derived_product,
        "previous_values": {
            "CX.PRODUCTTYPE": current_product,
            "CX.DOCUMENTATIONTYPE": current_doc_type,
            "CUST42FV": current_non_del,
            "CUST69FV": current_pw_aka,
        },
        "aka_names": all_akas,
        "aka_borrower_count": len(borrower_akas),
        "aka_coborrower_count": len(coborrower_akas),
        "fields_written": list(writes.keys()),
        "flags_count": len(flags),
        "message": (
            f"Processor Workflow: set Product={derived_product or '(unknown)'}, "
            f"DocType=Full Doc, Non-Del Inv=NO, {aka_summary}"
            + (f" with {len(flags)} flag(s)" if flags else "")
        ),
    }

    logger.info(f"[UPDATE_PROCESSOR_WORKFLOW] {result['message']}")

    update = {"messages": [ToolMessage(content=json.dumps(result), tool_call_id=tool_call_id)]}
    if flags:
        update["flags"] = flags

    return Command(update=update)
