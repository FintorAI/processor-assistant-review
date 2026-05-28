"""review_urla_reo — Tool for substep 5.4: Section 3 — REO Properties

Step 5 (STEP_05): 1003 URLA Part 3
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

from ._helpers import _los, _doc, _profile, _efolder_present

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

logger = logging.getLogger(__name__)

SUBSTEP = "5.4"


def _flag(title: str, severity: str, details: str, suggestion: str) -> Dict[str, Any]:
    return {
        "substep": SUBSTEP,
        "title": title,
        "severity": severity,
        "details": details,
        "suggestion": suggestion,
        "resolved": False,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


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

    Call this tool during STEP_05 (1003 URLA Part 3) as substep 5.4.
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
