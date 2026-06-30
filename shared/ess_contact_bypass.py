"""ESS / pre-CD Contact Information extraction — download-bypass path.

Why this exists
---------------
The processor files the settlement statement under the Encompass eFolder bucket
**"Estimated Settlement Statement"**, but on many loans the actual attachment is a
*Closing Disclosure* ("pre-CD"). CatchingDoc's finder will not bind that attachment
to either doc type:

* the **ESS** doc type uses LLM content classification and rejects CD-format content;
* the **Closing Disclosure** doc type's ``avoid_keywords`` skip the ESS bucket.

So the page-5 "Contact Information" table (settlement agent + buyer/seller real-estate
brokers, with license #s, addresses, phones, emails) never reaches ``state["doc_fields"]``
through the normal pipeline — even though the ESS schema already defines all 45
``contact_*`` fields.

This module bypasses the finder entirely: it downloads the attachment bytes from
Encompass and sends the PDF straight to LandingAI with the **contact-only ESS schema**
(``output/config/ess_contacts_schema.json`` — sourced from the rich
``Estimated Settlement Statement`` schema, NOT the stale ``ESS`` alias which only has
9 fields). Returns the extracted ``contact_*`` field map.

Requirements
------------
``LANDINGAI_API_KEY`` must be set in the deployment environment (the same key
LG-docsOrch uses). If it is absent the function logs a warning and returns ``None``
so callers degrade gracefully (fall back to the cover-letter image OCR / missing flags).
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# eFolder schema API (same host/token as shared/efolder_client.py)
_EFOLDER_API_BASE = os.getenv(
    "EFOLDER_API_BASE_URL", "https://1doxzxvey2.execute-api.us-west-1.amazonaws.com/prod"
)
_EFOLDER_AUTH_TOKEN = os.getenv("EFOLDER_API_TOKEN", "esfuse-token")
_LANDINGAI_API_URL = os.getenv(
    "LANDINGAI_API_URL", "https://api.va.landing.ai/v1/tools/agentic-document-analysis"
)

# Canonical schema DocumentType key (NOT the "ESS" alias, which is stale/9-field).
_ESS_SCHEMA_DOCTYPE = "Estimated Settlement Statement"

# Bucket titles (case-insensitive) that hold the settlement statement / pre-CD.
_ESS_BUCKET_TITLES = {
    "estimated settlement statement",
    "ess",
    "settlement statement",
    "preliminary settlement",
    "closing disclosure",  # pre-CD filed under the ESS bucket on some loans
}

_CONFIG_DIR = Path(__file__).resolve().parent.parent / "output" / "config"
_BUNDLED_SCHEMA = _CONFIG_DIR / "ess_contacts_schema.json"

# Fields we need downstream that the remote catchingDoc contact schema does not yet
# define. Named ``contact_settlement_agent_*`` so they survive the ``contact_*``
# filter and map onto the Escrow Company (settlement agent) File Contact. Injected
# into whichever schema (live or bundled) is sent to LandingAI.
_EXTRA_CONTACT_FIELDS = {
    "contact_settlement_agent_file_number": {
        "type": ["string", "null"],
        "description": (
            "The settlement / escrow File number shown in the 'Closing Information' "
            "header of the settlement statement / Closing Disclosure, labeled 'File #' "
            "(e.g. 'File #: 2610313'). This is the escrow case / file number that goes "
            "in the Escrow Company's 'Escrow Case #' field — NOT a license, NMLS, or "
            "loan number. Return null if not present."
        ),
    },
}


def _make_nullable(props: dict) -> dict:
    """Allow ``null`` for every property type.

    The ESS Contact Information table has empty columns on most files (e.g. the
    Mortgage Broker column). LandingAI validates its *own* extracted output
    against the schema we send and returns ``null`` for absent fields — but the
    source schema declares ``"type": "string"`` (non-nullable). That mismatch
    raises a ValidationError and makes LandingAI discard the ENTIRE result
    (HTTP 206, empty extracted_schema). Coercing each type to ``[type, "null"]``
    lets empty columns pass validation so the populated fields still come back.
    """
    out = {}
    for key, spec in props.items():
        if isinstance(spec, dict):
            spec = dict(spec)
            t = spec.get("type")
            if isinstance(t, str) and t != "null":
                spec["type"] = [t, "null"]
            elif isinstance(t, list) and "null" not in t:
                spec["type"] = t + ["null"]
        out[key] = spec
    return out


def _load_contacts_schema() -> Optional[dict]:
    """Load the contact-only ESS schema.

    Prefers the live schema (fetched by the correct ``Estimated Settlement Statement``
    DocumentType, filtered to ``contact_*``); falls back to the bundled config file so
    the bypass keeps working even if the schema API is down or the alias is wrong.
    """
    # Try the live schema first, keyed by the correct DocumentType.
    try:
        import urllib.parse

        encoded = urllib.parse.quote(_ESS_SCHEMA_DOCTYPE, safe="")
        url = f"{_EFOLDER_API_BASE}/efolder/schemas/{encoded}"
        resp = requests.get(
            url, headers={"Authorization": f"Bearer {_EFOLDER_AUTH_TOKEN}"}, timeout=15
        )
        if resp.status_code == 200:
            data = resp.json()
            schema_obj = data.get("schema", data)
            props = (schema_obj.get("extraction_schema", {}) or {}).get("properties", {})
            contact = {k: v for k, v in props.items() if k.startswith("contact_")}
            if len(contact) >= 20:  # sanity: the rich schema has ~45 contact fields
                for k in ("escrow_company", "title_company"):
                    if k in props:
                        contact[k] = props[k]
                for k, v in _EXTRA_CONTACT_FIELDS.items():
                    contact.setdefault(k, v)
                logger.info(f"[ESS_BYPASS] Loaded live contacts schema ({len(contact)} fields)")
                return {"type": "object", "properties": _make_nullable(contact), "required": []}
            logger.warning(
                f"[ESS_BYPASS] Live schema for {_ESS_SCHEMA_DOCTYPE!r} had only "
                f"{len(contact)} contact fields — using bundled schema instead."
            )
    except Exception as exc:
        logger.warning(f"[ESS_BYPASS] Live schema fetch failed ({exc}); using bundled schema.")

    # Fallback: bundled config. Inject the extra contact field(s) here too so the
    # offline schema matches the live-schema branch (the bundled file already
    # ships them, but setdefault keeps the two paths consistent if it drifts).
    try:
        with open(_BUNDLED_SCHEMA) as f:
            bundled = json.load(f)
        props = dict(bundled.get("properties", {}))
        for k, v in _EXTRA_CONTACT_FIELDS.items():
            props.setdefault(k, v)
        return {
            "type": "object",
            "properties": _make_nullable(props),
            "required": bundled.get("required", []),
        }
    except Exception as exc:
        logger.error(f"[ESS_BYPASS] Could not load bundled schema {_BUNDLED_SCHEMA}: {exc}")
        return None


def _find_attachment(client, loan_id: str, bucket_titles: set, log_prefix: str = "BYPASS") -> Optional[str]:
    """Return the first attachment entityId in any matching eFolder bucket, or None."""
    url = f"{client.api_base_url}/encompass/v3/loans/{loan_id}/documents"
    headers = {"Authorization": f"Bearer {client.access_token}", "Accept": "application/json"}
    resp = requests.get(url, headers=headers, timeout=20)
    if resp.status_code == 401:
        client.refresh_token()
        headers["Authorization"] = f"Bearer {client.access_token}"
        resp = requests.get(url, headers=headers, timeout=20)
    resp.raise_for_status()

    for doc in resp.json():
        title = (doc.get("title") or "").strip().lower()
        if title in bucket_titles:
            attachments = doc.get("attachments") or []
            if attachments:
                att_id = attachments[0].get("entityId")
                logger.info(
                    f"[{log_prefix}] bucket {doc.get('title')!r} -> attachment "
                    f"{attachments[0].get('entityName')!r}"
                )
                return att_id
    return None


def _find_ess_attachment(client, loan_id: str) -> Optional[str]:
    """Return the first attachment entityId in the ESS bucket, or None."""
    return _find_attachment(client, loan_id, _ESS_BUCKET_TITLES, "ESS_BYPASS")


def _download_attachment(client, loan_id: str, attachment_id: str) -> Optional[bytes]:
    """Download attachment PDF bytes via Encompass attachmentDownloadUrl."""
    url = f"{client.api_base_url}/encompass/v3/loans/{loan_id}/attachmentDownloadUrl"
    headers = {"Authorization": f"Bearer {client.access_token}", "Content-Type": "application/json"}
    resp = requests.post(url, json={"attachments": [attachment_id]}, headers=headers, timeout=30)
    if resp.status_code == 401:
        client.refresh_token()
        headers["Authorization"] = f"Bearer {client.access_token}"
        resp = requests.post(url, json={"attachments": [attachment_id]}, headers=headers, timeout=30)
    resp.raise_for_status()
    items = resp.json().get("attachments", [])
    if not items:
        return None
    item = items[0]
    pdf = requests.get(
        item["url"], headers={"Authorization": item.get("authorizationHeader", "")}, timeout=120
    )
    pdf.raise_for_status()
    if pdf.content[:4] != b"%PDF":
        logger.warning(f"[ESS_BYPASS] Downloaded bytes are not a PDF (first: {pdf.content[:16]!r})")
        return None
    return pdf.content


def _landingai_extract(pdf_bytes: bytes, schema: dict) -> dict:
    """Send PDF + schema to LandingAI; return the flat {field: value} map."""
    key = os.getenv("LANDINGAI_API_KEY", "")
    headers = {"Authorization": f"Basic {key}"}
    files = {"pdf": ("ess.pdf", pdf_bytes, "application/pdf")}
    data = {"fields_schema": json.dumps(schema)}
    resp = requests.post(_LANDINGAI_API_URL, files=files, data=data, headers=headers, timeout=300)
    resp.raise_for_status()
    extracted = (resp.json().get("data") or {}).get("extracted_schema") or {}
    out = {}
    for k, v in extracted.items():
        out[k] = v.get("value") if isinstance(v, dict) else v
    return out


def extract_ess_contacts(loan_id: str, state: Optional[dict] = None) -> Optional[dict]:
    """Extract the ESS / pre-CD page-5 Contact Information table for a loan.

    Downloads the attachment from the "Estimated Settlement Statement" eFolder bucket
    and runs it through LandingAI with the contact-only ESS schema, bypassing the
    CatchingDoc finder (which won't bind a CD-format pre-CD).

    Returns:
        ``{contact_*: value, ...}`` with non-empty values only, or ``None`` if the
        attachment is absent, no ``LANDINGAI_API_KEY`` is configured, or any step fails.
        Always best-effort — never raises.
    """
    if not os.getenv("LANDINGAI_API_KEY"):
        logger.warning(
            "[ESS_BYPASS] LANDINGAI_API_KEY not set — skipping ESS contact extraction. "
            "Set it in the deployment env to enable the pre-CD contact bypass."
        )
        return None

    try:
        from encompass_client import get_encompass_client

        client = get_encompass_client(state=state)

        att_id = _find_ess_attachment(client, loan_id)
        if not att_id:
            logger.info("[ESS_BYPASS] No attachment found in the ESS bucket.")
            return None

        schema = _load_contacts_schema()
        if not schema or not schema.get("properties"):
            return None

        pdf = _download_attachment(client, loan_id, att_id)
        if not pdf:
            return None

        logger.info(f"[ESS_BYPASS] Extracting {len(pdf):,} bytes via LandingAI...")
        fields = _landingai_extract(pdf, schema)
        contacts = {
            k: v for k, v in fields.items()
            if k.startswith("contact_") and v not in (None, "", "null")
        }
        logger.info(f"[ESS_BYPASS] Extracted {len(contacts)} non-empty contact field(s).")
        return contacts or None
    except Exception as exc:
        logger.error(f"[ESS_BYPASS] Bypass extraction failed: {exc}")
        return None
