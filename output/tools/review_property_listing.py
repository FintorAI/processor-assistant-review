"""review_property_listing — Tool for substep 1.3: Property Verification (Address + Listing / PUD)

Step 1 (STEP_01): Pre-Checks
Phase: INTAKE

Consolidates property verification into Pre-Checks:
  1. USPS Address API v3 deliverability + Purchase Contract street-number
     cross-check (moved from former STEP_00 0.5 validate_property_address).
     Stores the same state['address_validation'] shape so existing consumers
     (review_borrower_summary, review_flood_hazard_insurance, build_action_items)
     need no changes.
  2. Live Zillow/HasData public-records lookup for PUD signals and
     new-construction (year_built / isNewConstruction).
  3. Stores both results in state['property_verification'] so Transmittal
     Summary (11.1), FHA Management (12.1), and HUD Transmittal (12.2) can
     consume them without re-querying.

Flag-only — no Encompass writes in Pre-Checks.

# FACTORY-LOCK: true
"""

import json
import logging
from datetime import datetime, timezone
from typing import Annotated

from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.prebuilt import InjectedState
from langgraph.types import Command

from ._helpers import (
    _detect_new_construction,
    _detect_pud_signals,
    _doc,
    _los,
    _lookup_zillow_facts,
)

logger = logging.getLogger(__name__)

SUBSTEP = "1.3"


