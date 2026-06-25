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

def _required_contacts(loan_purpose: str) -> list:
    """Return the list of required contacts based on loan purpose.

    Purchases require all four contacts.
    Refis only require an Escrow Company (no buyer/seller agents or seller).
    """
    contacts = [{"type": "ESCROW_COMPANY", "label": "Escrow Company"}]
    if "purchase" in str(loan_purpose).lower():
        contacts += [
            {"type": "BUYERS_AGENT",  "label": "Buyer's Agent"},
            {"type": "SELLERS_AGENT", "label": "Seller's Agent"},
            {"type": "SELLER",        "label": "Seller 1"},
        ]
    return contacts


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


# ── Cover-letter image → File Contacts (Gap A) ──────────────────────────────
# The frontend OCRs the image Almas attaches to her notes (substep 0.6,
# extract_almas_images) into state["almas_notes_images"][i]["extracted_text"].
# That image is a screenshot of Encompass's Roles & Contacts panel; the OCR
# prompt emits a structured "KEY CONTACTS" section we parse here to sync the
# escrow/agent file contacts: missing contacts are created, and a present
# contact that differs from the image is overwritten. OCR is imperfect, so any
# field whose value looks incomplete/invalid (truncated email/phone) is skipped
# — and because the contacts PATCH merges by contactType, a skipped field keeps
# its existing Encompass value. Every create/overwrite is flagged for review.

import re

_ROLE_TO_CONTACT_TYPE = {
    "escrow company": "ESCROW_COMPANY",
    "buyer's agent": "BUYERS_AGENT",
    "buyers agent": "BUYERS_AGENT",
    "seller's agent": "SELLERS_AGENT",
    "sellers agent": "SELLERS_AGENT",
    "seller 1": "SELLER",
}
# Only these roles are safe to auto-create from the image (agents + escrow).
_IMAGE_POPULATABLE = {"ESCROW_COMPANY", "BUYERS_AGENT", "SELLERS_AGENT"}
_CT_LABEL = {
    "ESCROW_COMPANY": "Escrow Company",
    "BUYERS_AGENT": "Buyer's Agent",
    "SELLERS_AGENT": "Seller's Agent",
    "SELLER": "Seller 1",
}
_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")


def _norm_role(s: str) -> str:
    return re.sub(r"[^a-z' ]", "", (s or "").strip().lower()).strip()


def _merge_bullet_blocks(text: str, found: Dict[str, Dict[str, str]]) -> None:
    """Parse the 'KEY CONTACTS' bullet format:

        **Escrow Company:**
        - Company: Grand Strand Law Group
        - Contact: Amy Tush
        - Phone: 843-492-5422
        - Email: amy@grandstrandlawgroup.com
    """
    current: Optional[str] = None
    cur: Dict[str, str] = {}

    def _flush() -> None:
        nonlocal current, cur
        if current and cur:
            ct = _ROLE_TO_CONTACT_TYPE.get(current)
            if ct:
                dst = found.setdefault(ct, {})
                for k, v in cur.items():
                    dst.setdefault(k, v)
        current, cur = None, {}

    for raw in (text or "").splitlines():
        line = raw.strip()
        header = re.match(r"^\*\*(.+?):\*\*$", line)
        if header:
            _flush()
            current = _norm_role(header.group(1))
            cur = {}
            continue
        if current:
            bullet = re.match(r"^[-*]\s*([A-Za-z ]+):\s*(.+)$", line)
            if bullet:
                field = bullet.group(1).strip().lower()
                val = bullet.group(2).strip()
                if field == "company":
                    cur["name"] = val
                elif field == "contact":
                    cur["contactName"] = val
                elif field == "phone":
                    cur["phone"] = val
                elif field == "email":
                    cur["email"] = val
                elif field == "license":
                    cur["personalLicenseNumber"] = val
    _flush()


