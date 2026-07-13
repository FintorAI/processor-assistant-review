"""build_action_items — Final substep: derive communications action items.

Step 13 (STEP_13): Processor Workflow and Closing
Substep 12.3 — run last, after all reviews/updates complete.

Component-agnostic RULE REGISTRY. Each rule inspects the review state and emits
at most one "action item" describing a downstream action a processor can trigger
from the dashboard (an email or a Blend follow-up). Items are written to
``state['comms_actions']`` and merged/deduped by ``id`` in
``proc_agent.merge_comms_actions`` (runtime status preserved across re-runs).

The schema is intentionally component-agnostic: every item carries a
``component`` and a ``trigger`` block, so future components (e.g. an
``integrations`` agent) can add rules WITHOUT changing the structure — future
growth is additive, not a revision.

Action item shape::

    {
      "id":          "order_title_report",       # stable; one per action_type per loan
      "component":   "communications",            # which downstream component owns it
      "action_type": "order_title_report",        # rule key
      "title":       "Order Title Report",
      "description": "…why this is needed…",
      "severity":    "action",                    # action | info
      "status":      "actionable",                # actionable (runtime status preserved on re-run)
      "blockers":    [],                          # human-readable reasons it can't run yet
      "needs_input": [],                          # fields the dashboard must collect before sending
      "trigger": {
          "agent":           "processor_communications",
          "graph_id":        "processor_title_order",   # LangGraph assistant_id
          "resume_contract": "email",                   # "email" | "blend_loe" (HITL resume shape)
          "payload":         { …AgentInput… }           # matches AGENT_INPUT_CONTRACT.md
      }
    }

# FACTORY-LOCK: true
"""

import json
import logging
import re
from datetime import datetime, timezone
from typing import Annotated, Any, Callable, Dict, List, Optional

from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.prebuilt import InjectedState
from langgraph.types import Command

from ._helpers import _efolder_present, _los, _profile

logger = logging.getLogger(__name__)

SUBSTEP = "13.3"
COMMS_AGENT = "processor_communications"


# ─────────────────────────────────────────────────────────────
# State helpers
# ─────────────────────────────────────────────────────────────

def _full_name(first: Optional[str], last: Optional[str]) -> str:
    return " ".join(p for p in [first, last] if p).strip()


def _borrower_name(state: dict) -> str:
    return _full_name(_los(state, "borrower_first_name"), _los(state, "borrower_last_name"))


def _coborrower_name(state: dict) -> Optional[str]:
    return _full_name(_los(state, "coborrower_first_name"), _los(state, "coborrower_last_name")) or None


def _loan_number(state: dict) -> Optional[str]:
    return _los(state, "loan_number") or state.get("loan_number")


