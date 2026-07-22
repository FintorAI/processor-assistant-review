"""HasData Zillow API client — best-effort property lookup for PUD + new-construction.

Automates the manual "Go to Zillow" step a processor does to decide whether a
subject property sits in a Planned Unit Development, and to spot new construction
(< 1 year old). Given an address, it queries HasData's Zillow Listing API (which
handles proxy rotation / CAPTCHA on Zillow's behalf — raw requests + BeautifulSoup
against zillow.com gets 403'd) and returns the public-record facts that matter:

  - home_type / structure_type  (SingleFamily / Townhouse / Condo / …)
  - has_attached_property        (shared wall → attached dwelling)
  - hoa_fee                      (HOA dues present → community/association)
  - subdivision                  (community/subdivision name → PUD project name)
  - year_built                   (for new-construction detection)
  - is_new_construction_flag     (Zillow's own boolean — bonus only)

API docs: https://docs.hasdata.com/apis/zillow/listing
Auth: HASDATA_API_KEY in the environment (x-api-key header).

The client is intentionally best-effort: if the key is missing or the call fails
it returns ``found=False`` and the caller falls back to the Encompass-only
heuristic + a Zillow deep-link. No exceptions propagate to the agent.
"""

import logging
import os
from dataclasses import dataclass, field
from datetime import date
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

# Single-family markers used by FHA Management "1 Unit" confirmation.
_SINGLE_FAMILY_HOME_TYPES = (
    "singlefamily", "single_family", "single family", "detached",
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
    subdivision: Optional[str] = None
    community_features: List[str] = field(default_factory=list)
    # Verified live (HasData 2026-07-22): prop.yearBuilt / resoData.yearBuilt,
    # plus atAGlanceFacts "Year Built". resoData.isNewConstruction is inconsistently
    # populated (False on some listings, None/absent on others) — bonus signal only.
    year_built: Optional[int] = None
    is_new_construction_flag: Optional[bool] = None


def _parse_hoa(reso: dict) -> Optional[str]:
    """Pull an HOA dues value out of Zillow's atAGlanceFacts, if present."""
    for fact in reso.get("atAGlanceFacts") or []:
        label = (fact.get("factLabel") or "").lower()
        if "hoa" in label:
            val = fact.get("factValue")
            if val and str(val).strip().lower() not in ("", "no data", "none", "$0/mo"):
                return str(val).strip()
    return None


def _parse_year_built(prop: dict, reso: dict) -> Optional[int]:
    """Pull year built from prop / resoData / atAGlanceFacts (verified live)."""
    raw = prop.get("yearBuilt") or reso.get("yearBuilt")
    if raw is None:
        for fact in reso.get("atAGlanceFacts") or []:
            label = (fact.get("factLabel") or "").lower()
            if "year built" in label:
                raw = fact.get("factValue")
                break
    if raw is None:
        return None
    try:
        year = int(str(raw).strip()[:4])
    except (TypeError, ValueError):
        return None
    # Sanity: ignore garbage years outside a plausible residential range.
    if 1700 <= year <= date.today().year + 2:
        return year
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


def is_new_construction(facts: ZillowPropertyFacts, as_of: Optional[date] = None) -> bool:
    """True when the listing looks like new construction (< ~1 year old).

    Primary signal: ``year_built`` within the last calendar year of ``as_of``.
    Bonus: Zillow's ``isNewConstruction`` boolean when True (never used alone —
    live tests showed it is ``None``/absent on plenty of legitimate non-new
    listings rather than ``False``).
    """
    if not facts.found:
        return False
    today = as_of or date.today()
    if facts.year_built is not None and facts.year_built >= today.year - 1:
        return True
    if facts.is_new_construction_flag is True:
        return True
    return False


def looks_single_family(facts: ZillowPropertyFacts) -> bool:
    """True when Zillow home/structure type looks like a detached single-family."""
    if not facts.found:
        return False
    ht = (facts.home_type or "").lower().replace(" ", "").replace("_", "")
    st = (facts.structure_type or "").lower().replace(" ", "").replace("_", "")
    if any(t in ht or t in st for t in _ATTACHED_HOME_TYPES):
        return False
    return any(
        t.replace(" ", "").replace("_", "") in ht or t.replace(" ", "").replace("_", "") in st
        for t in _SINGLE_FAMILY_HOME_TYPES
    ) or (ht == "singlefamily" or st == "singlefamily")


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

        # Redacted location token for logs — never log the full street address.
        log_loc = loc or "unknown area"
        try:
            logger.info(f"[ZILLOW] Looking up property in {log_loc}")
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
                subdivision=(
                    reso.get("subdivisionName")
                    or prop.get("subdivisionName")
                    or reso.get("subdivision")
                ),
                community_features=reso.get("communityFeatures") or [],
                year_built=_parse_year_built(prop, reso),
                is_new_construction_flag=(
                    reso.get("isNewConstruction")
                    if isinstance(reso.get("isNewConstruction"), bool)
                    else prop.get("isNewConstruction")
                    if isinstance(prop.get("isNewConstruction"), bool)
                    else None
                ),
            )
            logger.info(
                f"[ZILLOW] Found property ({log_loc}) — type={facts.home_type}, "
                f"attached={facts.has_attached_property}, hoa={facts.hoa_fee}, "
                f"subdivision={facts.subdivision!r}, year_built={facts.year_built}, "
                f"is_new={facts.is_new_construction_flag}"
            )
            return facts
        except Exception as e:  # noqa: BLE001 — best-effort, never break the agent
            logger.warning(f"[ZILLOW] Lookup failed ({log_loc}): {e}")
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
