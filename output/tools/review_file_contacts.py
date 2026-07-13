"""review_file_contacts — Tool for substep 1.2: File Contacts Check

Step 1 (STEP_01): Pre-Checks
Phase: INTAKE

# FACTORY-LOCK: true
"""

import json
import logging
import re
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


# ── Image + Settlement Statement → File Contacts (Gap A + Gap B) ─────────────
# Two sources feed the escrow/agent File Contacts, in priority order:
#
#   1. Settlement statement (Gap B) — the ESS / pre-CD page-5 "Contact
#      Information" table. Its contact_* fields arrive in state["doc_fields"]
#      when CatchingDoc extracts a real ESS; on pre-CD loans (a CD-format file in
#      the ESS bucket that the finder won't bind) we fall back to a direct
#      download→LandingAI bypass (shared/ess_contact_bypass.py). The ESS table is
#      RICHER than the image — it has company + contact license #s and the full
#      address — so it takes precedence per field.
#
#   2. Cover-letter image (Gap A) — the frontend OCRs the image Almas attaches to
#      her notes (substep 0.6) into state["almas_notes_images"][i]["extracted_text"],
#      a screenshot of Encompass's Roles & Contacts panel. We parse its "KEY
#      CONTACTS" section to fill any gaps the settlement statement didn't cover.
#
# Merged per field (settlement statement wins): missing contacts are created and
# present-but-differing contacts are overwritten. Any field whose value looks
# incomplete/invalid (truncated email/phone) is skipped — and because the
# contacts PATCH merges by contactType, a skipped field keeps its existing
# Encompass value. Every create/overwrite is flagged for review with its source.

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
    lines = [ln.rstrip() for ln in (text or "").splitlines()]
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


# Plain string fields copied verbatim (settlement statement supplies licenses +
# address; the image only supplies name/contactName/personalLicenseNumber).
_PLAIN_CONTACT_FIELDS = (
    "name", "contactName", "bizLicenseNumber", "personalLicenseNumber",
    "referenceNumber", "address", "city", "state", "postalCode",
)


# Human-readable labels for the contact fields we write (used in flag bullets).
_FIELD_LABEL = {
    "name": "Company Name",
    "contactName": "Contact",
    "address": "Address",
    "city": "City",
    "state": "State",
    "postalCode": "ZIP",
    "phone": "Phone",
    "fax": "Fax",
    "email": "Email",
    "bizLicenseNumber": "Company License #",
    "personalLicenseNumber": "License #",
    "referenceNumber": "Escrow Case #",
}

_ADDRESS_FIELDS = ("address", "city", "state", "postalCode")

_US_STATES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID", "IL",
    "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT",
    "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI",
    "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY", "DC",
}

# Street-suffix tokens that mark the end of the street name.
_STREET_SUFFIX = {
    "st", "street", "ave", "avenue", "blvd", "boulevard", "rd", "road", "dr",
    "drive", "ln", "lane", "ct", "court", "cir", "circle", "way", "pl", "place",
    "pkwy", "parkway", "ter", "terrace", "hwy", "highway", "trl", "trail", "sq",
    "square", "loop", "pike", "row", "run", "path", "crossing", "xing", "pt",
    "point", "plz", "plaza",
}
# Unit designators that belong to the street line (come after the street suffix).
_UNIT_PREFIX = {
    "ste", "suite", "apt", "apartment", "unit", "bldg", "building", "fl",
    "floor", "rm", "room", "no", "lot", "#",
}
# Abbreviation → canonical form, applied token-wise for address comparison only.
_ADDR_ABBR = {
    "st": "street", "str": "street", "ave": "avenue", "av": "avenue",
    "blvd": "boulevard", "rd": "road", "dr": "drive", "ln": "lane",
    "ct": "court", "cir": "circle", "pkwy": "parkway", "hwy": "highway",
    "ter": "terrace", "pl": "place", "sq": "square", "plz": "plaza",
    "ste": "suite", "apt": "apartment", "bldg": "building", "fl": "floor",
    "rm": "room", "no": "number", "n": "north", "s": "south", "e": "east",
    "w": "west", "ne": "northeast", "nw": "northwest", "se": "southeast",
    "sw": "southwest",
}


def _is_unit_token(tok: str) -> bool:
    norm = re.sub(r"[^a-z#]", "", tok.lower())
    if not norm:
        return False
    if norm.startswith("#"):
        return True
    # Bare unit word, or unit glued to its identifier (e.g. "ste.140" -> "ste140").
    for u in _UNIT_PREFIX:
        if u == "#":
            continue
        if norm == u or (norm.startswith(u) and norm[len(u):].isdigit()):
            return True
    return False