def _merge_role_lines(text: str, found: Dict[str, Dict[str, str]]) -> None:
    """Parse the numbered Roles panel fallback:

        **40** Escrow Company
        Grand Strand Law Group | Amy Tush | 843-492-5422 | amy@...
    """
    lines = [l.rstrip() for l in (text or "").splitlines()]
    for i, raw in enumerate(lines):
        m = re.match(r"^\*\*\d+\*\*\s*(.+)$", raw.strip())
        if not m:
            continue
        ct = _ROLE_TO_CONTACT_TYPE.get(_norm_role(m.group(1)))
        if not ct:
            continue
        for j in range(i + 1, min(i + 3, len(lines))):
            nxt = lines[j].strip()
            if not nxt or re.match(r"^\*\*\d+\*\*", nxt):
                continue
            if "|" in nxt:
                parts = [p.strip() for p in nxt.split("|")]
                dst = found.setdefault(ct, {})
                fields = ["name", "contactName", "phone", "email"]
                for idx, key in enumerate(fields):
                    if idx < len(parts) and parts[idx]:
                        dst.setdefault(key, parts[idx])
            break


def _parse_image_contacts(images: Any) -> Dict[str, Dict[str, str]]:
    """Extract escrow/agent contacts from the OCR'd Almas-notes image(s).

    Returns {contactType: {name, contactName, phone, email, ...}} for the roles
    we map to File Contacts. KEY CONTACTS bullets win; numbered role lines fill gaps.
    """
    found: Dict[str, Dict[str, str]] = {}
    if not isinstance(images, list):
        return found
    for item in images:
        if not isinstance(item, dict) or item.get("ocr_status") != "ok":
            continue
        text = item.get("extracted_text") or ""
        if not text:
            continue
        _merge_bullet_blocks(text, found)
        _merge_role_lines(text, found)
    return found


def _looks_complete_phone(value: str) -> bool:
    """A usable phone needs at least 10 digits (US). OCR truncation drops digits."""
    return len([c for c in value if c.isdigit()]) >= 10


def _build_contact_obj(contact_type: str, parsed: Dict[str, str]) -> tuple:
    """Build an Encompass contact object from parsed image fields.

    Drops any field whose OCR value looks incomplete/invalid (a truncated email
    like ``foo@gmail.co..`` or a phone with too few digits) and reports it. Since
    the contacts PATCH merges by contactType, a dropped field leaves the existing
    Encompass value untouched.
    Returns (contact_obj, dropped_field_names).
    """
    obj: Dict[str, Any] = {"contactType": contact_type}
    dropped: List[str] = []
    for key in ("name", "contactName", "personalLicenseNumber"):
        val = (parsed.get(key) or "").strip()
        if val:
            obj[key] = val
    phone = (parsed.get("phone") or "").strip()
    if phone:
        if _looks_complete_phone(phone):
            obj["phone"] = phone
        else:
            dropped.append("phone")
    email = (parsed.get("email") or "").strip()
    if email:
        if _EMAIL_RE.match(email):
            obj["email"] = email
        else:
            dropped.append("email")
    return obj, dropped


