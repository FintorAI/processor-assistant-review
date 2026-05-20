"""review_file_contacts — Tool for substep 1.2: File Contacts Check

Step 1 (STEP_01): Pre-Checks
Phase: INTAKE

# FACTORY-LOCK: true
"""

import json
import logging
import sys
import requests
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, List, Dict, Any, Optional

from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.prebuilt import InjectedState
from langgraph.types import Command

from ._helpers import _los

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

logger = logging.getLogger(__name__)

SUBSTEP = "1.2"

# Contact types we require to be linked on every loan
REQUIRED_CONTACTS = [
    {"type": "BUYERS_AGENT",   "label": "Buyer's Agent"},
    {"type": "SELLERS_AGENT",  "label": "Seller's Agent"},
    {"type": "SELLER",         "label": "Seller 1"},
    {"type": "ESCROW_COMPANY", "label": "Escrow Company"},
]


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


def _contact_summary(contact: Dict[str, Any]) -> str:
    """Build a short human-readable summary of a contact entry."""
    parts = []
    name = contact.get("name") or contact.get("contactName") or ""
    contact_person = contact.get("contactName") or ""
    email = contact.get("email") or ""
    phone = contact.get("phone") or contact.get("cell") or ""
    city = contact.get("city") or ""
    state = contact.get("state") or ""

    if name:
        parts.append(name)
    if contact_person and contact_person != name:
        parts.append(f"c/o {contact_person}")
    if city and state:
        parts.append(f"{city}, {state}")
    if email:
        parts.append(email)
    if phone:
        parts.append(phone)
    return " | ".join(parts) if parts else "(no details)"


def _get_loan_contacts(loan_id: str, state: dict) -> Optional[List[Dict[str, Any]]]:
    """Fetch all contacts for a loan via GET /v3/loans/{loanId}/contacts."""
    try:
        from encompass_client import get_encompass_client
        client = get_encompass_client(state=state)
        url = f"{client.api_base_url}/encompass/v3/loans/{loan_id}/contacts"
        headers = {"accept": "application/json", "Authorization": f"Bearer {client.access_token}"}
        r = requests.get(url, headers=headers, timeout=20)
        if r.status_code == 401:
            client.refresh_token()
            headers["Authorization"] = f"Bearer {client.access_token}"
            r = requests.get(url, headers=headers, timeout=20)
        r.raise_for_status()
        return r.json() if isinstance(r.json(), list) else []
    except Exception as exc:
        logger.warning(f"[REVIEW_FILE_CONTACTS] Contacts API error: {exc}")
        return None


@tool
def review_file_contacts(
    tool_call_id: Annotated[str, InjectedToolCallId],
    state: Annotated[dict, InjectedState],
) -> Command:
    """Verify that the four key file contacts are assigned in Encompass.

    Checks via GET /v3/loans/{loanId}/contacts that the following are linked:
      - Buyer's Agent  (BUYERS_AGENT)
      - Seller's Agent (SELLERS_AGENT)
      - Seller 1       (SELLER)
      - Escrow Company (ESCROW_COMPANY)

    Flags a warning for each missing contact. Raises an info flag listing all
    present contacts for processor awareness.

    Call this tool during STEP_01 (Pre-Checks) as substep 1.2.
    """
    loan_id = state.get("loan_id")
    if not loan_id:
        return Command(update={"messages": [ToolMessage(
            content=json.dumps({"error": "No loan_id in state. Run find_loan first."}),
            tool_call_id=tool_call_id,
        )]})

    logger.info(f"[REVIEW_FILE_CONTACTS] Starting for loan {str(loan_id)[:8]}...")

    flags: List[Dict[str, Any]] = []

    # ── Fetch contacts ──
    all_contacts = _get_loan_contacts(loan_id, state)
    if all_contacts is None:
        flags.append(_flag(
            title="File Contacts API Error",
            severity="warning",
            details="Could not retrieve loan contacts from Encompass.",
            suggestion="Manually verify Buyer's Agent, Seller's Agent, Seller 1, and Escrow Company are assigned.",
        ))
        result = {
            "success": False,
            "substep": SUBSTEP,
            "tool": "review_file_contacts",
            "flags_count": len(flags),
            "message": "File Contacts Check failed — API error",
        }
        return Command(update={
            "messages": [ToolMessage(content=json.dumps(result), tool_call_id=tool_call_id)],
            "flags": flags,
        })

    contact_map = {c.get("contactType", ""): c for c in all_contacts}

    # ── Check each required contact ──
    present: List[str] = []
    missing: List[str] = []

    for req in REQUIRED_CONTACTS:
        ct = req["type"]
        label = req["label"]
        contact = contact_map.get(ct)

        if contact:
            summary = _contact_summary(contact)
            present.append(f"  • {label}: {summary}")
            logger.info(f"[REVIEW_FILE_CONTACTS] [{ct}] OK — {summary}")
        else:
            missing.append(label)
            flags.append(_flag(
                title=f"Missing File Contact: {label}",
                severity="warning",
                details=f"{label} ({ct}) is not assigned to this loan in Encompass.",
                suggestion=f"Go to File Contacts in Encompass and link the {label}.",
            ))
            logger.warning(f"[REVIEW_FILE_CONTACTS] [{ct}] MISSING")

    # ── LOS cross-check: Seller 1 name field vs SELLER contact ──
    seller_1_name_los = _los(state, "seller_1_name")  # Field 638
    seller_contact = contact_map.get("SELLER", {})
    seller_contact_name = (seller_contact.get("name") or "").strip()

    if seller_1_name_los and seller_contact_name:
        if seller_1_name_los.lower() != seller_contact_name.lower():
            flags.append(_flag(
                title="Seller 1 Name Mismatch",
                severity="warning",
                details=(
                    f"LOS field 638 shows '{seller_1_name_los}' but "
                    f"the SELLER contact is '{seller_contact_name}'."
                ),
                suggestion="Verify and correct the Seller 1 name in Encompass.",
            ))

    # ── Info summary of all present contacts ──
    if present:
        flags.append(_flag(
            title="File Contacts — Present",
            severity="info",
            details="The following required contacts are assigned:\n" + "\n".join(present),
            suggestion="Confirm details are correct before placing orders.",
        ))

    # ── Build result ──
    result = {
        "success": True,
        "substep": SUBSTEP,
        "tool": "review_file_contacts",
        "contacts_found": len(present),
        "contacts_missing": len(missing),
        "flags_count": len(flags),
        "message": (
            f"File contacts: {len(present)} present, {len(missing)} missing"
            + (f" ({', '.join(missing)})" if missing else "")
        ),
    }

    logger.info(f"[REVIEW_FILE_CONTACTS] {result['message']}")

    update = {
        "messages": [ToolMessage(content=json.dumps(result), tool_call_id=tool_call_id)],
    }
    if flags:
        update["flags"] = flags

    return Command(update=update)
