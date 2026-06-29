"""HasData Zillow API client — best-effort property lookup for PUD detection.

Automates the manual "Go to Zillow" step a processor does to decide whether a
subject property sits in a Planned Unit Development. Given an address, it queries
HasData's Zillow Listing API (which handles proxy rotation / CAPTCHA on Zillow's
behalf — raw requests + BeautifulSoup against zillow.com gets 403'd) and returns
the public-record facts that matter for PUD classification:

  - home_type / structure_type  (SingleFamily / Townhouse / Condo / …)
  - has_attached_property        (shared wall → attached dwelling)
  - hoa_fee                      (HOA dues present → community/association)

API docs: https://docs.hasdata.com/apis/zillow/listing
Auth: HASDATA_API_KEY in the environment (x-api-key header).

The client is intentionally best-effort: if the key is missing or the call fails
it returns ``found=False`` and the caller falls back to the Encompass-only
heuristic + a Zillow deep-link. No exceptions propagate to the agent.
"""

import logging
import os
from dataclasses import dataclass, field
from typing import List, Optional

import requests

logger = logging.getLogger(__name__)

LISTING_URL = "https://api.hasdata.com/scrape/zillow/listing"

# Zillow home/structure types that imply an attached / community dwelling and so
# raise the likelihood of a PUD on a non-condo loan. NOTE: Zillow's
# `hasAttachedProperty` boolean is unreliable (observed False for an end-of-row
# townhouse that clearly shares a wall), so we lean on home/structure type here —
# these substrings cover townhomes, rowhomes, duplexes/twins and condos.
_ATTACHED_HOME_TYPES = (
    "townhouse", "townhome", "row", "rowhouse", "duplex", "twin",
    "condo", "condominium", "multi", "apartment", "garden",
)


@dataclass
class ZillowPropertyFacts:
    found: bool = False
    error: Optional[str] = None
    url: Optional[str] = None
    home_type: Optional[str] = None
    structure_type: Optional[str] = None
    architectural_style: Optional[str] = None
    has_attached_property: Optional[bool] = None
    hoa_fee: Optional[str] = None
    community_features: List[str] = field(default_factory=list)


def _parse_hoa(reso: dict) -> Optional[str]:
    """Pull an HOA dues value out of Zillow's atAGlanceFacts, if present."""
    for fact in reso.get("atAGlanceFacts") or []:
        label = (fact.get("factLabel") or "").lower()
        if "hoa" in label:
            val = fact.get("factValue")
            if val and str(val).strip().lower() not in ("", "no data", "none", "$0/mo"):
                return str(val).strip()
    return None


def is_pud_indicative(facts: ZillowPropertyFacts) -> "tuple[bool, List[str]]":
    """Translate Zillow facts into human-readable PUD signals."""
    signals: List[str] = []
    if facts.has_attached_property:
        signals.append("Zillow: property has an attached structure (shared wall)")
    ht = (facts.home_type or "").lower()
    st = (facts.structure_type or "").lower()
    if any(t in ht or t in st for t in _ATTACHED_HOME_TYPES):
        signals.append(f"Zillow home/structure type = {facts.home_type or facts.structure_type}")
    if facts.hoa_fee:
        signals.append(f"Zillow shows HOA dues ({facts.hoa_fee})")
    return (len(signals) > 0, signals)


class ZillowClient:
    """Thin HasData Zillow Listing API client. Disabled gracefully w/o a key."""

    def __init__(self, api_key: Optional[str] = None, timeout: int = 25):
        self.api_key = api_key or os.getenv("HASDATA_API_KEY")
        self.timeout = timeout
        self.enabled = bool(self.api_key)
        if not self.enabled:
            logger.info("[ZILLOW] HASDATA_API_KEY not set — Zillow lookups disabled")

    def lookup(
        self,
        street: Optional[str],
        city: Optional[str] = None,
        state: Optional[str] = None,
        zip_code: Optional[str] = None,
    ) -> ZillowPropertyFacts:
        if not self.enabled:
            return ZillowPropertyFacts(found=False, error="HASDATA_API_KEY not set")
        if not street or not str(street).strip():
            return ZillowPropertyFacts(found=False, error="no street address")

        loc = " ".join(p for p in (str(state or "").strip(), str(zip_code or "").strip()) if p)
        keyword = ", ".join(p for p in (str(street).strip(), str(city or "").strip(), loc) if p)

        try:
            logger.info(f"[ZILLOW] Looking up: {keyword!r}")
            resp = requests.get(
                LISTING_URL,
                headers={"Content-Type": "application/json", "x-api-key": self.api_key},
                params={"keyword": keyword, "type": "forSale"},
                timeout=self.timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            prop = data.get("property") or {}
            if not prop:
                return ZillowPropertyFacts(
                    found=False,
                    error="no property in HasData response",
                    url=(data.get("requestMetadata") or {}).get("url"),
                )
            reso = prop.get("resoData") or {}
            facts = ZillowPropertyFacts(
                found=True,
                url=prop.get("url"),
                home_type=prop.get("homeType") or reso.get("homeType"),
                structure_type=reso.get("structureType"),
                architectural_style=reso.get("architecturalStyle"),
                has_attached_property=reso.get("hasAttachedProperty"),
                hoa_fee=_parse_hoa(reso),
                community_features=reso.get("communityFeatures") or [],
            )
            logger.info(
                f"[ZILLOW] Found {facts.url} — type={facts.home_type}, "
                f"attached={facts.has_attached_property}, hoa={facts.hoa_fee}"
            )
            return facts
        except Exception as e:  # noqa: BLE001 — best-effort, never break the agent
            logger.warning(f"[ZILLOW] Lookup failed for {keyword!r}: {e}")
            return ZillowPropertyFacts(found=False, error=str(e))


_client_instance: Optional[ZillowClient] = None


def get_zillow_client() -> ZillowClient:
    global _client_instance
    if _client_instance is None:
        _client_instance = ZillowClient()
    return _client_instance


def lookup_property_sync(
    street: Optional[str],
    city: Optional[str] = None,
    state: Optional[str] = None,
    zip_code: Optional[str] = None,
) -> ZillowPropertyFacts:
    """Convenience wrapper around the singleton client."""
    return get_zillow_client().lookup(street, city, state, zip_code)