def _sync_contacts_from_image(
    loan_id: str,
    state: dict,
    contact_map: Dict[str, Dict[str, Any]],
    required: List[tuple],
    flags: List[Dict[str, Any]],
) -> Dict[str, str]:
    """Create missing AND overwrite differing escrow/agent contacts from the image.

    For each populatable role present in the image:
      - missing in Encompass → create it;
      - present but differing → overwrite the differing fields.
    Fields whose OCR value looks incomplete/invalid are skipped; because the
    contacts PATCH merges by contactType, skipped fields keep their existing
    Encompass value. ``contact_map`` is updated in place to mirror the write so
    the caller's present/missing summary is accurate.

    Returns {contactType: "created"|"updated"} for contacts that were written.
    """
    images = (
        state.get("almas_notes_images")
        or (state.get("additional_info") or {}).get("almas_notes_images")
    )
    image_contacts = _parse_image_contacts(images)
    if not image_contacts:
        return {}

    # (ct, label, obj, dropped, mode, diffs)
    plans: List[tuple] = []
    for ct, label in required:
        if ct not in _IMAGE_POPULATABLE:
            continue
        parsed = image_contacts.get(ct)
        if not parsed or not (parsed.get("name") or parsed.get("contactName")):
            continue
        obj, dropped = _build_contact_obj(ct, parsed)
        if len(obj) <= 1:  # nothing valid beyond contactType
            continue
        existing = contact_map.get(ct)
        if not existing:
            plans.append((ct, label, obj, dropped, "created", {}))
            continue
        diffs = {}
        for k, v in obj.items():
            if k == "contactType":
                continue
            old = (existing.get(k) or "").strip()
            if old.lower() != v.strip().lower():
                diffs[k] = (old, v)
        if diffs:
            plans.append((ct, label, obj, dropped, "updated", diffs))

    if not plans:
        return {}

    try:
        from encompass_client import write_loan_contacts
        res = write_loan_contacts(loan_id, [obj for _, _, obj, _, _, _ in plans], state=state)
    except Exception as exc:
        logger.error(f"[REVIEW_FILE_CONTACTS] contacts write raised: {exc}")
        res = {"success": False, "error": str(exc)}

    if not res.get("success"):
        flags.append(_flag(
            title="File Contact Auto-Sync Failed",
            severity="warning",
            details=(
                f"Attempted to write {', '.join(l for _, l, _, _, _, _ in plans)} from the "
                f"cover-letter image but the contacts write failed: {res.get('error')}"
            ),
            suggestion="Manually update the contacts in Encompass File Contacts.",
        ))
        return {}

    written: Dict[str, str] = {}
    for ct, label, obj, dropped, mode, diffs in plans:
        written[ct] = mode
        # Mirror the server-side merge locally for an accurate present summary.
        merged = dict(contact_map.get(ct) or {})
        merged.update(obj)
        contact_map[ct] = merged
        summary = _contact_summary(merged)
        note = (
            f" Skipped {', '.join(dropped)} (incomplete/invalid in OCR — kept existing value)."
            if dropped else ""
        )
        if mode == "created":
            title = f"File Contact Populated from Image: {label}"
            details = (
                f"{label} ({ct}) was missing and has been written to Encompass File Contacts "
                f"from the cover-letter image OCR: {summary}.{note} "
                f"Source is image OCR — verify accuracy against the source document."
            )
        else:
            change = "; ".join(f"{k} '{old or '(empty)'}' → '{new}'" for k, (old, new) in diffs.items())
            title = f"File Contact Updated from Image: {label}"
            details = (
                f"{label} ({ct}) differed from the cover-letter image and was overwritten "
                f"({change}).{note} Source is image OCR — verify accuracy against the source document."
            )
        flags.append({
            "substep": SUBSTEP,
            "title": title,
            "severity": "info-overwrite",
            "details": details,
            "suggestion": f"Verify the {label} details in Encompass File Contacts.",
            "resolved": False,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        logger.info(f"[REVIEW_FILE_CONTACTS] [{ct}] {mode} from image — {summary}")
    return written


@tool
def review_file_contacts(
    tool_call_id: Annotated[str, InjectedToolCallId],
    state: Annotated[dict, InjectedState],
) -> Command:
    """Verify that the required file contacts are assigned in Encompass.

    Checks via GET /v3/loans/{loanId}/contacts. Required contacts vary by
    loan purpose:
      - Purchase: Buyer's Agent, Seller's Agent, Seller 1, Escrow Company
      - Refi / all other: Escrow Company only

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

    # ── Determine required contacts based on loan purpose ──
    loan_purpose = _los(state, "loan_purpose") or ""
    required_contacts = _required_contacts(loan_purpose)
    logger.info(f"[REVIEW_FILE_CONTACTS] Loan purpose: '{loan_purpose}' — "
                f"checking {len(required_contacts)} contact(s): "
                f"{[c['label'] for c in required_contacts]}")

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
    required_pairs = [(r["type"], r["label"]) for r in required_contacts]

    # ── Sync escrow/agent contacts from the cover-letter image (Gap A) ──
    # Creates missing contacts and overwrites present-but-differing ones; OCR
    # fields that look incomplete/invalid are skipped (existing value preserved).
    written = _sync_contacts_from_image(loan_id, state, contact_map, required_pairs, flags)

    # ── Check each required contact against the (post-sync) contact map ──
    present: List[str] = []
    missing: List[str] = []

    for ct, label in required_pairs:
        contact = contact_map.get(ct)
        if contact:
            tag = ""
            if written.get(ct) == "created":
                tag = " (populated from image)"
            elif written.get(ct) == "updated":
                tag = " (updated from image)"
            summary = _contact_summary(contact)
            present.append(f"  • {label}: {summary}{tag}")
            logger.info(f"[REVIEW_FILE_CONTACTS] [{ct}] OK{tag} — {summary}")
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
