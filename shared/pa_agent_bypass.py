"""Purchase Agreement agent/brokerage extraction — download-bypass path (Gap C).

Why this exists
---------------
The Purchase Agreement *does* bind via the catchingDoc finder and the pipeline extracts
it, but the server schema models the agents as **nested objects** with generic field
descriptions. The result is mislabeled on state-specific layouts — on South Carolina
contracts the seller's-agent block ("Seller's Agent Name/License #" line + "LLR Office
Code" column + "Notice Email/Address" block) came back with only a phone number, and the
"LLR Office Code" (the brokerage/company license) was dumped into ``mls_id``.

This module bypasses that by sending the PA PDF straight to LandingAI with a **flat,
SC-aware schema** (``output/config/pa_agents_schema.json``). Flat keys are required
because LandingAI rejects nested-object schemas (HTTP 422); they also extract more
reliably and map 1:1 onto File Contact fields. Verified on loan 2605968646: the seller's
agent now returns the correct name (Joe M Scaturro), individual license (121177) and
brokerage office code (27547), separated correctly.

The buyer's agent is usually covered better by the ESS Contact Information table (Gap B);
this bypass primarily fills the **seller's-agent license/office-code gap** that the ESS
table leaves empty.

Requires ``LANDINGAI_API_KEY`` in the deployment env (same key as Gap B); returns ``None``
gracefully if unset or on any failure.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Reuse the shared download / LandingAI / nullable helpers from the ESS bypass.
from shared.ess_contact_bypass import (
    _download_attachment,
    _find_attachment,
    _landingai_extract,
    _make_nullable,
)

_PA_BUCKET_TITLES = {
    "purchase agreement",
    "purchase contract",
    "sales contract",
    "contract",
    "purchase and sale agreement",
}

_CONFIG_DIR = Path(__file__).resolve().parent.parent / "output" / "config"
_BUNDLED_SCHEMA = _CONFIG_DIR / "pa_agents_schema.json"


def _load_pa_agents_schema() -> Optional[dict]:
    """Load the flat SC-aware PA agents schema (nullable types)."""
    try:
        with open(_BUNDLED_SCHEMA) as f:
            bundled = json.load(f)
        return {
            "type": "object",
            "properties": _make_nullable(bundled.get("properties", {})),
            "required": [],
        }
    except Exception as exc:
        logger.error(f"[PA_BYPASS] Could not load schema {_BUNDLED_SCHEMA}: {exc}")
        return None


def extract_pa_agents(loan_id: str, state: Optional[dict] = None) -> Optional[dict]:
    """Extract buyer's/seller's agent fields from the Purchase Agreement.

    Returns a flat ``{buyer_agent_*: value, seller_agent_*: value}`` map with non-empty
    values only, or ``None`` if the attachment is absent, no ``LANDINGAI_API_KEY`` is set,
    or any step fails. Always best-effort — never raises.
    """
    if not os.getenv("LANDINGAI_API_KEY"):
        logger.warning("[PA_BYPASS] LANDINGAI_API_KEY not set — skipping PA agent extraction.")
        return None

    try:
        from encompass_client import get_encompass_client

        client = get_encompass_client(state=state)

        att_id = _find_attachment(client, loan_id, _PA_BUCKET_TITLES, "PA_BYPASS")
        if not att_id:
            logger.info("[PA_BYPASS] No attachment found in the Purchase Agreement bucket.")
            return None

        schema = _load_pa_agents_schema()
        if not schema or not schema.get("properties"):
            return None

        pdf = _download_attachment(client, loan_id, att_id)
        if not pdf:
            return None

        logger.info(f"[PA_BYPASS] Extracting {len(pdf):,} bytes via LandingAI...")
        fields = _landingai_extract(pdf, schema)
        agents = {
            k: v for k, v in fields.items()
            if (k.startswith("buyer_agent_") or k.startswith("seller_agent_"))
            and v not in (None, "", "null")
        }
        logger.info(f"[PA_BYPASS] Extracted {len(agents)} non-empty agent field(s).")
        return agents or None
    except Exception as exc:
        logger.error(f"[PA_BYPASS] Bypass extraction failed: {exc}")
        return None