def _split_address(full: str) -> Dict[str, str]:
    """Best-effort split of a one-line US address into components.

    Returns a dict with any of ``address`` (street + unit), ``city``, ``state``,
    ``postalCode`` that could be reliably parsed. ZIP and state are extracted via
    regex; the street/city boundary is found at the last street-suffix token (e.g.
    'Drive', 'Boulevard') plus any following unit ('#1020', 'Ste.140'). Returns an
    empty dict when no structure can be parsed (caller keeps the raw string).

    Examples:
        '20251 Century Boulevard Ste.140 Germantown, MD 20874'
            -> address='20251 Century Boulevard Ste.140', city='Germantown',
               state='MD', postalCode='20874'
        '1441 McCormick Drive #1020 Upper Marlboro, MD 20774'
            -> address='1441 McCormick Drive #1020', city='Upper Marlboro',
               state='MD', postalCode='20774'
    """
    s = re.sub(r"\s+", " ", (full or "").strip())
    if not s:
        return {}
    out: Dict[str, str] = {}

    m = re.search(r"(\d{5}(?:-\d{4})?)\s*$", s)
    if m:
        out["postalCode"] = m.group(1)
        s = s[: m.start()].strip().rstrip(",").strip()

    m = re.search(r"[,\s]+([A-Za-z]{2})\.?$", s)
    if m and m.group(1).upper() in _US_STATES:
        out["state"] = m.group(1).upper()
        s = s[: m.start()].strip().rstrip(",").strip()

    # Remaining = "street [unit] city". The street ends at the last street-suffix
    # token (e.g. 'Circle', 'Drive') plus any trailing unit ('Ste 220', '#1020').
    tokens = s.split(" ")
    last_suffix = -1
    for i, tok in enumerate(tokens):
        if re.sub(r"[^a-z]", "", tok.lower()) in _STREET_SUFFIX:
            last_suffix = i
    if last_suffix >= 0:
        j = last_suffix + 1
        while j < len(tokens):
            tok = tokens[j]
            if tok.lstrip().startswith("#"):
                j += 1
                continue
            norm = re.sub(r"[^a-z]", "", tok.lower())
            if norm in _UNIT_PREFIX or (
                norm and any(norm.startswith(u) and norm[len(u):] == "" for u in _UNIT_PREFIX)
            ):
                j += 1
                # consume the unit's identifier (e.g. 'Ste 220', 'Apt B')
                if j < len(tokens):
                    nxt = re.sub(r"[^a-z0-9]", "", tokens[j].lower())
                    if nxt and (any(c.isdigit() for c in nxt) or len(nxt) <= 2):
                        j += 1
                continue
            if _is_unit_token(tok):  # glued form, e.g. 'Ste.140', '#1020'
                j += 1
                continue
            break
        street = " ".join(tokens[:j]).strip().rstrip(",").strip()
        city = " ".join(tokens[j:]).strip().rstrip(",").strip()
        if street:
            out["address"] = street
        if city:
            out["city"] = city
    elif "," in s:  # no recognizable street suffix → fall back to comma boundary
        street, _, city = s.rpartition(",")
        if street.strip():
            out["address"] = street.strip().rstrip(",").strip()
        if city.strip():
            out["city"] = city.strip()
    else:
        out["address"] = s
    return {k: v for k, v in out.items() if v}


