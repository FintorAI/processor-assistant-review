"""review_fraud_compliance — Tool for substep 13.1: Review Fraud & LDP

Step 13 (STEP_13): Fraud & Compliance
Phase: DATA_REVIEW

Checklist §15:
  • 15 #1 — Review Fraud Report high alerts (fraud_alert_status, fraud_score)
  • 15 #2 — Known Participants / OFAC clear (LDP presence)

Read-only — does not order fraud or LDP reports.

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

from ._helpers import _doc, _efolder_present, _relevant_docs

logger = logging.getLogger(__name__)

SUBSTEP = "13.1"
FRAUD_DOC = "Fraud Report"
LDP_DOC = "LDP"
FRAUD_SCORE_THRESHOLD = 500

_HIGH_ALERT_TOKENS = frozenset({
    "high", "alert", "fail", "failed", "critical", "severe", "fraud",
})


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


def _parse_score(raw) -> float | None:
    if raw is None:
        return None
    cleaned = re.sub(r"[^0-9.\-]", "", str(raw))
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _is_high_alert_status(status: str) -> bool:
    s = (status or "").strip().lower()
    if not s:
        return False
    if s in _HIGH_ALERT_TOKENS:
        return True
    return any(tok in s for tok in _HIGH_ALERT_TOKENS)


@tool
def review_fraud_compliance(
    tool_call_id: Annotated[str, InjectedToolCallId],
    state: Annotated[dict, InjectedState],
) -> Command:
    """Review fraud report alerts and LDP/OFAC document presence (checklist §15 #1–#2)."""
    loan_id = state.get("loan_id")
    if not loan_id:
        return Command(update={"messages": [ToolMessage(
            content=json.dumps({"error": "No loan_id in state. Run data_gathering first."}),
            tool_call_id=tool_call_id,
        )]})

    flags: list[dict] = []

    # ── 15 #1 Fraud Report ─────────────────────────────────────────────
    fraud_present = _efolder_present(state, FRAUD_DOC)
    fraud_docs = _relevant_docs(state, FRAUD_DOC) if fraud_present else None
    alert_status = _doc(state, "fraud_alert_status")
    fraud_score = _parse_score(_doc(state, "fraud_score"))

    if not fraud_present:
        _flag(
            flags,
            "§15 #1 Fraud Report Missing",
            "warning",
            "No Fraud Report / FraudGuard document is in the eFolder.",
            "Obtain or order the fraud report and review high alerts before submission.",
        )
    else:
        high_alert = _is_high_alert_status(alert_status)
        score_high = fraud_score is not None and fraud_score >= FRAUD_SCORE_THRESHOLD

        if high_alert or score_high:
            parts = []
            if alert_status:
                parts.append(f"fraud_alert_status={alert_status!r}")
            if fraud_score is not None:
                parts.append(f"fraud_score={fraud_score:g}")
            _flag(
                flags,
                "§15 #1 Fraud Report High Alert",
                "warning",
                "Fraud report indicates elevated risk: " + "; ".join(parts) + ".",
                "Review FraudGuard alerts in the fraud report and clear or escalate before submission.",
                docs=fraud_docs,
            )
        elif alert_status or fraud_score is not None:
            detail = []
            if alert_status:
                detail.append(f"status={alert_status!r}")
            if fraud_score is not None:
                detail.append(f"score={fraud_score:g}")
            _flag(
                flags,
                "§15 #1 Fraud Report Reviewed",
                "info",
                "Fraud report on file — no high-alert indicators detected (" + ", ".join(detail) + ").",
                "No action needed unless processor sees additional concerns in the report.",
                docs=fraud_docs,
            )
        else:
            _flag(
                flags,
                "§15 #1 Fraud Report Present",
                "info",
                "Fraud report is in the eFolder but alert status/score were not extracted.",
                "Manually confirm FraudGuard shows no high alerts.",
                docs=fraud_docs,
            )

    # ── 15 #2 LDP / OFAC ─────────────────────────────────────────────
    ldp_present = _efolder_present(state, LDP_DOC)
    ldp_docs = _relevant_docs(state, LDP_DOC) if ldp_present else None

    if ldp_present:
        _flag(
            flags,
            "§15 #2 LDP/GSA On File",
            "info",
            "LDP/GSA document is present in the eFolder for known-participants / OFAC review.",
            "Confirm the report shows successful / clear status before submission.",
            docs=ldp_docs,
        )
    else:
        _flag(
            flags,
            "§15 #2 LDP/GSA Missing",
            "warning",
            "No LDP/GSA document is in the eFolder.",
            "Obtain LDP/GSA and confirm known participants / OFAC clearance before submission.",
        )

    result = {
        "success": True,
        "substep": SUBSTEP,
        "tool": "review_fraud_compliance",
        "fraud_present": fraud_present,
        "ldp_present": ldp_present,
        "flags_count": len(flags),
        "message": (
            "Fraud & LDP review complete"
            + (f" — {len(flags)} flag(s)" if flags else "")
        ),
    }

    logger.info("[REVIEW_FRAUD_COMPLIANCE] %s", result["message"])

    update: dict = {
        "messages": [ToolMessage(content=json.dumps(result), tool_call_id=tool_call_id)],
    }
    if flags:
        update["flags"] = flags
    return Command(update=update)