def _money(value: Any) -> Optional[float]:
    """Coerce an LOS money value to a float for the comms templates.

    LOS fields arrive as formatted strings ('289,500.00', '$300,000'); the comms
    email templates expect numbers (they apply ``{:,.2f}|float`` formatting). This
    strips currency punctuation and returns a float, or ``None`` when the value is
    not actually numeric (e.g. EMD field holding a contract number like
    'MD92315-PU') so the recipient never sees a misleading '$0.00'.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = re.sub(r"[^0-9.\-]", "", str(value))
    if cleaned in ("", "-", ".", "-.", "."):
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _test_mode(state: dict) -> bool:
    """Email graphs must redirect to test inboxes on Test runs (never email real
    escrow/agent contacts from a Test review). Mirrors COMMS_TEST_MODE."""
    return str(state.get("env") or "").strip().lower() == "test"


def _property_address(state: dict) -> str:
    """Prefer the USPS-normalized address; fall back to the assembled LOS address."""
    av = state.get("address_validation") or {}
    if av.get("valid") and av.get("normalized"):
        return av["normalized"]
    state_zip = f"{_los(state, 'property_state') or ''} {_los(state, 'property_zip') or ''}".strip()
    parts = [_los(state, "property_address"), _los(state, "property_city"), state_zip]
    return ", ".join(p for p in parts if p).strip(", ")


def _is_purchase(state: dict) -> bool:
    lp = (_profile(state, "purpose") or _los(state, "loan_purpose") or "").lower()
    return "purchase" in lp


def _is_condo(state: dict) -> bool:
    pt = (_los(state, "property_type") or "").lower()
    return "condo" in pt  # matches "condominium", "condo", "condo/pud"


def _is_locked(state: dict) -> bool:
    if str(_los(state, "rate_is_locked") or "").strip().upper() == "Y":
        return True
    return str(_los(state, "loan_locked") or "").strip().lower() in ("y", "yes", "true", "locked")


def _lock_snapshot(state: dict) -> Optional[str]:
    parts = []
    if _los(state, "lock_date"):
        parts.append(f"Locked {_los(state, 'lock_date')}")
    if _los(state, "lock_expires"):
        parts.append(f"exp {_los(state, 'lock_expires')}")
    return ", ".join(parts) or None


def _unresolved_flag(state: dict, *keywords: str) -> Optional[dict]:
    kws = [k.lower() for k in keywords]
    for f in state.get("flags") or []:
        if not isinstance(f, dict) or f.get("resolved"):
            continue
        hay = f"{f.get('title', '')} {f.get('details', '')}".lower()
        if any(k in hay for k in kws):
            return f
    return None


def _base_payload(state: dict) -> Dict[str, Any]:
    """Top-level AgentInput fields shared by every graph."""
    return {
        "loan_number": _loan_number(state),
        "loan_id": state.get("loan_id"),
        "env": state.get("env") or "Prod",
        "processor_name": state.get("processor_name") or _los(state, "processor_name"),
    }


def _item(
    action_type: str,
    title: str,
    description: str,
    graph_id: str,
    resume_contract: str,
    payload: Dict[str, Any],
    *,
    component: str = "communications",
    status: str = "actionable",
    severity: str = "action",
    blockers: Optional[List[str]] = None,
    needs_input: Optional[List[str]] = None,
) -> Dict[str, Any]:
    return {
        "id": action_type,
        "component": component,
        "action_type": action_type,
        "title": title,
        "description": description,
        "severity": severity,
        "status": status,
        "blockers": blockers or [],
        "needs_input": needs_input or [],
        "trigger": {
            "agent": COMMS_AGENT,
            "graph_id": graph_id,
            "resume_contract": resume_contract,
            "payload": payload,
        },
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


# ─────────────────────────────────────────────────────────────
# Rules (each returns an action item dict, or None when N/A)
# ─────────────────────────────────────────────────────────────

def _rule_title_order(state: dict) -> Optional[dict]:
    """Order the title report when none is present in the eFolder."""
    if _efolder_present(state, "Title Report"):
        return None
    payload = {**_base_payload(state), "inputs": {
        "borrower_name": _borrower_name(state),
        "coborrower_name": _coborrower_name(state),
        "property_address": _property_address(state),
        "loan_amount": _money(_los(state, "loan_amount")),
        "purchase_price": _money(_los(state, "los_purchase_price")),
        "is_condo": _is_condo(state),
        "loan_number": _loan_number(state),
        "test_mode": _test_mode(state),
    }}
    return _item(
        "order_title_report",
        "Order Title Report",
        "No Title Report is in the eFolder. Send the official title order to the escrow/title company.",
        "processor_title_order", "email", payload,
    )


def _rule_lock_desk(state: dict) -> Optional[dict]:
    """Email the lock desk when a LOCKED loan's address needs correcting."""
    if not _is_locked(state):
        return None
    av = state.get("address_validation") or {}
    los_addr = av.get("los_address") or _property_address(state)
    new_addr = av.get("normalized")

    def _canon(s: Optional[str]) -> str:
        return " ".join((s or "").upper().replace(",", " ").split())

    needs_input: List[str] = []
    if not new_addr:
        # Locked, but USPS didn't return a normalized form — let the dashboard collect both.
        needs_input = ["old_address", "new_address"]
    elif _canon(new_addr) == _canon(los_addr):
        return None  # locked but address already matches USPS — nothing to change

    payload = {**_base_payload(state), "inputs": {
        "borrower_name": _borrower_name(state),
        "old_address": los_addr or None,
        "new_address": new_addr or None,
        "loan_number": _loan_number(state),
        "lock_status_snapshot": _lock_snapshot(state),
        "test_mode": _test_mode(state),
    }}
    return _item(
        "lock_desk_address_change",
        "Email Lock Desk — Address Change",
        "Loan is locked and the subject property address needs correcting. Email the lock desk to update it.",
        "processor_lock_desk", "email", payload,
        needs_input=needs_input,
    )