def _norm_addr(s: str) -> str:
    """Normalize an address string for equality comparison (abbrev + punctuation)."""
    s = (s or "").lower()
    s = re.sub(r"[.,#]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return " ".join(_ADDR_ABBR.get(tok, tok) for tok in s.split(" ") if tok)


def _join_addr(d: Dict[str, Any]) -> str:
    return " ".join(
        str(d.get(k) or "").strip() for k in _ADDRESS_FIELDS if str(d.get(k) or "").strip()
    )


def _diff_bullets(diffs: Dict[str, tuple]) -> str:
    return "\n".join(
        f"  • {_FIELD_LABEL.get(k, k)}: '{old or '(empty)'}' → '{new}'"
        for k, (old, new) in diffs.items()
    )


def _field_bullets(obj: Dict[str, Any]) -> str:
    return "\n".join(
        f"  • {_FIELD_LABEL.get(k, k)}: '{v}'"
        for k, v in obj.items()
        if k != "contactType" and v
    )


def _build_contact_obj(contact_type: str, parsed: Dict[str, str]) -> tuple:
    """Build an Encompass contact object from parsed (merged) source fields.

    Drops any field whose value looks incomplete/invalid (a truncated email
    like ``foo@gmail.co..`` or a phone with too few digits) and reports it. Since
    the contacts PATCH merges by contactType, a dropped field leaves the existing
    Encompass value untouched.
    Returns (contact_obj, dropped_field_names).
    """
    parsed = dict(parsed)  # don't mutate the caller's merged source

    # Settlement statement / OCR usually give a single full address string. Split
    # it into street/city/state/ZIP so we don't cram the whole line into `address`.
    full_addr = (parsed.get("address") or "").strip()
    if full_addr:
        comp = _split_address(full_addr)
        if comp.get("city") or comp.get("state") or comp.get("postalCode"):
            parsed["address"] = comp.get("address", full_addr)
            for k in ("city", "state", "postalCode"):
                if comp.get(k) and not (parsed.get(k) or "").strip():
                    parsed[k] = comp[k]

    obj: Dict[str, Any] = {"contactType": contact_type}
    dropped: List[str] = []
    for key in _PLAIN_CONTACT_FIELDS:
        val = (parsed.get(key) or "").strip()
        if val:
            obj[key] = val
    phone = (parsed.get("phone") or "").strip()
    if phone:
        if _looks_complete_phone(phone):
            obj["phone"] = phone
        else:
            dropped.append("phone")
    # Emails can't contain whitespace; a stray space is almost always a PDF
    # line-wrap artifact (e.g. "grandstrandlaw group.com" where "group" wrapped
    # to the next line), so collapse internal whitespace before validating.
    email = re.sub(r"\s+", "", (parsed.get("email") or "").strip())
    if email:
        if _EMAIL_RE.match(email):
            obj["email"] = email
        else:
            dropped.append("email")
    return obj, dropped


# ── Settlement statement (ESS / pre-CD) contact source (Gap B) ──────────────
# ESS page-5 column prefix → File Contact type.
_ESS_COLUMN_TO_CT = {
    "settlement_agent": "ESCROW_COMPANY",
    "real_estate_broker_buyer": "BUYERS_AGENT",
    "real_estate_broker_seller": "SELLERS_AGENT",
}
# ESS contact_* sub-key → Encompass contact field.
_ESS_SUBKEY_TO_FIELD = {
    "name": "name",
    "contact": "contactName",
    "address": "address",
    "phone": "phone",
    "email": "email",
    "st_license_id": "bizLicenseNumber",            # company license #
    "contact_st_license_id": "personalLicenseNumber",  # individual license #
    # Settlement File # → Escrow Case #. Written via the File Contacts API as
    # referenceNumber (verified to be the same field as Encompass loan field 186).
    "file_number": "referenceNumber",
}

# ── Purchase Agreement agent source (Gap C) ─────────────────────────────────
# Flat PA keys (from the bundled SC-aware schema / shared/pa_agent_bypass.py)
# {side}_agent_* → File Contact type + field. The PA primarily fills the
# seller's-agent license/office-code gap the ESS table leaves empty.
_PA_SIDE_TO_CT = {"buyer": "BUYERS_AGENT", "seller": "SELLERS_AGENT"}
_PA_SUBKEY_TO_FIELD = {
    "company": "name",
    "name": "contactName",
    "office_code": "bizLicenseNumber",       # LLR Office Code = company license
    "license": "personalLicenseNumber",       # individual agent license
    "address": "address",
    "phone": "phone",
    "email": "email",
}


def _doc(state: dict, key: str) -> str:
    """Read a normalized doc_fields value (``state['doc_fields'][key]['value']``)."""
    entry = (state.get("doc_fields") or {}).get(key)
    if isinstance(entry, dict):
        return str(entry.get("value") or "").strip()
    return str(entry or "").strip()


def _contacts_from_getter(getter) -> Dict[str, Dict[str, str]]:
    """Build {contactType: {field: value}} from a contact_* key getter."""
    out: Dict[str, Dict[str, str]] = {}
    for col, ct in _ESS_COLUMN_TO_CT.items():
        obj: Dict[str, str] = {}
        for sub, field in _ESS_SUBKEY_TO_FIELD.items():
            val = str(getter(f"contact_{col}_{sub}") or "").strip()
            if val:
                obj[field] = val
        if obj.get("name") or obj.get("contactName"):
            out[ct] = obj
    return out


def _contacts_from_pa(getter) -> Dict[str, Dict[str, str]]:
    """Build {contactType: {field: value}} from flat PA {side}_agent_* keys."""
    out: Dict[str, Dict[str, str]] = {}
    for side, ct in _PA_SIDE_TO_CT.items():
        obj: Dict[str, str] = {}
        for sub, field in _PA_SUBKEY_TO_FIELD.items():
            val = str(getter(f"{side}_agent_{sub}") or "").strip()
            if val:
                obj[field] = val
        if obj.get("name") or obj.get("contactName"):
            out[ct] = obj
    return out


def _ess_contacts(state: dict, loan_id: str) -> Dict[str, Dict[str, str]]:
    """ESS / pre-CD settlement statement contacts (doc_fields, else bypass)."""
    doc = _contacts_from_getter(lambda k: _doc(state, k))
    if doc:
        return doc
    try:
        from shared.ess_contact_bypass import extract_ess_contacts
        raw = extract_ess_contacts(loan_id, state)
    except Exception as exc:
        logger.warning(f"[REVIEW_FILE_CONTACTS] ESS contact bypass error: {exc}")
        raw = None
    return _contacts_from_getter(lambda k: raw.get(k, "")) if raw else {}


def _pa_agents(state: dict, loan_id: str, ess: Dict[str, Dict[str, str]]) -> Dict[str, Dict[str, str]]:
    """Purchase Agreement agents (doc_fields flat keys, else gated bypass).

    The bypass (a LandingAI call) only fires for purchase loans when the ESS
    didn't already supply both agents *with* an individual license number — the
    PA's main value is the seller's-agent license + LLR office code that the ESS
    Contact Information table leaves blank.
    """
    pa = _contacts_from_pa(lambda k: _doc(state, k))
    if pa:
        return pa
    if "purchase" not in str(_los(state, "loan_purpose") or "").lower():
        return {}
    needs_pa = any(
        not ess.get(ct) or not (ess[ct].get("personalLicenseNumber"))
        for ct in ("BUYERS_AGENT", "SELLERS_AGENT")
    )
    if not needs_pa:
        return {}
    try:
        from shared.pa_agent_bypass import extract_pa_agents
        raw = extract_pa_agents(loan_id, state)
    except Exception as exc:
        logger.warning(f"[REVIEW_FILE_CONTACTS] PA agent bypass error: {exc}")
        raw = None
    return _contacts_from_pa(lambda k: raw.get(k, "")) if raw else {}


def _parse_doc_contacts(state: dict, loan_id: str) -> Dict[str, Dict[str, str]]:
    """Extract escrow/agent contacts from the document sources, merged.

    Sources (per-field priority, highest first):
      1. ESS / pre-CD settlement statement (`_ess_contacts`) — richest table.
      2. Purchase Agreement agents (`_pa_agents`) — fills the seller's-agent
         license / LLR office code the ESS table leaves blank.

    Each source reads `state['doc_fields']` first and falls back to a
    download→LandingAI bypass. Always best-effort; returns {} on total failure so
    the image-OCR path still runs.
    """
    ess = _ess_contacts(state, loan_id)
    pa = _pa_agents(state, loan_id, ess)
    if not ess and not pa:
        return {}
    merged: Dict[str, Dict[str, str]] = {}
    for ct in set(ess) | set(pa):
        obj = dict(pa.get(ct) or {})                       # PA as base
        obj.update({k: v for k, v in (ess.get(ct) or {}).items() if v})  # ESS overrides
        merged[ct] = obj
    return merged


def _sync_contacts(
    loan_id: str,
    state: dict,
    contact_map: Dict[str, Dict[str, Any]],
    required: List[tuple],
    flags: List[Dict[str, Any]],
) -> Dict[str, str]:
    """Create/overwrite escrow & agent contacts from the settlement statement + image.

    For each populatable role the two sources are merged **per field** with the
    settlement statement (ESS / pre-CD) winning over the cover-letter image:
      - missing in Encompass → create it;
      - present but differing → overwrite the differing fields.
    Fields whose value looks incomplete/invalid are skipped; because the contacts
    PATCH merges by contactType, skipped fields keep their existing Encompass value.
    ``contact_map`` is updated in place to mirror the write so the caller's
    present/missing summary is accurate.

    Returns {contactType: "created"|"updated"} for contacts that were written.
    """
    images = (
        state.get("almas_notes_images")
        or (state.get("additional_info") or {}).get("almas_notes_images")
    )
    image_contacts = _parse_image_contacts(images)
    doc_contacts = _parse_doc_contacts(state, loan_id)
    if not image_contacts and not doc_contacts:
        return {}

    # (ct, label, obj, dropped, mode, diffs, source)
    plans: List[tuple] = []
    for ct, label in required:
        if ct not in _IMAGE_POPULATABLE:
            continue
        img = image_contacts.get(ct) or {}
        doc = doc_contacts.get(ct) or {}
        if not img and not doc:
            continue
        # Merge: image first, document extraction overrides per field.
        merged_src: Dict[str, str] = dict(img)
        merged_src.update({k: v for k, v in doc.items() if v})
        source = "settlement statement / purchase agreement" if doc else "cover-letter image"
        if not (merged_src.get("name") or merged_src.get("contactName")):
            continue
        obj, dropped = _build_contact_obj(ct, merged_src)
        if len(obj) <= 1:  # nothing valid beyond contactType
            continue
        existing = contact_map.get(ct)
        if not existing:
            plans.append((ct, label, obj, dropped, "created", {}, source))
            continue
        diffs = {}
        for k, v in obj.items():
            if k == "contactType" or k in _ADDRESS_FIELDS:
                continue
            old = (existing.get(k) or "").strip()
            if old.lower() != str(v).strip().lower():
                diffs[k] = (old, v)
        # Address group: compare the *full* reconstructed address (street + city +
        # state + ZIP) so a pure formatting difference (e.g. "Blvd" vs "Boulevard",
        # or the doc cramming city/state/ZIP into one line) does NOT trigger an
        # overwrite. Only flag/write the differing components on a real discrepancy.
        if any(k in obj for k in _ADDRESS_FIELDS):
            new_addr = {**existing, **{k: obj[k] for k in _ADDRESS_FIELDS if k in obj}}
            if _norm_addr(_join_addr(existing)) != _norm_addr(_join_addr(new_addr)):
                for k in _ADDRESS_FIELDS:
                    if k not in obj:
                        continue
                    old = (existing.get(k) or "").strip()
                    new = str(obj[k]).strip()
                    if old.lower() != new.lower():
                        diffs[k] = (old, new)
        if diffs:
            # Only send the fields we actually decided to write (keeps the contacts
            # PATCH from clobbering address components we judged equal).
            write_obj = {"contactType": ct}
            write_obj.update({k: obj[k] for k in diffs if k in obj})
            plans.append((ct, label, write_obj, dropped, "updated", diffs, source))

    if not plans:
        return {}

    try:
        from encompass_client import write_loan_contacts
        res = write_loan_contacts(loan_id, [p[2] for p in plans], state=state)
    except Exception as exc:
        logger.error(f"[REVIEW_FILE_CONTACTS] contacts write raised: {exc}")
        res = {"success": False, "error": str(exc)}

    if not res.get("success"):
        flags.append(_flag(
            title="File Contact Auto-Sync Failed",
            severity="warning",
            details=(
                f"Attempted to write {', '.join(p[1] for p in plans)} from the "
                f"settlement statement / cover-letter image but the contacts write "
                f"failed: {res.get('error')}"
            ),
            suggestion="Manually update the contacts in Encompass File Contacts.",
        ))
        return {}

    written: Dict[str, str] = {}
    for ct, label, obj, dropped, mode, diffs, source in plans:
        written[ct] = mode
        # Mirror the server-side merge locally for an accurate present summary.
        merged = dict(contact_map.get(ct) or {})
        merged.update(obj)
        contact_map[ct] = merged
        summary = _contact_summary(merged)
        note = (
            f" Skipped {', '.join(dropped)} (incomplete/invalid in {source} — kept existing value)."
            if dropped else ""
        )
        if mode == "created":
            title = f"File Contact Populated from {source.title()}: {label}"
            details = (
                f"{label} ({ct}) was missing and has been written to Encompass File "
                f"Contacts from the {source}:\n"
                f"{_field_bullets(obj)}\n"
                f"{note + chr(10) if note else ''}"
                f"Verify accuracy against the source document."
            )
        else:
            title = f"File Contact Updated from {source.title()}: {label}"
            details = (
                f"{label} ({ct}) differed from the {source} and the following "
                f"field(s) were overwritten:\n"
                f"{_diff_bullets(diffs)}\n"
                f"{note + chr(10) if note else ''}"
                f"Verify accuracy against the source document."
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
        logger.info(f"[REVIEW_FILE_CONTACTS] [{ct}] {mode} from {source} — {summary}")
    return written


def _sync_seller_addresses(
    loan_id: str,
    state: dict,
    contact_map: Dict[str, Dict[str, Any]],
    flags: List[Dict[str, Any]],
) -> Dict[str, str]:
    """Populate the Seller file contacts' address with the subject property.

    Per processor convention the Seller file contacts (Seller 1 = ``SELLER``,
    Seller 2 = ``SELLER2``) carry the **subject property address**. Reads the LOS
    subject-property address (fields 11/12/14/15) and writes
    ``address/city/state/postalCode`` to each *existing* seller contact, overwriting
    differing fields. Purchase loans only.

    Only enriches seller contacts that already exist — it never fabricates a Seller 1
    or Seller 2 (a genuinely missing seller still raises the "Missing File Contact"
    warning). Emits an ``info-overwrite`` flag per contact written.
    """
    if "purchase" not in str(_los(state, "loan_purpose") or "").lower():
        return {}

    addr: Dict[str, str] = {}
    for los_key, field in (
        ("property_address", "address"),
        ("property_city", "city"),
        ("property_state", "state"),
        ("property_zip", "postalCode"),
    ):
        val = str(_los(state, los_key) or "").strip()
        if val:
            addr[field] = val
    if not addr.get("address"):
        return {}

    targets = [
        (ct, label)
        for ct, label in (("SELLER", "Seller 1"), ("SELLER2", "Seller 2"))
        if contact_map.get(ct)
    ]
    if not targets:
        return {}

    plans: List[tuple] = []
    for ct, label in targets:
        existing = contact_map.get(ct) or {}
        diffs = {}
        for k, v in addr.items():
            old = (existing.get(k) or "").strip()
            if old.lower() != v.strip().lower():
                diffs[k] = (old, v)
        if not diffs:
            continue
        obj = {"contactType": ct}
        obj.update(addr)
        plans.append((ct, label, obj, diffs))

    if not plans:
        return {}

    try:
        from encompass_client import write_loan_contacts
        res = write_loan_contacts(loan_id, [p[2] for p in plans], state=state)
    except Exception as exc:
        logger.error(f"[REVIEW_FILE_CONTACTS] seller address write raised: {exc}")
        res = {"success": False, "error": str(exc)}

    if not res.get("success"):
        flags.append(_flag(
            title="Seller Address Auto-Sync Failed",
            severity="warning",
            details=(
                f"Attempted to write the subject property address to "
                f"{', '.join(p[1] for p in plans)} but the contacts write failed: "
                f"{res.get('error')}"
            ),
            suggestion="Manually set the Seller 1 / Seller 2 address in Encompass File Contacts.",
        ))
        return {}

    addr_str = ", ".join(
        v for v in (addr.get("address"), addr.get("city"),
                    addr.get("state"), addr.get("postalCode")) if v
    )
    written: Dict[str, str] = {}
    for ct, label, obj, diffs in plans:
        written[ct] = "updated"
        merged = dict(contact_map.get(ct) or {})
        merged.update(obj)
        contact_map[ct] = merged
        flags.append({
            "substep": SUBSTEP,
            "title": f"File Contact Updated from Subject Property: {label} Address",
            "severity": "info-overwrite",
            "details": (
                f"{label} ({ct}) address was set to the subject property address "
                f"({addr_str}) — the following field(s) were written:\n"
                f"{_diff_bullets(diffs)}\n"
                f"Verify this matches the seller's intended address."
            ),
            "suggestion": f"Verify the {label} address in Encompass File Contacts.",
            "resolved": False,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        logger.info(f"[REVIEW_FILE_CONTACTS] [{ct}] address set from subject property — {addr_str}")
    return written


# ── Title Insurance Company File Contact (Title Report source) ───────────────
# The Title Report server schema carries the title company (name + license),
# the commitment/order number, and the issuing agent / contact. We write these
# into the Encompass TITLE_INSURANCE_COMPANY file contact so the processor
# doesn't have to key them by hand (checklist 02 #2 "Update Title Insurance
# Company — license + order #"). The reference doc is the Title Report / commitment.
_TITLE_CT = "TITLE_INSURANCE_COMPANY"


def _doc_obj(state: dict, key: str) -> Dict[str, Any]:
    """Read a nested doc_fields object (e.g. Title Report ``title_company``).

    The extractor stores the object as the field's ``value`` (a dict), or
    occasionally as a JSON string. Returns a plain dict, or {} if absent/unparseable.
    """
    entry = (state.get("doc_fields") or {}).get(key)
    val = entry.get("value") if isinstance(entry, dict) else entry
    if isinstance(val, dict):
        return val
    if isinstance(val, str) and val.strip().startswith("{"):
        try:
            parsed = json.loads(val)
            return parsed if isinstance(parsed, dict) else {}
        except (ValueError, TypeError):
            return {}
    return {}


def _title_company_source(state: dict) -> Dict[str, str]:
    """Build the Title Insurance Company contact fields from the Title Report.

    Merges the nested ``title_company`` object with the top-level
    ``commitment_number`` (order/commitment ref), ``issuing_agent``, and the
    nested ``contact`` object. Returns only non-empty fields.
    """
    tc = _doc_obj(state, "title_company")
    contact = _doc_obj(state, "contact")

    def g(d: Dict[str, Any], *keys: str) -> str:
        for k in keys:
            v = str((d or {}).get(k) or "").strip()
            if v:
                return v
        return ""

    src: Dict[str, str] = {
        "name": g(tc, "company_name"),
        "bizLicenseNumber": g(tc, "license_number"),
        "referenceNumber": _doc(state, "commitment_number"),
        "contactName": g(contact, "contact_name") or _doc(state, "issuing_agent"),
        "personalLicenseNumber": g(contact, "contact_license_number"),
        "phone": g(tc, "phone_number") or g(contact, "phone"),
        "email": g(tc, "email") or g(contact, "email"),
        "address": g(tc, "address"),
        "city": g(tc, "city"),
        "state": g(tc, "state"),
        "postalCode": g(tc, "postal_code", "postalCode", "zip"),
    }
    return {k: v for k, v in src.items() if v}


def _sync_title_company(
    loan_id: str,
    state: dict,
    contact_map: Dict[str, Dict[str, Any]],
    flags: List[Dict[str, Any]],
) -> Dict[str, str]:
    """Create/update the TITLE_INSURANCE_COMPANY file contact from the Title Report.

    Behaviour mirrors ``_sync_contacts``: create when missing, overwrite only the
    fields that genuinely differ (address compared as a whole so formatting-only
    differences don't trigger a write), and emit one ``info-overwrite`` flag per
    write. No-op when the Title Report supplies no usable title-company data, so a
    file without a title report (e.g. early refi) never gets a spurious flag.
    """
    src = _title_company_source(state)
    # Require at least a company name or a license/order number to act on.
    if not (src.get("name") or src.get("bizLicenseNumber") or src.get("referenceNumber")):
        return {}

    # Validate phone/email the same way as the other contacts; drop bad values so
    # the PATCH merge keeps whatever Encompass already has.
    dropped: List[str] = []
    obj: Dict[str, Any] = {"contactType": _TITLE_CT}
    for k in ("name", "contactName", "bizLicenseNumber", "personalLicenseNumber",
              "referenceNumber", "address", "city", "state", "postalCode"):
        if src.get(k):
            obj[k] = src[k]
    if src.get("phone"):
        if _looks_complete_phone(src["phone"]):
            obj["phone"] = src["phone"]
        else:
            dropped.append("phone")
    _email = re.sub(r"\s+", "", src.get("email", ""))
    if _email:
        if _EMAIL_RE.match(_email):
            obj["email"] = _email
        else:
            dropped.append("email")
    if len(obj) <= 1:  # nothing valid beyond contactType
        return {}

    existing = contact_map.get(_TITLE_CT)
    mode = "created"
    diffs: Dict[str, tuple] = {}
    write_obj = obj
    if existing:
        mode = "updated"
        for k, v in obj.items():
            if k == "contactType" or k in _ADDRESS_FIELDS:
                continue
            old = (existing.get(k) or "").strip()
            if old.lower() != str(v).strip().lower():
                diffs[k] = (old, v)
        if any(k in obj for k in _ADDRESS_FIELDS):
            new_addr = {**existing, **{k: obj[k] for k in _ADDRESS_FIELDS if k in obj}}
            if _norm_addr(_join_addr(existing)) != _norm_addr(_join_addr(new_addr)):
                for k in _ADDRESS_FIELDS:
                    if k in obj:
                        old = (existing.get(k) or "").strip()
                        new = str(obj[k]).strip()
                        if old.lower() != new.lower():
                            diffs[k] = (old, new)
        if not diffs:
            return {}
        write_obj = {"contactType": _TITLE_CT}
        write_obj.update({k: obj[k] for k in diffs if k in obj})

    try:
        from encompass_client import write_loan_contacts
        res = write_loan_contacts(loan_id, [write_obj], state=state)
    except Exception as exc:
        logger.error(f"[REVIEW_FILE_CONTACTS] title company write raised: {exc}")
        res = {"success": False, "error": str(exc)}

    if not res.get("success"):
        flags.append(_flag(
            title="Title Insurance Company Auto-Sync Failed",
            severity="warning",
            details=(
                "Attempted to write the Title Insurance Company file contact from the "
                f"Title Report but the contacts write failed: {res.get('error')}"
            ),
            suggestion="Manually update the Title Insurance Company in Encompass File Contacts.",
        ))
        return {}

    merged = dict(existing or {})
    merged.update(write_obj)
    contact_map[_TITLE_CT] = merged
    note = (
        f" Skipped {', '.join(dropped)} (incomplete/invalid on the Title Report — kept existing value)."
        if dropped else ""
    )
    if mode == "created":
        title = "File Contact Populated from Title Report: Title Insurance Company"
        details = (
            "The Title Insurance Company was missing and has been written to Encompass "
            "File Contacts from the Title Report / commitment:\n"
            f"{_field_bullets(write_obj)}\n"
            f"{note + chr(10) if note else ''}"
            "Verify accuracy against the title commitment."
        )
    else:
        title = "File Contact Updated from Title Report: Title Insurance Company"
        details = (
            "The Title Insurance Company differed from the Title Report and the following "
            "field(s) were overwritten:\n"
            f"{_diff_bullets(diffs)}\n"
            f"{note + chr(10) if note else ''}"
            "Verify accuracy against the title commitment."
        )
    flags.append({
        "substep": SUBSTEP,
        "title": title,
        "severity": "info-overwrite",
        "details": details,
        "suggestion": "Verify the Title Insurance Company details in Encompass File Contacts.",
        "resolved": False,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    logger.info(f"[REVIEW_FILE_CONTACTS] [{_TITLE_CT}] {mode} from Title Report — "
                f"{_contact_summary(merged)}")
    return {_TITLE_CT: mode}


# ── Insurance company File Contacts (Evidence of Insurance / Flood policy) ───
# 02 #6/#7: populate the Hazard/HOI and Flood insurance-company File Contacts from
# the extracted insurance docs. The doc field keys are the live CatchingDoc schema
# names surfaced in required_docs.json: Evidence of Insurance uses hazard_insurance_*
# / agent_email; the Flood doc uses the generic company_* / contact_* keys (which are
# collision-free in the flat doc_fields namespace). Both are create-or-overwrite with
# an info-overwrite flag, and no-op when the doc supplies no company name.
_HOI_CT = "HAZARD_INSURANCE"
_FLOOD_CT = "FLOOD_INSURANCE"

# (contactType, label, reference-doc label, {contact_field: [doc_keys…]}) —
# first non-empty doc key wins for each contact field.
_INSURANCE_SOURCES = [
    (_HOI_CT, "Hazard/HOI Insurance", "Evidence of Insurance", {
        "name": ["hazard_insurance_company", "insurance_carrier_name"],
        "contactName": ["hazard_insurance_contact"],
        "phone": ["hazard_insurance_phone"],
        "email": ["agent_email"],
        "fax": ["hazard_insurance_fax"],
        "address": ["hazard_insurance_address"],
        "city": ["hazard_insurance_city"],
        "state": ["hazard_insurance_state"],
        "postalCode": ["hazard_insurance_zip"],
    }),
    (_FLOOD_CT, "Flood Insurance", "Flood Insurance policy", {
        "name": ["flood_insurance_company"],
        "contactName": ["flood_insurance_contact"],
        "phone": ["flood_insurance_phone"],
        "address": ["flood_insurance_address"],
        "city": ["flood_insurance_city"],
        "state": ["flood_insurance_state"],
        "postalCode": ["flood_insurance_zip"],
    }),
]


def _insurance_src(state: dict, mapping: Dict[str, List[str]]) -> Dict[str, str]:
    """Build {contact_field: value} from the first non-empty extracted doc key.

    When a single-line ``address`` is supplied without discrete city/state/ZIP
    (the Flood policy case), split it into components so we don't cram the whole
    line into ``address``.
    """
    src: Dict[str, str] = {}
    for field, keys in mapping.items():
        for k in keys:
            v = _doc(state, k)
            if v:
                src[field] = v
                break
    full_addr = (src.get("address") or "").strip()
    if full_addr and not (src.get("city") or src.get("state") or src.get("postalCode")):
        comp = _split_address(full_addr)
        if comp.get("city") or comp.get("state") or comp.get("postalCode"):
            src["address"] = comp.get("address", full_addr)
            for k in ("city", "state", "postalCode"):
                if comp.get(k):
                    src[k] = comp[k]
    return src


def _upsert_contact(
    loan_id: str,
    state: dict,
    contact_map: Dict[str, Dict[str, Any]],
    flags: List[Dict[str, Any]],
    ct: str,
    label: str,
    ref_doc: str,
    src: Dict[str, str],
) -> Dict[str, str]:
    """Create/update a single File Contact ``ct`` from ``src`` (field → value).

    Mirrors ``_sync_title_company``: phone/email are validated and dropped if
    incomplete/invalid (the contacts PATCH merges by contactType, so a dropped
    field keeps the existing Encompass value); an existing contact is overwritten
    only on real per-field differences (address compared as a whole so a
    formatting-only difference does not trigger a write). Emits one
    ``info-overwrite`` flag per write. Returns {ct: "created"|"updated"} or {}.
    """
    dropped: List[str] = []
    obj: Dict[str, Any] = {"contactType": ct}
    for k in ("name", "contactName", "address", "city", "state", "postalCode"):
        if src.get(k):
            obj[k] = src[k]
    if src.get("fax"):
        obj["fax"] = src["fax"]
    if src.get("phone"):
        if _looks_complete_phone(src["phone"]):
            obj["phone"] = src["phone"]
        else:
            dropped.append("phone")
    _email = re.sub(r"\s+", "", src.get("email", ""))
    if _email:
        if _EMAIL_RE.match(_email):
            obj["email"] = _email
        else:
            dropped.append("email")
    if len(obj) <= 1:  # nothing valid beyond contactType
        return {}

    existing = contact_map.get(ct)
    mode = "created"
    diffs: Dict[str, tuple] = {}
    write_obj = obj
    if existing:
        mode = "updated"
        for k, v in obj.items():
            if k == "contactType" or k in _ADDRESS_FIELDS:
                continue
            old = (existing.get(k) or "").strip()
            if old.lower() != str(v).strip().lower():
                diffs[k] = (old, v)
        if any(k in obj for k in _ADDRESS_FIELDS):
            new_addr = {**existing, **{k: obj[k] for k in _ADDRESS_FIELDS if k in obj}}
            if _norm_addr(_join_addr(existing)) != _norm_addr(_join_addr(new_addr)):
                for k in _ADDRESS_FIELDS:
                    if k in obj:
                        old = (existing.get(k) or "").strip()
                        new = str(obj[k]).strip()
                        if old.lower() != new.lower():
                            diffs[k] = (old, new)
        if not diffs:
            return {}
        write_obj = {"contactType": ct}
        write_obj.update({k: obj[k] for k in diffs if k in obj})

    try:
        from encompass_client import write_loan_contacts
        res = write_loan_contacts(loan_id, [write_obj], state=state)
    except Exception as exc:
        logger.error(f"[REVIEW_FILE_CONTACTS] {ct} write raised: {exc}")
        res = {"success": False, "error": str(exc)}

    if not res.get("success"):
        flags.append(_flag(
            title=f"{label} Contact Auto-Sync Failed",
            severity="warning",
            details=(
                f"Attempted to write the {label} file contact ({ct}) from the "
                f"{ref_doc} but the contacts write failed: {res.get('error')}"
            ),
            suggestion=f"Manually update the {label} in Encompass File Contacts.",
        ))
        return {}

    merged = dict(existing or {})
    merged.update(write_obj)
    contact_map[ct] = merged
    note = (
        f" Skipped {', '.join(dropped)} (incomplete/invalid on the {ref_doc} — kept existing value)."
        if dropped else ""
    )
    if mode == "created":
        title = f"File Contact Populated from {ref_doc}: {label}"
        details = (
            f"{label} ({ct}) was missing and has been written to Encompass File "
            f"Contacts from the {ref_doc}:\n"
            f"{_field_bullets(write_obj)}\n"
            f"{note + chr(10) if note else ''}"
            f"Verify accuracy against the {ref_doc}."
        )
    else:
        title = f"File Contact Updated from {ref_doc}: {label}"
        details = (
            f"{label} ({ct}) differed from the {ref_doc} and the following field(s) "
            f"were overwritten:\n"
            f"{_diff_bullets(diffs)}\n"
            f"{note + chr(10) if note else ''}"
            f"Verify accuracy against the {ref_doc}."
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
    logger.info(f"[REVIEW_FILE_CONTACTS] [{ct}] {mode} from {ref_doc} — "
                f"{_contact_summary(merged)}")
    return {ct: mode}


def _sync_insurance_contacts(
    loan_id: str,
    state: dict,
    contact_map: Dict[str, Dict[str, Any]],
    flags: List[Dict[str, Any]],
) -> Dict[str, str]:
    """Populate the Hazard/HOI and Flood insurance-company File Contacts (02 #6/#7).

    Reads the extracted Evidence of Insurance (hazard) and Flood policy/certificate
    docs. Requires at least a company name to act, so a file with no insurance doc
    never gets a spurious write/flag.
    """
    written: Dict[str, str] = {}
    for ct, label, ref_doc, mapping in _INSURANCE_SOURCES:
        src = _insurance_src(state, mapping)
        if not src.get("name"):
            continue
        written.update(_upsert_contact(loan_id, state, contact_map, flags, ct, label, ref_doc, src))
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

    Auto-sync (each write raises an ``info-overwrite`` flag):
      - Escrow Company / Buyer's & Seller's Agent — created/overwritten from the
        settlement statement (ESS / pre-CD) and cover-letter image. Full address
        strings are split into street/city/state/ZIP; an existing address is only
        overwritten on a real discrepancy (formatting-only differences are ignored).
        The settlement File # ("File #: …") is written to the Escrow Company's
        Escrow Case # via the contacts API ``referenceNumber`` (the same field as
        Encompass loan field 186).
      - Seller 1 / Seller 2 — the subject property address (LOS fields 11/12/14/15)
        is written to each existing seller contact's address.
      - Title Insurance Company — created/overwritten from the Title Report:
        company name, company license (bizLicenseNumber), commitment/order #
        (referenceNumber), and issuing agent / contact (contactName +
        personalLicenseNumber) with phone/email/address. No-op when the Title
        Report supplies no title-company data.
      - Hazard/HOI Insurance (HAZARD_INSURANCE) and Flood Insurance
        (FLOOD_INSURANCE) — company name + phone + email (+ contact / address)
        written from the extracted Evidence of Insurance and Flood policy docs
        (checklist 02 #6 / #7). No-op when no company name is extracted.

    Every auto-write flag enumerates the written field(s) as a bullet list.

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

    # ── Sync escrow/agent contacts from settlement statement + image (Gap A+B) ──
    # Settlement statement (ESS / pre-CD page-5 table) is preferred over the
    # cover-letter image per field. Creates missing contacts and overwrites
    # present-but-differing ones; fields that look incomplete/invalid are skipped
    # (existing value preserved via the contacts-collection PATCH merge).
    written = _sync_contacts(loan_id, state, contact_map, required_pairs, flags)

    # ── Populate Seller 1/2 addresses with the subject property (purchase) ──
    written.update(_sync_seller_addresses(loan_id, state, contact_map, flags))

    # ── Populate Title Insurance Company from the Title Report (02 #2) ──
    # Writes company name + company license (bizLicenseNumber) + commitment/order #
    # (referenceNumber) + issuing agent/contact from the Title Report. No-op when
    # no title-company data is extracted.
    written.update(_sync_title_company(loan_id, state, contact_map, flags))

    # ── Populate HOI + Flood insurance company contacts (02 #6/#7) ──
    # Writes company name + phone + email (+ contact / address) to the
    # HAZARD_INSURANCE and FLOOD_INSURANCE file contacts from the extracted
    # Evidence of Insurance and Flood policy docs. No-op when no company name is
    # extracted for a given insurer.
    written.update(_sync_insurance_contacts(loan_id, state, contact_map, flags))

    # ── Check each required contact against the (post-sync) contact map ──
    present: List[str] = []
    missing: List[str] = []

    for ct, label in required_pairs:
        contact = contact_map.get(ct)
        if contact:
            tag = ""
            if written.get(ct) == "created":
                tag = " (auto-populated)"
            elif written.get(ct) == "updated":
                tag = " (auto-updated)"
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

    # ── §9.1: Purchase Contract seller name vs Encompass (doc-grounded) ──
    # Complements the internal LOS-vs-contact check above by grounding the seller
    # name against the extracted Purchase Agreement. Purchase loans only; loose
    # both-way containment so middle names / ordering don't false-flag.
    pa_seller_name = _doc(state, "pa_seller_name")
    if pa_seller_name and "purchase" in str(loan_purpose).lower():
        _enc_seller = (seller_1_name_los or seller_contact_name or "").strip()
        _pa = pa_seller_name.strip()
        if _enc_seller:
            if _pa.lower() not in _enc_seller.lower() and _enc_seller.lower() not in _pa.lower():
                flags.append(_flag(
                    title="Seller Name vs Purchase Contract",
                    severity="warning",
                    details=(
                        f"Purchase Contract seller '{_pa}' does not match the Encompass seller "
                        f"('{_enc_seller}')."
                    ),
                    suggestion="Confirm the seller name on the sales contract / title matches Encompass.",
                ))
        else:
            flags.append(_flag(
                title="Seller Name Missing in Encompass",
                severity="warning",
                details=(
                    f"Purchase Contract shows seller '{_pa}' but no Seller 1 name / SELLER contact "
                    f"is set in Encompass."
                ),
                suggestion="Add the Seller 1 name / SELLER file contact in Encompass.",
            ))

    # ── §9.1: Applicant name on the Title Report vesting (final_vesting) ──
    # The Tax Cert carries no borrower/seller name, but the Title Report's vested
    # parties live in `final_vesting`. Confirm the applicant surname appears there;
    # loose containment so vesting formatting / trustee wording never false-flags.
    final_vesting = _doc(state, "final_vesting")
    borr_last = (_los(state, "borrower_last_name") or "").strip()
    if final_vesting and borr_last:
        if borr_last.lower() not in final_vesting.lower():
            flags.append(_flag(
                title="Applicant Not Found in Title Vesting",
                severity="warning",
                details=(
                    f"Borrower surname '{borr_last}' was not found in the Title Report final "
                    f"vesting ('{final_vesting}')."
                ),
                suggestion="Confirm the applicant name on the title commitment matches Encompass.",
            ))
        else:
            flags.append(_flag(
                title="Applicant Confirmed on Title Vesting",
                severity="info",
                details=f"Borrower '{borr_last}' appears in the Title Report vesting.",
                suggestion="No action needed — applicant matches the title vesting.",
            ))

    # ── §9.1: Owner of record on the Tax Certificate / Tax Summary ──
    # The tax roll lists the current assessed owner: on a purchase that is the
    # SELLER, on a refi it is the BORROWER. Loose both-way containment so middle
    # names / ordering / trustee wording don't false-flag.
    tax_owner = _doc(state, "tax_owner_name")
    if tax_owner and tax_owner.strip():
        _to = tax_owner.strip()
        _to_l = _to.lower()
        if "purchase" in str(loan_purpose).lower():
            _enc_seller = (seller_1_name_los or seller_contact_name or "").strip()
            if _enc_seller:
                if _enc_seller.lower() not in _to_l and _to_l not in _enc_seller.lower():
                    flags.append(_flag(
                        title="Tax Cert Owner vs Seller",
                        severity="warning",
                        details=(
                            f"Tax certificate owner of record ('{_to}') does not match the "
                            f"Encompass seller ('{_enc_seller}'). On a purchase the tax-roll "
                            f"owner should be the seller."
                        ),
                        suggestion="Confirm the seller is the current owner of record on the tax cert / title.",
                    ))
                else:
                    flags.append(_flag(
                        title="Tax Cert Owner Confirmed (Seller)",
                        severity="info",
                        details=f"Tax certificate owner ('{_to}') matches the Encompass seller.",
                        suggestion="No action needed — tax-roll owner matches the seller.",
                    ))
        else:
            borr_last = (_los(state, "borrower_last_name") or "").strip()
            if borr_last and borr_last.lower() not in _to_l:
                flags.append(_flag(
                    title="Tax Cert Owner vs Borrower",
                    severity="warning",
                    details=(
                        f"Tax certificate owner of record ('{_to}') does not include the borrower "
                        f"surname ('{borr_last}'). On a refinance the tax-roll owner should be the "
                        f"borrower."
                    ),
                    suggestion="Confirm the borrower is the current owner of record on the tax cert / title.",
                ))
            elif borr_last:
                flags.append(_flag(
                    title="Tax Cert Owner Confirmed (Borrower)",
                    severity="info",
                    details=f"Tax certificate owner ('{_to}') includes the borrower surname.",
                    suggestion="No action needed — tax-roll owner matches the borrower.",
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
