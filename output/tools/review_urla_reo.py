"""review_urla_reo — Tool for substep 6.4: Section 3 — REO Properties

Step 6 (STEP_06): 1003 URLA Part 3
Phase: DATA_REVIEW

# FACTORY-LOCK: true
"""

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, List, Dict, Any

from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.prebuilt import InjectedState
from langgraph.types import Command

from ._helpers import _doc, _efolder_present, _relevant_docs

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

logger = logging.getLogger(__name__)

SUBSTEP = "6.4"


def _flag(title: str, severity: str, details: str, suggestion: str, docs=None) -> Dict[str, Any]:
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
    return f


@tool
def review_urla_reo(
    tool_call_id: Annotated[str, InjectedToolCallId],
    state: Annotated[dict, InjectedState],
) -> Command:
    """Review Section 3 — REO (Real Estate Owned) properties.

    Fetches reoProperties from the Encompass v3 API. If any properties are
    present, raises an info flag listing them so the processor knows to verify
    supporting docs (mortgage statement, insurance deck page, HOA statement,
    tax bill) are in the eFolder.

    If no REO properties exist, no flags are raised.

    Call this tool during STEP_06 (1003 URLA Part 3) as substep 6.4.
    """
    loan_id = state.get("loan_id")
    if not loan_id:
        return Command(update={"messages": [ToolMessage(
            content=json.dumps({"error": "No loan_id in state. Run find_loan first."}),
            tool_call_id=tool_call_id,
        )]})

    logger.info(f"[REVIEW_URLA_REO] Starting for loan {str(loan_id)[:8]}...")

    flags: List[Dict[str, Any]] = []

    # ── Fetch REO properties from Encompass v3 API ──
    try:
        from shared.encompass_io import read_reo_properties
        reo_props = read_reo_properties(loan_id, state=state)
        logger.info(f"[REVIEW_URLA_REO] {len(reo_props)} REO property/ies fetched")
    except Exception as exc:
        logger.warning(f"[REVIEW_URLA_REO] Failed to fetch reoProperties: {exc}")
        reo_props = []

    # ── Flag: info if any REO rows present + per-property doc checks ──
    if reo_props:
        lines = []
        for prop in reo_props:
            addr = prop["street_address"] or "Unknown address"
            city = prop["city"]
            st   = prop["state"]
            loc  = f"{addr}, {city} {st}".strip(", ")
            disp = prop["disposition_status"] or "Unknown"
            owner = prop["owner"] or "Borrower"
            lines.append(f"  • {loc} ({owner}) — disposition: {disp}")

        flags.append(_flag(
            title="Section 3 — REO Properties Present",
            severity="info",
            details=(
                f"{len(reo_props)} REO propert{'y' if len(reo_props)==1 else 'ies'} found in Encompass:\n"
                + "\n".join(lines)
            ),
            suggestion=(
                "Verify the following docs are in the eFolder for each owned property: "
                "mortgage statement, insurance deck page, HOA statement (if applicable), tax bill."
            ),
        ))

        # Per-property required doc checks — one warning per missing doc type
        # (eFolder doesn't support per-property buckets, so we check global presence once)
        reo_doc_checks = [
            ("Mortgage Statement",  "Mortgage Statement"),
            ("HOA Statement",       "HOA Statement"),
            ("Property Tax Bill",   "Property Tax Bill"),
        ]
        for doc_label, bucket_name in reo_doc_checks:
            if not _efolder_present(state, bucket_name):
                flags.append(_flag(
                    title=f"REO Doc Missing — {doc_label}",
                    severity="warning",
                    details=(
                        f"{doc_label} not found in eFolder. "
                        f"Required because borrower owns {len(reo_props)} "
                        f"propert{'y' if len(reo_props)==1 else 'ies'}."
                    ),
                    suggestion=f"Obtain and upload {doc_label} to the eFolder.",
                ))

        # ── Stale Mortgage Statement check (>90 days old) ──
        raw_stmt_date = _doc(state, "statement_date")
        _mortgage_refs = _relevant_docs(state, "statement_date", doc_types=["Mortgage Statement"])
        if raw_stmt_date:
            try:
                for fmt in ("%m/%d/%Y", "%B %d, %Y", "%b %d, %Y", "%Y-%m-%d"):
                    try:
                        stmt_dt = datetime.strptime(str(raw_stmt_date).strip(), fmt)
                        break
                    except ValueError:
                        continue
                else:
                    stmt_dt = None

                if stmt_dt:
                    age_days = (datetime.now() - stmt_dt).days
                    if age_days > 90:
                        flags.append(_flag(
                            title="Mortgage Statement — Stale (>90 Days)",
                            severity="warning",
                            details=(
                                f"Mortgage Statement is dated {raw_stmt_date} "
                                f"({age_days} days ago), which exceeds the 90-day "
                                "freshness threshold."
                            ),
                            suggestion=(
                                "Pull a Xactus credit supplement to obtain a current "
                                "mortgage payment history. Upload to eFolder under "
                                "'Other Owned Property Documents'."
                            ),
                            docs=_mortgage_refs,
                        ))
                    else:
                        flags.append(_flag(
                            title="Mortgage Statement — Current",
                            severity="info",
                            details=(
                                f"Mortgage Statement is dated {raw_stmt_date} "
                                f"({age_days} days ago) — within the 90-day window."
                            ),
                            suggestion="No action needed.",
                            docs=_mortgage_refs,
                        ))
            except Exception as exc:
                logger.warning(f"[REVIEW_URLA_REO] Could not parse statement_date '{raw_stmt_date}': {exc}")

    # ── Build result ──
    result = {
        "success": True,
        "substep": SUBSTEP,
        "tool": "review_urla_reo",
        "reo_count": len(reo_props),
        "flags_count": len(flags),
        "message": (
            f"Section 3 REO review complete — {len(reo_props)} propert{'y' if len(reo_props)==1 else 'ies'}"
            + (f"; {len(flags)} flag(s) raised" if flags else "")
        ),
    }

    logger.info(f"[REVIEW_URLA_REO] {result['message']}")

    update = {
        "messages": [ToolMessage(content=json.dumps(result), tool_call_id=tool_call_id)],
    }
    if flags:
        update["flags"] = flags

    return Command(update=update)
