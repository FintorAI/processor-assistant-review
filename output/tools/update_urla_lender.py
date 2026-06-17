"""update_urla_lender — Tool for substep 3.1: Update 1003 URLA Lender

Step 3 (STEP_03): 1003 URLA Lender
Phase: DATA_REVIEW

Owns the 1003 URLA Lender "how title will be held" fields:
  - Manner in Which Title Will Be Held (field 33) + URLA.X138 (Lender-form enum):
    computed from property state, marital status, co-borrower/NBS presence, and
    borrower sex. Written ONLY when empty. Exception: NV forces "As Joint
    Tenants" for married couples on title. This is the single owner of the
    manner-held value — Borrower Vesting (later step) reads field 33 and never
    recomputes it.
  - Estate Will Be Held In (field 1066): set to FeeSimple for standard
    residential loans (blank or Leasehold).

Manner-held computation ported from update_borrower_vesting.py (sections D & E).

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

from ._helpers import _los, _write_fields, _enrich_flag_docs

logger = logging.getLogger(__name__)

# ── State-specific manner-held rules ──────────────────────────────────────────

_COMMUNITY_PROPERTY_STATES = {"AZ", "CA", "ID", "LA", "NV", "NM", "TX", "WA", "WI"}
_JOINT_TENANTS_STATES = {"NV"}           # married couples → As Joint Tenants (force overwrite)
_FORCE_OVERWRITE_STATES = {"NV"}         # overwrite even when 33 is populated
# Tenancy by the Entirety is the default for married + co-borrower in all states
# EXCEPT community property states (own rules) and NV (As Joint Tenants override).
_NO_TENANCY_ENTIRETY_STATES = _COMMUNITY_PROPERTY_STATES

_SPOUSE_VESTING_VARIANTS = {"HUSBAND AND WIFE", "WIFE AND HUSBAND"}

# Sole-ownership vesting descriptions that are compatible with computed "Sole Ownership".
# Encompasses dropdown for field 33 contains these as distinct entries that all represent
# title held solely by one person (marital status qualifier only, not a different ownership form).
_SOLE_OWNERSHIP_VARIANTS = {
    "SINGLE MAN", "SINGLE WOMAN",
    "UNMARRIED MAN", "UNMARRIED WOMAN",
    "A SINGLE MAN", "A SINGLE WOMAN",
    "AN UNMARRIED MAN", "AN UNMARRIED WOMAN",
    "MARRIED MAN", "MARRIED WOMAN",
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _flag(substep, title, severity, details, suggestion, resolved=False, docs=None):
    f = {
        "substep": substep,
        "title": title,
        "severity": severity,
        "details": details,
        "suggestion": suggestion,
        "resolved": resolved,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if docs:
        f["relevant_documents"] = docs
    return f


def _determine_manner_held(property_state, marital_status, has_coborrower,
                            has_nbs=False, borrower_sex=None) -> str:
    """Compute the base manner held from state rules + borrower profile.

    Returns a simplified category string. The LOS may have a more specific
    dropdown value (e.g. "Husband And Wife as Joint Tenants With Right of
    Survivorship") — _manner_held_compatible handles that case.
    """
    marital = (marital_status or "").strip().upper()
    prop_st = (property_state or "").strip().upper()
    is_female = (borrower_sex or "").strip().upper() == "FEMALE"
    is_community = prop_st in _COMMUNITY_PROPERTY_STATES
    is_married = marital == "MARRIED"
    both_on_title = has_coborrower or has_nbs

    if both_on_title and is_married:
        if prop_st in _JOINT_TENANTS_STATES:
            return "As Joint Tenants"
        if prop_st not in _NO_TENANCY_ENTIRETY_STATES:
            # Default for married couples in all non-community-property states
            return "Tenancy By The Entirety"
        # Community property states — fall through to CP handling below
        return "Wife And Husband" if is_female else "Husband And Wife"

    if both_on_title:
        return "As Joint Tenants"

    if is_community and is_married:
        return "As Her Sole And Separate Property" if is_female else "As His Sole And Separate Property"
    if marital in ("UNMARRIED", "SINGLE", "NOT MARRIED", "SEPARATED"):
        return "Unmarried Woman" if is_female else "Unmarried Man"
    if is_married:
        return "Married Woman" if is_female else "Married Man"

    return "Sole Ownership"


_MANNER_TO_URLA_X138 = {
    # Confirmed via live Encompass API read-back on test instance (2026-06-01).
    # Field 33 display text → URLA.X138 camelCase enum value.
    # Lender form checkboxes only cover these 6 categories; Borrower Vesting
    # dropdown has many more values that collapse into these buckets.
    "sole ownership":                                          "Individual",
    "individual":                                             "Individual",
    "single man":                                             "Individual",
    "single woman":                                           "Individual",
    "unmarried man":                                          "Individual",
    "unmarried woman":                                        "Individual",
    "married man":                                            "Individual",
    "married woman":                                          "Individual",
    "as his sole and separate property":                      "Individual",
    "as her sole and separate property":                      "Individual",
    "joint tenancy with right of survivorship":               "JointTenantsWithRightOfSurvivorship",
    "joint tenancy with rights of survivorship":              "JointTenantsWithRightOfSurvivorship",
    "as joint tenants":                                       "JointTenantsWithRightOfSurvivorship",
    "all as joint tenants":                                   "JointTenantsWithRightOfSurvivorship",
    "joint tenants":                                          "JointTenantsWithRightOfSurvivorship",
    "as joint tenants with right of survivorship":            "JointTenantsWithRightOfSurvivorship",
    "husband and wife as joint tenants":                      "JointTenantsWithRightOfSurvivorship",
    "husband and wife as joint tenants with right of survivorship": "JointTenantsWithRightOfSurvivorship",
    "tenancy by the entirety":                                "TenantsByTheEntirety",
    "tenancy by entirety":                                    "TenantsByTheEntirety",
    "as tenancy by entirety":                                 "TenantsByTheEntirety",
    "tenants by the entirety":                                "TenantsByTheEntirety",
    "husband and wife":                                       "TenantsByTheEntirety",
    "wife and husband":                                       "TenantsByTheEntirety",
    "spouses married to each other":                          "TenantsByTheEntirety",
    "tenancy in common":                                      "TenantsInCommon",
    "tenants in common":                                      "TenantsInCommon",
    "as tenants in common":                                   "TenantsInCommon",
    "all as tenants in common":                               "TenantsInCommon",
    "husband and wife as tenants in common":                  "TenantsInCommon",
    "both unmarried":                                         "TenantsInCommon",
    "each as to an undivided one half interest":              "TenantsInCommon",
    "each as to an undivided one third interest":             "TenantsInCommon",
    "each as to an undivided one fourth interest":            "TenantsInCommon",
    "life estate":                                            "LifeEstate",
    "as community property":                                  "Other",
    "community property":                                     "Other",
    "to be decided in escrow":                                "Other",
    "other":                                                  "Other",
}


def _manner_to_urla_x138(field_33_value: str) -> str:
    """Map a field 33 display value to the URLA.X138 camelCase enum.

    Returns "Other" for any unrecognised value so the lender form always gets
    a valid enum rather than an empty/rejected write.
    """
    return _MANNER_TO_URLA_X138.get(
        (field_33_value or "").strip().lower(), "Other"
    )


def _manner_held_compatible(los_value: str, computed: str) -> bool:
    """True if the LOS manner held is compatible with the computed value.

    LOS may be more specific (e.g. "Husband And Wife as Joint Tenants...").
    We accept if LOS contains our computed base anywhere, or if both contain
    spouse vesting variants ("Husband And Wife" ↔ "Wife And Husband"),
    or if computed is "Sole Ownership" and LOS is a recognised sole-ownership
    vesting description (e.g. "Single Man", "Unmarried Woman").
    """
    if not los_value or not computed:
        return False
    los_up = los_value.strip().upper()
    comp_up = computed.strip().upper()
    if comp_up in los_up:
        return True
    # Spouse vesting variants: "Husband And Wife" ↔ "Wife And Husband" and longer forms
    if any(variant in los_up for variant in _SPOUSE_VESTING_VARIANTS) and \
       any(variant in comp_up for variant in _SPOUSE_VESTING_VARIANTS):
        return True
    # Sole ownership aliases: LOS has a marital-status-qualified form that maps to Sole Ownership
    if comp_up == "SOLE OWNERSHIP" and any(variant == los_up for variant in _SOLE_OWNERSHIP_VARIANTS):
        return True
    return False


# ── Main tool ─────────────────────────────────────────────────────────────────

@tool
def update_urla_lender(
    tool_call_id: Annotated[str, InjectedToolCallId],
    state: Annotated[dict, InjectedState],
) -> Command:
    """Populate the 1003 URLA Lender "how title will be held" fields.

    Computes Manner in Which Title Will Be Held (field 33) + URLA.X138 from
    property state, marital status, co-borrower/NBS presence, and borrower sex,
    writing them when empty (NV forces As Joint Tenants for married couples).
    Auto-sets Estate Will Be Held In (field 1066) to FeeSimple for standard
    residential loans. Surfaces Attachment Type / Property Type for verification.

    Call this tool during STEP_03 (1003 URLA Lender) as substep 3.1.
    """
    loan_id = state.get("loan_id")
    if not loan_id:
        return Command(update={"messages": [ToolMessage(
            content=json.dumps({"error": "No loan_id in state. Run find_loan first."}),
            tool_call_id=tool_call_id,
        )]})

    logger.info(f"[UPDATE_URLA_LENDER] Starting for loan {str(loan_id)[:8]}...")

    flags: list = []
    field_updates: dict = {}
    actions: list = []

    # ── Read fields ───────────────────────────────────────────────────────────
    property_state = _los(state, "property_state")
    marital_status = _los(state, "marital_status")
    borr_sex = _los(state, "borrower_sex")

    cobr_first = _los(state, "coborrower_first_name")
    cobr_last = _los(state, "coborrower_last_name")
    nbs_flag = _los(state, "nbs_flag")
    nbs_info = _los(state, "nbs_info")

    current_manner = (_los(state, "manner_of_title") or "").strip()   # field 33
    current_urla_x138 = (_los(state, "manner_urla_x138") or "").strip()  # URLA.X138
    current_estate = (_los(state, "estate_held") or "").strip()       # field 1066
    property_type = (_los(state, "property_type") or "").strip()      # field 1041
    attachment_type = (_los(state, "attachment_type") or "").strip()  # CX.ATTACHMENT.TYPE

    # ── Derived flags ─────────────────────────────────────────────────────────
    has_coborrower = bool(cobr_first and cobr_last)
    has_nbs = not has_coborrower and (nbs_flag or "").strip().upper() == "YES" and bool(nbs_info)
    prop_st = (property_state or "").strip().upper()

    logger.info(
        f"[UPDATE_URLA_LENDER] state={prop_st}, marital={marital_status}, "
        f"has_coborrower={has_coborrower}, has_nbs={has_nbs}"
    )

    # ── A. Manner in Which Title Will Be Held (field 33 + URLA.X138) ───────────
    computed_manner = _determine_manner_held(
        property_state, marital_status, has_coborrower,
        has_nbs=has_nbs, borrower_sex=borr_sex,
    )
    manner_compatible = _manner_held_compatible(current_manner, computed_manner)
    manner_exact = current_manner.upper() == computed_manner.upper() if current_manner else False

    # Backfill URLA.X138 if empty (even when field 33 is not being updated)
    if not current_urla_x138:
        # Use current_manner if present, otherwise use computed_manner
        manner_for_x138 = current_manner if current_manner else computed_manner
        field_updates["URLA.X138"] = _manner_to_urla_x138(manner_for_x138)
        actions.append(
            f"SET URLA.X138 = '{field_updates['URLA.X138']}' (backfill from manner='{manner_for_x138}')"
        )

    if not current_manner:
        field_updates["33"] = computed_manner
        field_updates["URLA.X138"] = _manner_to_urla_x138(computed_manner)
        actions.append(
            f"SET 33 = '{computed_manner}' / URLA.X138 = '{field_updates['URLA.X138']}' (was empty)"
        )
        flags.append(_flag("3.1",
            "Manner Held Auto-Set",
            "info-overwrite",
            (
                f"Manner Held (field 33) was empty. Set to '{computed_manner}' "
                f"(state={prop_st}, co-borrower={has_coborrower}, NBS={has_nbs})."
            ),
            f"Set Manner Held (33) = '{computed_manner}' — verify full dropdown value includes survivorship language if needed",
            resolved=True, docs=["Title Report"],
        ))
    elif not manner_compatible:
        force = prop_st in _FORCE_OVERWRITE_STATES and computed_manner == "As Joint Tenants"
        if force:
            field_updates["33"] = computed_manner
            field_updates["URLA.X138"] = _manner_to_urla_x138(computed_manner)
            actions.append(
                f"SET 33 = '{computed_manner}' / URLA.X138 = '{field_updates['URLA.X138']}'"
                f" FORCE (was '{current_manner}', {prop_st} rule)"
            )
            flags.append(_flag("3.1",
                f"Manner Held Auto-Corrected ({prop_st})",
                "info-overwrite",
                f"Manner Held: was '{current_manner}', forced to '{computed_manner}' ({prop_st} requires As Joint Tenants for married couples).",
                f"Set Manner Held (33) = '{computed_manner}' ({prop_st} rule)",
                resolved=True, docs=["Title Report"],
            ))
        else:
            flags.append(_flag("3.1",
                "Manner Held Mismatch",
                "warning",
                (
                    f"Manner Held (field 33) is '{current_manner}', but expected '{computed_manner}' "
                    f"(state={prop_st}, marital={marital_status}, "
                    f"co-borrower={has_coborrower}, NBS={has_nbs}). "
                    f"LOS value was NOT overwritten — confirm with team lead."
                ),
                "Verify Manner Held (33) or escalate to team lead",
                docs=["Title Report"],
            ))
    elif manner_compatible and not manner_exact:
        flags.append(_flag("3.1",
            "Manner Held Verified",
            "info",
            f"Manner Held (33): '{current_manner}' is compatible with computed '{computed_manner}' — keeping LOS value (may include survivorship language).",
            "No action needed",
            resolved=True, docs=["Title Report"],
        ))
    else:
        flags.append(_flag("3.1",
            "Manner Held Verified",
            "info",
            f"Manner Held (33): '{current_manner}' matches computed value.",
            "No action needed",
            resolved=True, docs=["Title Report"],
        ))

    # ── B. Estate Will Be Held In (field 1066) ─────────────────────────────────
    # Set to FeeSimple only when blank or Leasehold (don't clobber other valid values).
    _estate_norm = current_estate.lower().replace(" ", "")
    if _estate_norm == "" or _estate_norm == "leasehold":
        field_updates["1066"] = "FeeSimple"
        _was = f"'{current_estate}'" if current_estate else "blank"
        actions.append(f"SET 1066 (Estate Will Be Held In) = 'FeeSimple' (was {_was})")
        flags.append(_flag("3.1",
            "Estate Auto-Set to Fee Simple",
            "info-overwrite",
            f"Estate Will Be Held In (field 1066) was {_was}. Set to 'FeeSimple' (standard for residential loans).",
            "Confirm Fee Simple is appropriate for this property.",
            resolved=True, docs=["Title Report"],
        ))
    elif _estate_norm == "feesimple":
        flags.append(_flag("3.1",
            "Estate Verified — Fee Simple",
            "info",
            "Estate Will Be Held In (field 1066) is 'FeeSimple'.",
            "No action needed.",
            resolved=True, docs=["Title Report"],
        ))
    else:
        flags.append(_flag("3.1",
            "Estate Not Modified",
            "info",
            f"Estate Will Be Held In (field 1066) is '{current_estate}' (not blank or Leasehold) — not modified.",
            "Verify estate type is appropriate for this property.",
            docs=["Title Report"],
        ))

    # ── C. Attachment Type / Property Type vs Listing ──────────────────────────
    # TODO: Validate Attachment Type (CX.ATTACHMENT.TYPE) and Property Type
    # (field 1041) against the property listing (e.g. Google/MLS lookup). Not
    # implemented yet — surfaced here for the processor to verify manually.
    flags.append(_flag("3.1",
        "Verify Attachment / Property Type vs Listing",
        "info",
        (
            f"Property Type (1041) = '{property_type or '(empty)'}', "
            f"Attachment Type (CX.ATTACHMENT.TYPE) = '{attachment_type or '(empty)'}'. "
            "Automated validation against the listing is not yet implemented."
        ),
        "Verify Attachment Type and Property Type match the property listing.",
    ))

    # ── D. Write fields ─────────────────────────────────────────────────────────
    _FIELD_LABELS = {
        "33": "Manner Held",
        "URLA.X138": "Manner Held (Lender Form)",
        "1066": "Estate Will Be Held In",
    }
    if field_updates:
        _write_fields(loan_id, field_updates, substep="3.1", flags=flags, state=state, labels=_FIELD_LABELS)
        logger.info(f"[UPDATE_URLA_LENDER] Submitted {len(field_updates)} field updates")

    # ── Result ────────────────────────────────────────────────────────────────
    result = {
        "success": True,
        "substep": "3.1",
        "tool": "update_urla_lender",
        "computed_manner_held": computed_manner,
        "los_manner_held": current_manner or "",
        "estate_held": "FeeSimple" if "1066" in field_updates else (current_estate or ""),
        "has_coborrower": has_coborrower,
        "has_nbs": has_nbs,
        "fields_updated": list(field_updates.keys()),
        "flags_count": len(flags),
        "actions": actions,
        "message": (
            f"1003 URLA Lender updated — manner='{computed_manner}', "
            f"estate={'FeeSimple' if '1066' in field_updates else (current_estate or 'unchanged')}, "
            f"{len(field_updates)} field(s) submitted"
        ),
    }

    logger.info(f"[UPDATE_URLA_LENDER] {result['message']}")
    for a in actions:
        logger.info(f"[UPDATE_URLA_LENDER]   {a}")

    # Resolve doc-type names on flags (e.g. ["Title Report"]) into DocRepo
    # coordinate refs, dropping any that are not present in the eFolder.
    _enrich_flag_docs(state, flags)

    update = {"messages": [ToolMessage(content=json.dumps(result), tool_call_id=tool_call_id)]}
    if flags:
        update["flags"] = flags
    return Command(update=update)
