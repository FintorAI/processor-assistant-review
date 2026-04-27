"""Plan code operations using the Encompass Docs API.

Provides:
- fetch_plan_codes(): GET all plan codes from the instance
- match_plan_code(): fuzzy-match extracted Lock Confirmation data to a plan code
- apply_plan_code(): POST the selected plan code to a loan
"""

import logging
import os
import re
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

try:
    from encompass_client import get_encompass_client
except ImportError:
    get_encompass_client = None
    logger.warning("encompass_client not available — plan code operations will fail")


def _get_token(state: dict = None) -> str:
    if get_encompass_client is None:
        raise RuntimeError("encompass_client not available")
    ec = get_encompass_client(state=state)
    return ec.access_token


def _base_url() -> str:
    return os.getenv("ENCOMPASS_API_BASE_URL", "https://api.elliemae.com").rstrip("/")


def fetch_plan_codes(
    plan_code_type: str = "closing",
    state: dict = None,
) -> List[Dict[str, Any]]:
    """Fetch all plan codes from the Encompass Docs API.

    Returns a list of plan code dicts with keys:
        id, code, description, investorName, loanType,
        amortizationType, lienPosition, investorPlanCode, ...
    """
    token = _get_token(state)
    url = f"{_base_url()}/encompassdocs/v1/planCodes"
    params = {"planCodeType": plan_code_type}
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    resp = requests.get(url, headers=headers, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    plans = data.get("plan", [])
    logger.info(f"[PLAN_CODES] Fetched {len(plans)} plan codes (type={plan_code_type})")
    return plans


def _extract_investor_from_program(loan_program: str) -> Tuple[str, str]:
    """Parse investor name from loan program strings.

    Supports two common Encompass formats:
      '{Investor} - {Program Details}'  (e.g. 'Nevada Housing Division - HIP Worker...')
      '{Investor}: {Program Details}'   (e.g. 'Arc Home: 30 Year Fixed Rate...')

    Returns (investor_name, remaining_program_text).
    """
    if not loan_program:
        return "", ""
    parts = loan_program.split(" - ", 1)
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip()
    parts = loan_program.split(": ", 1)
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip()
    return "", loan_program.strip()


def match_plan_code(
    plans: List[Dict[str, Any]],
    product_name: str = "",
    investor_name: str = "",
    loan_type: str = "",
    amortization_type: str = "",
    lien_position: str = "",
    loan_term: str = "",
    investor_plan_code: str = "",
    loan_program: str = "",
) -> Optional[Dict[str, Any]]:
    """Match loan attributes against available plan codes.

    Matching strategy (in priority order):
    1. Exact investor_plan_code match (if provided)
    2. Parse investor from loan_program string if investor_name not given
    3. Filter by investor_name + loan_type + lien_position, then
       fuzzy-match product_name/loan_program against description
    4. Fall back to best fuzzy match across all filtered candidates

    Returns the best-matching plan code dict, or None.
    """
    if not plans:
        return None

    # If no investor_name but loan_program is available, parse investor from it
    program_remainder = ""
    if not investor_name and loan_program:
        parsed_investor, program_remainder = _extract_investor_from_program(loan_program)
        if parsed_investor:
            investor_name = parsed_investor
            logger.info(f"[PLAN_CODES] Parsed investor from loan program: '{investor_name}'")

    # Clean the remainder for matching: strip "Correspondent:" prefix, then
    # extract the core product from the last " - " segment (DPA qualifiers
    # like "HIP Worker Advantage - 0% Discount w/DPA" appear before the core).
    _match_text = program_remainder or loan_program
    if _match_text.lower().startswith("correspondent:"):
        _match_text = _match_text[len("correspondent:"):].strip()

    _segments = _match_text.split(" - ")
    _core_product = _segments[-1].strip() if len(_segments) > 1 else _match_text
    _full_remainder_norm = _norm(_match_text)
    _full_remainder_words = set(re.findall(r"\w+", _full_remainder_norm))
    _has_fthb = bool({"fthb", "firsthome"} & _full_remainder_words)

    effective_product = product_name or _core_product

    # Normalize inputs
    product_norm = _norm(effective_product)
    investor_norm = _norm(investor_name)
    loan_type_norm = _norm(loan_type)
    amort_norm = _norm(amortization_type)
    lien_norm = _norm(lien_position)
    term_norm = _norm(loan_term)
    inv_code_norm = _norm(investor_plan_code)

    # Strategy 1: exact investor plan code
    if inv_code_norm:
        for p in plans:
            if _norm(p.get("investorPlanCode", "")) == inv_code_norm:
                logger.info(f"[PLAN_CODES] Exact investorPlanCode match: {p['code']} — {p['description']}")
                return p
            if _norm(p.get("code", "")) == inv_code_norm or _norm(p.get("id", "")) == inv_code_norm:
                logger.info(f"[PLAN_CODES] Exact code/id match: {p['code']} — {p['description']}")
                return p

    # Strategy 1b: loan_program itself may be a short code (e.g. "V30", "C30", "F30").
    # Try matching it against code/investorPlanCode directly before fuzzy matching.
    lp_norm = _norm(loan_program)
    if lp_norm and len(loan_program.strip()) <= 10:
        for p in plans:
            if _norm(p.get("code", "")) == lp_norm or _norm(p.get("investorPlanCode", "")) == lp_norm:
                logger.info(f"[PLAN_CODES] Direct code match for '{loan_program}': {p['code']} — {p['description']}")
                return p

    # Strategy 1c: expand known short-code patterns into descriptive text for fuzzy matching.
    # Common: V30=VA 30 Year Fixed, C30=Conventional 30 Year Fixed, F30=FHA 30 Year Fixed, etc.
    _SHORT_CODE_MAP = {
        "v": "VA", "c": "Conventional", "f": "FHA", "u": "USDA",
    }
    _short_match = re.match(r"^([A-Za-z])(\d+)$", loan_program.strip())
    if _short_match and not product_name:
        prefix_char = _short_match.group(1).lower()
        term_digits = _short_match.group(2)
        expanded_type = _SHORT_CODE_MAP.get(prefix_char)
        if expanded_type:
            effective_product = f"{expanded_type} {term_digits} Year Fixed Rate"
            product_norm = _norm(effective_product)
            if not loan_type_norm:
                loan_type_norm = _norm(expanded_type)
            if not term_norm:
                term_norm = term_digits
            logger.info(f"[PLAN_CODES] Expanded short code '{loan_program}' -> '{effective_product}'")

    # Strategy 2: filter, then fuzzy match
    candidates = plans

    if investor_norm:
        filtered = [p for p in candidates if investor_norm in _norm(p.get("investorName", ""))]
        if filtered:
            candidates = filtered
            logger.info(f"[PLAN_CODES] Filtered by investor '{investor_name}': {len(candidates)} candidates")

    if loan_type_norm:
        lt_map = {
            "FHA": "FHA", "VA": "VA", "CONVENTIONAL": "Conventional",
            "USDA": "FarmersHomeAdministration",
        }
        api_lt = lt_map.get(loan_type_norm.upper(), loan_type_norm)
        filtered = [p for p in candidates if _norm(p.get("loanType", "")) == _norm(api_lt)]
        if filtered:
            candidates = filtered
            logger.info(f"[PLAN_CODES] Filtered by loanType '{api_lt}': {len(candidates)} candidates")

    if lien_norm:
        lien_map = {
            "FIRST": "FIRSTLIEN", "FIRST LIEN": "FIRSTLIEN", "1": "FIRSTLIEN", "1ST": "FIRSTLIEN",
            "SECOND": "SECONDLIEN", "SECOND LIEN": "SECONDLIEN", "2": "SECONDLIEN", "2ND": "SECONDLIEN",
        }
        api_lien = lien_map.get(lien_norm.upper(), lien_norm.replace(" ", ""))
        filtered = [p for p in candidates if _norm(p.get("lienPosition", "")).replace(" ", "") == _norm(api_lien)]
        if filtered:
            candidates = filtered

    if amort_norm:
        filtered = [p for p in candidates if _norm(p.get("amortizationType", "")) == amort_norm]
        if filtered:
            candidates = filtered

    if not candidates:
        logger.warning("[PLAN_CODES] No candidates after filtering")
        return None

    if not product_norm:
        if len(candidates) == 1:
            match = candidates[0]
            logger.info(f"[PLAN_CODES] Single candidate after filter: {match['code']} — {match['description']}")
            return match
        logger.warning(f"[PLAN_CODES] {len(candidates)} candidates but no product_name to rank by")
        return None

    # Fuzzy match product_name against description
    product_words = set(re.findall(r"\w+", product_norm))
    scored: List[Tuple[float, Dict]] = []
    for p in candidates:
        desc_norm = _norm(p.get("description", ""))
        ratio = SequenceMatcher(None, product_norm, desc_norm).ratio()

        desc_words = set(re.findall(r"\w+", desc_norm))
        if product_words and desc_words:
            overlap = len(product_words & desc_words) / max(len(product_words), 1)
            ratio = ratio * 0.6 + overlap * 0.4

        if term_norm and term_norm in desc_norm:
            ratio += 0.05

        inv_pc = p.get("investorPlanCode", "")
        if inv_pc and inv_pc.isdigit() and len(inv_pc) <= 5:
            ratio += 0.08

        if _has_fthb and "firsthome" in desc_norm:
            ratio += 0.15

        if _full_remainder_words and desc_words:
            full_overlap = len(_full_remainder_words & desc_words) / max(len(_full_remainder_words), 1)
            ratio += full_overlap * 0.05

        scored.append((ratio, p))

    scored.sort(key=lambda x: x[0], reverse=True)
    best_score, best_match = scored[0]

    if best_score < 0.25:
        logger.warning(
            f"[PLAN_CODES] Best match score too low ({best_score:.2f}): "
            f"{best_match['code']} — {best_match['description']}"
        )
        return None

    logger.info(
        f"[PLAN_CODES] Best match (score={best_score:.2f}): "
        f"{best_match['code']} — {best_match['description']}"
    )
    return best_match


def apply_plan_code(
    loan_id: str,
    plan_code_id: str,
    state: dict = None,
) -> Dict[str, Any]:
    """Apply a plan code to a loan via the Encompass Docs API.

    POST /encompassdocs/v1/planCodes/{planCodeID}/evaluator

    Returns the API response (status, conflicts if any).
    In dry_run mode, returns a simulated SUCCESS without calling the API.
    """
    dry_run = False
    try:
        from output.registry import DEV_MODE
        dry_run = getattr(DEV_MODE, "dry_run", False)
    except Exception:
        pass

    if dry_run:
        logger.info(f"[DRY-RUN] Would apply plan code {plan_code_id} to loan {loan_id[:8]}")
        return {"status": "SUCCESS", "conflicts": [], "dry_run": True}

    token = _get_token(state)
    url = f"{_base_url()}/encompassdocs/v1/planCodes/{plan_code_id}/evaluator"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    body = {
        "entity": {
            "entityType": "urn:elli:encompass:loan",
            "entityId": loan_id,
        },
        "orderType": "closing",
        "import": "all",
    }

    logger.info(f"[PLAN_CODES] Applying plan code {plan_code_id} to loan {loan_id[:8]}...")
    resp = requests.post(url, headers=headers, json=body, timeout=30)

    if resp.status_code in (200, 202):
        result = resp.json()
        status = result.get("status", "UNKNOWN")
        conflicts = result.get("conflicts", [])
        logger.info(f"[PLAN_CODES] Apply result: {status}, {len(conflicts)} conflict(s)")
        return result
    elif resp.status_code == 409:
        result = resp.json()
        logger.warning(f"[PLAN_CODES] Apply CONFLICT: {result}")
        return result
    else:
        logger.error(f"[PLAN_CODES] Apply failed: {resp.status_code} — {resp.text[:300]}")
        resp.raise_for_status()
        return {}


def _norm(val: Any) -> str:
    """Normalize a string for comparison: lowercase, strip, collapse whitespace."""
    if not val:
        return ""
    return re.sub(r"\s+", " ", str(val).strip().lower())