def _rule_emd_request(state: dict) -> Optional[dict]:
    """Email the buyer's agent for the EMD check copy when review flagged an EMD issue."""
    if not _is_purchase(state):
        return None
    flag = _unresolved_flag(state, "emd", "earnest money")
    if not flag:
        return None
    text = f"{flag.get('title', '')} {flag.get('details', '')}".lower()
    payload = {**_base_payload(state), "inputs": {
        "borrower_name": _borrower_name(state),
        "property_address": _property_address(state),
        # Omit when the LOS field is not a real dollar amount (some loans hold a
        # contract/file number here) so the email never shows "Expected EMD: $0.00".
        "expected_emd_amount": _money(_los(state, "emd_amount")),
        "emd_reason": "mismatch" if "mismatch" in text else "missing",
        "loan_number": _loan_number(state),
        "test_mode": _test_mode(state),
    }}
    return _item(
        "emd_request",
        "Email Agent — EMD Check Copy",
        f"{flag.get('title', 'EMD issue')}. Email the buyer's agent to request a copy of the EMD check.",
        "processor_emd_request", "email", payload,
    )


def _unresolved_flags(state: dict, *keywords: str) -> List[dict]:
    """All unresolved flags whose title/details contain any keyword (lowercased)."""
    kws = [k.lower() for k in keywords]
    out: List[dict] = []
    for f in state.get("flags") or []:
        if not isinstance(f, dict) or f.get("resolved"):
            continue
        hay = f"{f.get('title', '')} {f.get('details', '')}".lower()
        if any(k in hay for k in kws):
            out.append(f)
    return out


def _rule_employment_gap_loe(state: dict) -> Optional[dict]:
    """Request an employment-gap Letter of Explanation when review flagged a gap.

    Fires only when ``review_urla_employment`` raised an unresolved employment-gap
    flag (``_check_employment_gap`` → "FHA Employment Gap …" / "Employment Gap >
    6 Months …"). One item per loan covering every applicant with a gap; the
    ``payload`` carries the applicant(s) and the flag detail(s) so the comms
    template can reference the specific gap. Never fires from the "no prior
    employer / < 2 years" flag (title "Employment History Gap"), which is an
    Encompass data-entry fix, not a borrower LOE.

    NOTE: ``graph_id`` / ``resume_contract`` below are the comms-owned seam. It is
    modelled as an email request to the borrower; switch ``resume_contract`` to
    "blend_loe" if the letter should instead be sent for e-sign via Blend.
    """
    gap_flags = [
        f for f in _unresolved_flags(state, "employment gap")
        if "employment gap" in (f.get("title", "").lower())
    ]
    if not gap_flags:
        return None

    def _applicant(title: str) -> str:
        t = title.lower()
        if "co-borrower" in t or "coborrower" in t:
            return "Co-Borrower"
        return "Borrower"

    applicants = sorted({_applicant(f.get("title", "")) for f in gap_flags})
    gap_details = [f.get("details", "") for f in gap_flags if f.get("details")]

    payload = {**_base_payload(state), "inputs": {
        "loe_type": "employment_gap",
        "borrower_name": _borrower_name(state),
        "coborrower_name": _coborrower_name(state),
        "property_address": _property_address(state),
        "loan_number": _loan_number(state),
        "applicants_with_gaps": applicants,
        "gap_details": gap_details,
        "test_mode": _test_mode(state),
    }}
    return _item(
        "employment_gap_loe",
        "Request Employment-Gap Letter of Explanation",
        (
            f"{len(gap_flags)} employment-gap flag(s) require a written explanation "
            f"({', '.join(applicants)}). Request an employment-gap LOE from the borrower."
        ),
        "processor_employment_gap", "email", payload,
    )


def _rule_hoa_loe(state: dict) -> Optional[dict]:
    """Send a 'no-HOA' LOE for borrower signature (Blend) when HOA status is unconfirmed.

    Only for non-condos: a condo always has an HOA, so it needs the actual HOA
    Statement rather than a 'no HOA' attestation (that request is out of scope here).
    """
    if _is_condo(state):
        return None
    if _efolder_present(state, "HOA Statement"):
        return None
    payload = {**_base_payload(state), "inputs": {
        "loe_type": "HOA",
        "borrower_name": _borrower_name(state),
        "coborrower_name": _coborrower_name(state),
        "property_address": _property_address(state),
        "loan_number": _loan_number(state),
    }}
    return _item(
        "hoa_loe_signature",
        "Create Blend Follow-Up — No-HOA Letter",
        "No HOA Statement is on file and the property is not a condo. Send the borrower a 'no HOA' "
        "letter to e-sign via Blend.",
        "processor_blend_loe", "blend_loe", payload,
    )