def _flag(flags: list, title: str, severity: str, details: str, suggestion: str,
          resolved: bool = False) -> None:
    flags.append({
        "substep": SUBSTEP,
        "title": title,
        "severity": severity,
        "details": details,
        "suggestion": suggestion,
        "resolved": resolved,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


def _run_usps_validation(state: dict) -> dict:
    """USPS deliverability check + Purchase Contract street-number cross-check.

    Preserves the exact state['address_validation'] shape formerly produced by
    STEP_00 validate_property_address so downstream consumers need no changes.
    """
    from shared.usps_validator import validate_address_sync

    street = (_los(state, "property_address") or "")
    street = str(street).strip() if street else ""
    city = (_los(state, "property_city") or "")
    city = str(city).strip() if city else ""
    state_ = (_los(state, "property_state") or "")
    state_ = str(state_).strip() if state_ else ""
    zip_ = (_los(state, "property_zip") or "")
    zip_ = str(zip_).strip() if zip_ else ""
    purchase_contract_address = (_doc(state, "purchase_property_address") or "")
    purchase_contract_address = (
        str(purchase_contract_address).strip() if purchase_contract_address else ""
    )

    if not street:
        logger.warning(
            "[REVIEW_PROPERTY_LISTING] Skipping USPS — property_address not in los_fields"
        )
        return {
            "valid": None,
            "skipped": True,
            "skip_reason": (
                "property_address not in los_fields — fetch_los_fields may not have "
                "run yet or failed"
            ),
            "normalized": None,
            "mismatch_with_purchase_contract": None,
            "purchase_contract_address": purchase_contract_address or None,
        }

    logger.info(f"[REVIEW_PROPERTY_LISTING] USPS validating: {street}, {city}, {state_} {zip_}")
    try:
        usps = validate_address_sync(
            street_address=street,
            city=city or None,
            state=state_ or None,
            zip_code=zip_ or None,
        )
        normalized = None
        if usps.standardized_address:
            std = usps.standardized_address
            normalized = " ".join(filter(None, [
                std.get("street"),
                std.get("city"),
                std.get("state"),
                std.get("zip"),
            ]))

        mismatch = False
        if purchase_contract_address and street:
            los_num = street.split()[0] if street else ""
            doc_num = purchase_contract_address.split()[0] if purchase_contract_address else ""
            if los_num and doc_num and los_num != doc_num:
                mismatch = True

        return {
            "valid": usps.success and usps.dpv_confirmation in ("Y", "S", "D"),
            "normalized": normalized,
            "dpv_confirmation": usps.dpv_confirmation,
            "error": usps.error,
            "warnings": usps.warnings or [],
            "mismatch_with_purchase_contract": mismatch,
            "purchase_contract_address": purchase_contract_address or None,
            "los_address": f"{street}, {city}, {state_} {zip_}".strip(", "),
        }
    except Exception as e:  # noqa: BLE001 — never break the agent on USPS failure
        logger.error(f"[REVIEW_PROPERTY_LISTING] USPS call failed: {e}")
        return {
            "valid": None,
            "error": str(e),
            "normalized": None,
            "mismatch_with_purchase_contract": None,
            "purchase_contract_address": purchase_contract_address or None,
            "los_address": f"{street}, {city}, {state_} {zip_}".strip(", "),
        }


@tool
def review_property_listing(
    tool_call_id: Annotated[str, InjectedToolCallId],
    state: Annotated[dict, InjectedState],
) -> Command:
    """Consolidate property verification into Pre-Checks.

    (1) USPS Address API v3 deliverability + Purchase Contract street-number
        cross-check (moved from former STEP_00 0.5).
    (2) Live Zillow/HasData public-records lookup for PUD signals and
        new-construction (year_built / isNewConstruction).
    (3) Store both results in state so Transmittal Summary (11.1), FHA
        Management (12.1), and HUD Transmittal (12.2) can consume them without
        re-querying. Flag-only — no Encompass writes in Pre-Checks.

    Call this tool during STEP_01 (Pre-Checks) as substep 1.3.
    """
    loan_id = state.get("loan_id")
    if not loan_id:
        return Command(update={"messages": [ToolMessage(
            content=json.dumps({"error": "No loan_id in state. Run find_loan first."}),
            tool_call_id=tool_call_id,
        )]})

    logger.info(f"[REVIEW_PROPERTY_LISTING] Starting for loan {str(loan_id)[:8]}...")

    flags: list = []

    # ── 1. USPS address validation (former 0.5) ──────────────────────────────
    address_validation = _run_usps_validation(state)
    if address_validation.get("valid") is False:
        _flag(
            flags,
            "Address Undeliverable",
            "warning",
            (
                f"USPS could not confirm deliverability for "
                f"{address_validation.get('los_address')!r} "
                f"(DPV={address_validation.get('dpv_confirmation')!r}"
                f"{'; error=' + repr(address_validation.get('error')) if address_validation.get('error') else ''})."
            ),
            "Correct the property address in Encompass against the Purchase Contract / USPS.",
        )
    if address_validation.get("mismatch_with_purchase_contract"):
        _flag(
            flags,
            "Address Mismatch with Purchase Contract",
            "warning",
            (
                f"LOS address street number does not match Purchase Contract "
                f"({address_validation.get('purchase_contract_address')!r} vs "
                f"{address_validation.get('los_address')!r})."
            ),
            "Reconcile the subject property address against the Purchase Contract.",
        )

    # ── 2. Single Zillow/HasData lookup (shared by PUD + new-construction) ───
    zf = _lookup_zillow_facts(state)
    pud = _detect_pud_signals(state, zillow_facts=zf)
    new_construction = _detect_new_construction(state, zillow_facts=zf)
    property_verification = {"pud": pud, "new_construction": new_construction}

    # ── 3. PUD flags ─────────────────────────────────────────────────────────
    link = pud.get("zillow_url") or pud.get("zillow_deep_link")
    if pud.get("strong"):
        _flag(
            flags,
            "Possible PUD — Verify Property / Project Type",
            "warning",
            (
                "Subject property shows strong PUD indicators: "
                + "; ".join(pud.get("pud_signals") or [])
                + ". Property/Project Type was NOT auto-written — confirm before "
                "Transmittal Summary (11.1) runs."
            ),
            (
                "Verify whether the subject sits in a Planned Unit Development"
                + (f" — Zillow: {link}" if link else "")
                + ". If it is a PUD, set Property Type (field 1041) = PUD and "
                "Project Type (field 1012) = 'Other: P/PUD'."
            ),
        )
    elif pud.get("pud_signals") and not pud.get("is_condo"):
        _flag(
            flags,
            "Possible PUD — Weak Indicators (Verify)",
            "info",
            (
                "Subject property shows weak PUD hint(s): "
                + "; ".join(pud.get("pud_signals") or [])
                + ". No strong PUD indicator — Transmittal Summary (11.1) will "
                "still default Project Type (1012) to 'Not in a Project'."
            ),
            (
                "Confirm the subject is not in a PUD via the listing / appraisal"
                + (f" — Zillow: {link}" if link else "")
                + "."
            ),
        )

    # ── 4. New-construction flag ─────────────────────────────────────────────
    construction_status = (_los(state, "construction_status") or "").strip()
    _existing_vals = {"", "existingconstruction", "existing construction", "existing"}
    if (
        new_construction.get("is_new_construction")
        and construction_status.lower() in _existing_vals
    ):
        year = new_construction.get("year_built")
        _flag(
            flags,
            "Possible New Construction — Verify",
            "info",
            (
                f"Zillow indicates new construction"
                + (f" (year_built={year})" if year else "")
                + (
                    f" / isNewConstruction={new_construction.get('zillow_flag')}"
                    if new_construction.get("zillow_flag") is True else ""
                )
                + f". Current Construction Status (field 1067) = "
                f"{construction_status or 'blank'!r}."
                + (
                    f" Listing: {new_construction.get('zillow_url')}"
                    if new_construction.get("zillow_url") else ""
                )
            ),
            (
                "Confirm Construction Status (field 1067) — HUD Transmittal 12.2 "
                "will attempt to write 'New' when blank if this signal is present."
            ),
        )

    # ── 5. Always-on listing facts flag when Zillow found a match ─────────────
    if pud.get("found"):
        facts_bits = []
        if pud.get("home_type"):
            facts_bits.append(f"home_type={pud['home_type']!r}")
        if pud.get("structure_type"):
            facts_bits.append(f"structure_type={pud['structure_type']!r}")
        if pud.get("hoa_fee"):
            facts_bits.append(f"hoa={pud['hoa_fee']!r}")
        if pud.get("zillow_subdivision"):
            facts_bits.append(f"subdivision={pud['zillow_subdivision']!r}")
        if new_construction.get("year_built") is not None:
            facts_bits.append(f"year_built={new_construction['year_built']}")
        _flag(
            flags,
            "Property Listing Facts",
            "info",
            (
                "Zillow/HasData returned a match"
                + (": " + ", ".join(facts_bits) if facts_bits else ".")
                + (f" Listing: {link}" if link else "")
            ),
            "No action needed — facts stored in state for downstream tools.",
            resolved=True,
        )
    elif pud.get("error"):
        logger.info(
            f"[REVIEW_PROPERTY_LISTING] Zillow lookup unavailable: {pud.get('error')}"
        )

    result = {
        "success": True,
        "substep": SUBSTEP,
        "tool": "review_property_listing",
        "address_valid": address_validation.get("valid"),
        "pud_strong": pud.get("strong"),
        "pud_signals": pud.get("pud_signals") or [],
        "is_new_construction": new_construction.get("is_new_construction"),
        "year_built": new_construction.get("year_built"),
        "zillow_found": pud.get("found"),
        "flags_count": len(flags),
        "message": (
            "Property Verification (Address + Listing / PUD) completed"
            + (f" with {len(flags)} flag(s)" if flags else "")
        ),
    }

    logger.info(f"[REVIEW_PROPERTY_LISTING] {result['message']}")

    update = {
        "address_validation": address_validation,
        "property_verification": property_verification,
        "messages": [ToolMessage(content=json.dumps(result), tool_call_id=tool_call_id)],
    }
    if flags:
        update["flags"] = flags

    return Command(update=update)