def _rule_inquiry_loe(state: dict) -> Optional[dict]:
    """Request a credit-inquiry Letter of Explanation when §04 #7 flagged recent inquiries.

    Fires only when ``review_borrower_summary`` raised an unresolved
    ``§04 #7 Recent Credit Inquiries — LOX Required`` flag. The payload carries
    the inquiry detail lines from the flag so the comms template can reference
    each recent inquiry.
    """
    inq_flags = [
        f for f in _unresolved_flags(state, "recent credit inquiries", "lox required")
        if "§04 #7" in (f.get("title") or "")
    ]
    if not inq_flags:
        return None

    inquiry_details = [f.get("details", "") for f in inq_flags if f.get("details")]

    payload = {**_base_payload(state), "inputs": {
        "loe_type": "credit_inquiry",
        "borrower_name": _borrower_name(state),
        "coborrower_name": _coborrower_name(state),
        "property_address": _property_address(state),
        "loan_number": _loan_number(state),
        "inquiry_details": inquiry_details,
        "test_mode": _test_mode(state),
    }}
    return _item(
        "inquiry_loe",
        "Request Credit-Inquiry Letter of Explanation",
        (
            f"{len(inq_flags)} recent credit-inquiry flag(s) require a written explanation "
            f"from the borrower. Request a credit-inquiry LOE."
        ),
        "processor_inquiry_loe", "email", payload,
    )


# Registry — append future rules (including other components) here.
# NOTE: `_rule_hoa_loe` is intentionally OMITTED. Per processor feedback
# (notes.txt:608-619), the "no-HOA" Blend follow-up is case-by-case, not
# general: whether a borrower needs a no-HOA letter depends on loan type
# (refi / 2nd-3rd property / already owns property) and cannot be inferred
# from "non-condo + no HOA Statement in eFolder" alone — that condition
# over-triggers. The function is kept below for future re-enable once the
# trigger is made configurable/loan-specific.
RULES: List[Callable[[dict], Optional[dict]]] = [
    _rule_title_order,
    _rule_lock_desk,
    _rule_emd_request,
    _rule_employment_gap_loe,
    _rule_inquiry_loe,
]


# ─────────────────────────────────────────────────────────────
# Tool
# ─────────────────────────────────────────────────────────────

@tool
def build_action_items(
    tool_call_id: Annotated[str, InjectedToolCallId],
    state: Annotated[dict, InjectedState],
) -> Command:
    """Derive communications action items (emails / Blend follow-ups) from review state.

    Runs the component-agnostic rule registry: each rule emits at most one action
    item whose ``trigger.payload`` matches a processor-assistant-communications
    graph (see docs/AGENT_INPUT_CONTRACT.md). Results are written to
    ``comms_actions`` and merged/deduped by id on re-run (runtime status preserved).

    Call this as the FINAL substep (12.3), after all reviews and form updates.
    """
    actions: List[Dict[str, Any]] = []
    for rule in RULES:
        try:
            item = rule(state)
        except Exception as exc:  # one bad rule must not abort the rest
            logger.warning("[BUILD_ACTION_ITEMS] rule %s failed: %s",
                           getattr(rule, "__name__", rule), exc)
            item = None
        if item:
            actions.append(item)

    result = {
        "success": True,
        "substep": SUBSTEP,
        "tool": "build_action_items",
        "actions_count": len(actions),
        "action_types": [a["action_type"] for a in actions],
        "message": (
            f"Derived {len(actions)} communications action item(s)"
            + (f": {', '.join(a['action_type'] for a in actions)}" if actions else "")
        ),
    }
    logger.info("[BUILD_ACTION_ITEMS] %s", result["message"])

    return Command(update={
        "comms_actions": actions,
        "messages": [ToolMessage(content=json.dumps(result), tool_call_id=tool_call_id)],
    })
