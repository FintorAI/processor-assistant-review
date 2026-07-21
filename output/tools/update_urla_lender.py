"""update_urla_lender — Tool for substep 3.1: Update 1003 URLA Lender

Step 3 (STEP_03): 1003 URLA Lender
Phase: DATA_REVIEW

Owns the 1003 URLA Lender "how title will be held" fields:
  - Manner in Which Title Will Be Held (field 33) + URLA.X138 (Lender-form enum):
    computed from property state, marital status, co-borrower/NBS presence, and
    borrower sex. Written when empty. Exception: NV forces "As Joint Tenants"
    for married couples on title. This is the single owner of the manner-held
    value — Borrower Vesting (later step) reads field 33 and never recomputes it.
    Title reconciliation (3 #13): when the Title Report / commitment extracted
    vesting maps to a standard manner-held value that differs from LOS, field 33
    (+ URLA.X138) is overwritten with that normalized standard value; a title
    value that is not a recognized standard is left to the processor (warning).
  - Estate Will Be Held In (field 1066): set to FeeSimple for standard
    residential loans (blank or Leasehold).

Manner-held computation ported from update_borrower_vesting.py (sections D & E).

# FACTORY-LOCK: true
"""

import json
import logging
import re
from datetime import datetime, timezone
from typing import Annotated

from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.prebuilt import InjectedState
from langgraph.types import Command

from ._helpers import _los, _doc, _write_fields, _enrich_flag_docs

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

def _split_title_names(title_names: str) -> list[str]:
    """Split a Title Names (URLA.X136) string into individual name strings.

    Typical formats seen: "John Smith and Jane Smith", "John Smith, Jane Smith",
    "John Smith & Jane Smith".
    """
    if not title_names:
        return []
    parts = re.split(r",|\band\b|&", title_names, flags=re.IGNORECASE)
    return [p.strip() for p in parts if p.strip()]


def _name_on_title_matches(name: str, first: str | None, last: str | None) -> bool:
    """True if a Title Names entry plausibly refers to the given first/last name.

    Matches on whole name tokens (not substrings) so a last name like "SON"
    can't falsely match "JOHNSON" or "ANDERSON" inside a longer surname.
    """
    if not name:
        return False
    tokens = [t for t in re.split(r"[^A-Z0-9]+", name.upper()) if t]
    f = (first or "").strip().upper()
    ln = (last or "").strip().upper()
    if ln and ln in tokens:
        return True
    if f and f in tokens and not ln:
        return True
    return False


def _extra_title_party(title_names: str, borrower_first, borrower_last,
                        cobr_first, cobr_last) -> str | None:
    """Return the first Title Names entry that matches neither the borrower nor
    the co-borrower, or None if every name on title is already accounted for.

    Used to auto-detect a non-borrowing spouse straight from Title Names
    (URLA.X136) rather than relying solely on the manually-set CX.NBSFLAG /
    CX.NBSINFO fields (video 6 feedback).
    """
    for name in _split_title_names(title_names):
        if _name_on_title_matches(name, borrower_first, borrower_last):
            continue
        if _name_on_title_matches(name, cobr_first, cobr_last):
            continue
        return name
    return None


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
        # Unmarried co-owners on title (unmarried partners, siblings, etc.) take
        # title as Tenants in Common per processor guidance (notes.txt:625, and
        # video-2 note line 464: "if siblings, Tenancy in Common").
        # Casing matches the processor's live convention on loan 2605968646
        # (field 33 = "Tenancy in Common"); field 33 stores values verbatim.
        return "Tenancy in Common"

    if is_community and is_married:
        return "As Her Sole And Separate Property" if is_female else "As His Sole And Separate Property"
    if marital in ("UNMARRIED", "SINGLE", "NOT MARRIED", "SEPARATED"):
        return "Unmarried Woman" if is_female else "Unmarried Man"
    if is_married:
        # Married borrower taking title alone (no co-borrower / NBS) in a
        # non-community-property state → Sole Ownership (notes.txt:629:
        # "if married but buying by himself so would be sole ownership").
        return "Sole Ownership"

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


# ── Title-commitment vesting reconciliation (checklist 3 #13) ─────────────────
# Map the tenancy phrase found in a Title Report's extracted vesting string to the
# canonical field-33 display value Encompass expects. Only well-defined manners are
# auto-written; anything else (e.g. bare "community property") is treated as
# unrecognized so we warn instead of overwriting with a non-standard value.
# Order = priority (most specific tenancy first); each canonical value carries the
# normalized trigger phrases we accept from the doc (tolerant of filler words).
_DOC_MANNER_RULES = [
    ("As Joint Tenants", (
        "joint tenants", "joint tenancy with right survivorship",
        "joint tenants with right survivorship", "joint tenancy with rights survivorship",
        "joint tenants with rights survivorship", "joint tenancy",
    )),
    ("Tenancy By The Entirety", (
        "tenants by entirety", "tenancy by entirety", "tenants by entireties",
    )),
    ("Tenancy in Common", (
        "tenants in common", "tenancy in common",
    )),
    ("Life Estate", ("life estate",)),
    ("Sole Ownership", (
        "sole ownership", "sole separate property",
        "his sole separate property", "her sole separate property",
        "single man", "single woman", "unmarried man", "unmarried woman",
    )),
]

# Filler words dropped before matching so "tenancy in the common" == "tenancy in
# common" and "joint tenants with right of survivorship" matches its trigger.
_VESTING_FILLER_RE = re.compile(r"\b(the|of|all|as|an|a|to)\b")


def _normalize_vesting_phrase(s: str) -> str:
    """Lowercase, strip punctuation, and drop filler words for phrase matching."""
    s = (s or "").lower()
    s = re.sub(r"[^a-z ]+", " ", s)
    s = _VESTING_FILLER_RE.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()


def _title_manner_from_vesting(vesting: str) -> str:
    """Return the canonical field-33 manner from a title vesting string, or ""."""
    norm = _normalize_vesting_phrase(vesting)
    if not norm:
        return ""
    for canonical, triggers in _DOC_MANNER_RULES:
        if any(t in norm for t in triggers):
            return canonical
    return ""


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

    Reconciles field 33 against the Title Report / commitment (checklist 3 #13):
    the extracted final vesting is normalized to a standard Encompass manner-held
    value and, when it differs from LOS, field 33 (+ URLA.X138) is overwritten
    with that standard value (info-overwrite, no warning). When the title vesting
    does not map to a recognized standard, LOS is left unchanged and a warning is
    raised. The Title Report takes precedence over the profile computation.
    Applicant surnames are also confirmed against the vesting string (warn only).

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
    # Marital status: prefer the Borrower Summary value (field 52) per processor
    # guidance (notes.txt:623 "look at Borrower Information - Summary marital
    # status"); fall back to the Vesting-form copy (field 479) when 52 is blank.
    summary_marital = (_los(state, "borrower_marital_status") or "").strip()  # field 52
    vesting_marital = (_los(state, "marital_status") or "").strip()           # field 479
    marital_status = summary_marital or vesting_marital
    borr_sex = _los(state, "borrower_sex")

    cobr_first = _los(state, "coborrower_first_name")
    cobr_last = _los(state, "coborrower_last_name")
    nbs_flag = _los(state, "nbs_flag")
    nbs_info = _los(state, "nbs_info")
    title_names = _los(state, "title_names")           # URLA.X136
    borrower_first = _los(state, "borrower_first_name")
    borrower_last = _los(state, "borrower_last_name")

    current_manner = (_los(state, "manner_of_title") or "").strip()   # field 33
    current_urla_x138 = (_los(state, "manner_urla_x138") or "").strip()  # URLA.X138
    current_estate = (_los(state, "estate_held") or "").strip()       # field 1066
    property_type = (_los(state, "property_type") or "").strip()      # field 1041
    attachment_type = (_los(state, "attachment_type") or "").strip()  # CX.ATTACHMENT.TYPE

    # ── Derived flags ─────────────────────────────────────────────────────────
    has_coborrower = bool(cobr_first and cobr_last)
    has_nbs_flag = not has_coborrower and (nbs_flag or "").strip().upper() == "YES" and bool(nbs_info)

    # Title Names (URLA.X136) cross-check — a party listed on title who is neither
    # the borrower nor a co-borrower is treated as a non-borrowing spouse for
    # manner-held purposes even when CX.NBSFLAG/CX.NBSINFO were never set manually
    # (video 6 feedback: "Title Names include borrower's spouse, but spouse is not
    # a coborrower, but we consider that they are Tenancy by the Entirety still").
    extra_title_party = None
    if not has_coborrower:
        extra_title_party = _extra_title_party(title_names, borrower_first, borrower_last, cobr_first, cobr_last)

    has_nbs = has_nbs_flag or bool(extra_title_party)
    prop_st = (property_state or "").strip().upper()

    logger.info(
        f"[UPDATE_URLA_LENDER] state={prop_st}, marital={marital_status} "
        f"(summary52={summary_marital or '-'}, vesting479={vesting_marital or '-'}), "
        f"has_coborrower={has_coborrower}, has_nbs={has_nbs} "
        f"(flag={has_nbs_flag}, title_names_extra={extra_title_party!r})"
    )

    if extra_title_party and not has_nbs_flag:
        flags.append(_flag("3.1",
            "Additional Party on Title Names (Non-Borrowing Spouse)",
            "info",
            (
                f"Title Names (URLA.X136) = '{title_names}' includes '{extra_title_party}', "
                f"who is neither the borrower nor a co-borrower on this loan. Treating this "
                f"as a non-borrowing spouse on title for manner-held purposes (Tenancy By The "
                f"Entirety), even though CX.NBSFLAG/CX.NBSINFO are not set."
            ),
            (
                f"Confirm '{extra_title_party}' is the borrower's non-borrowing spouse, set "
                f"CX.NBSFLAG=YES / CX.NBSINFO='{extra_title_party}', and note they must still "
                f"sign title/vesting documents even though they are not a co-borrower."
            ),
        ))

    if summary_marital and vesting_marital and summary_marital.upper() != vesting_marital.upper():
        flags.append(_flag("3.1",
            "Marital Status Source Divergence",
            "info",
            (
                f"Borrower Summary marital status (field 52) = '{summary_marital}' but the "
                f"Vesting-form copy (field 479) = '{vesting_marital}'. Using the Summary value "
                f"'{summary_marital}' for manner-held computation."
            ),
            "Confirm the borrower's marital status and align fields 52 and 479 in Encompass.",
        ))

    # ── A. Manner in Which Title Will Be Held (field 33 + URLA.X138) ───────────
    computed_manner = _determine_manner_held(
        property_state, marital_status, has_coborrower,
        has_nbs=has_nbs, borrower_sex=borr_sex,
    )
    manner_compatible = _manner_held_compatible(current_manner, computed_manner)
    manner_exact = current_manner.upper() == computed_manner.upper() if current_manner else False

    # ── A0. Title Commitment reconciliation (checklist 3 #13) ──────────────────
    # The Title Report / commitment is authoritative for how title is actually
    # held. Read its extracted final vesting, normalize the tenancy phrase to a
    # standard Encompass manner-held value, and reconcile field 33 against it:
    #   - overwrite when LOS differs AND the title value maps to a known EC
    #     standard — we write the NORMALIZED standard value ("tenancy in the
    #     common" -> "Tenancy in Common"), never the raw doc text, and emit an
    #     info-overwrite (no warning);
    #   - confirm (no write) when they already agree (same URLA.X138 bucket);
    #   - when the title value is NOT a recognized EC standard, leave LOS
    #     unchanged and raise a warning for manual review.
    # Field 33 and URLA.X138 are always written together (same underlying data).
    title_vesting = (_doc(state, "final_vesting") or "").strip()
    title_manner = _title_manner_from_vesting(title_vesting)
    manner_done = False

    if title_manner:
        target_x138 = _manner_to_urla_x138(title_manner)
        if not current_manner:
            manner_done = True
            field_updates["33"] = title_manner
            field_updates["URLA.X138"] = target_x138
            actions.append(
                f"SET 33 = '{title_manner}' / URLA.X138 = '{target_x138}' "
                f"(populated from Title Report vesting '{title_vesting}')"
            )
            flags.append(_flag("3.1",
                "Manner Held Populated from Title Report",
                "info-overwrite",
                (
                    f"Manner Held (field 33) was empty. Title Report vesting "
                    f"'{title_vesting}' normalized to the standard value '{title_manner}' "
                    f"and written to field 33 (URLA.X138='{target_x138}')."
                ),
                f"Verify Manner Held (33) = '{title_manner}' against the title commitment.",
                resolved=True, docs=["Title Report"],
            ))
        elif _manner_to_urla_x138(current_manner) == target_x138:
            manner_done = True
            flags.append(_flag("3.1",
                "Manner Held Confirmed vs Title Report",
                "info",
                (
                    f"Manner Held (field 33) '{current_manner}' agrees with the Title Report "
                    f"vesting '{title_vesting}' (both '{target_x138}'). No change."
                ),
                "No action needed.",
                resolved=True, docs=["Title Report"],
            ))
        else:
            manner_done = True
            _prev = current_manner
            field_updates["33"] = title_manner
            field_updates["URLA.X138"] = target_x138
            actions.append(
                f"SET 33 = '{title_manner}' / URLA.X138 = '{target_x138}' "
                f"(corrected from '{_prev}' to match Title Report vesting '{title_vesting}')"
            )
            flags.append(_flag("3.1",
                "Manner Held Corrected to Title Report",
                "info-overwrite",
                (
                    f"Manner Held (field 33) was '{_prev}' but the Title Report vesting is "
                    f"'{title_vesting}'. Overwrote field 33 with the standard Encompass value "
                    f"'{title_manner}' (URLA.X138='{target_x138}')."
                ),
                f"Verify Manner Held (33) = '{title_manner}' against the title commitment.",
                resolved=True, docs=["Title Report"],
            ))
    elif title_vesting and current_manner:
        # Title has a vesting string but it does not map to a recognized standard
        # manner-held value — never overwrite LOS with a non-standard value.
        manner_done = True
        flags.append(_flag("3.1",
            "Title Vesting Not a Standard Manner-Held Value",
            "warning",
            (
                f"The Title Report vesting is '{title_vesting}', which does not map to a "
                f"standard Encompass manner-held value. Field 33 '{current_manner}' was "
                f"left unchanged."
            ),
            "Review the title commitment and set/confirm Manner Held (33) manually.",
            docs=["Title Report"],
        ))

    # ── A1. Borrower name(s) vs Title Commitment (checklist 3 #13) ─────────────
    # Confirm the applicant surname(s) appear in the title vesting string. Names
    # are never auto-corrected (the 1003 is the source of truth) — we only warn.
    if title_vesting:
        _tv_up = title_vesting.upper()
        _missing = []
        for _fn, _ln, _label in (
            (_los(state, "borrower_first_name"), _los(state, "borrower_last_name"), "borrower"),
            (_los(state, "coborrower_first_name"), _los(state, "coborrower_last_name"), "co-borrower"),
        ):
            _ln_up = (_ln or "").strip().upper()
            if _ln_up and _ln_up not in _tv_up:
                _full = f"{(_fn or '').strip()} {(_ln or '').strip()}".strip()
                _missing.append(f"{_label} '{_full}'")
        if _missing:
            flags.append(_flag("3.1",
                "Name Not Found on Title Commitment",
                "warning",
                (
                    f"These borrower name(s) do not appear in the Title Report vesting "
                    f"'{title_vesting}': {', '.join(_missing)}."
                ),
                "Confirm the borrower name(s) match the title commitment (names are not auto-corrected).",
                docs=["Title Report"],
            ))

    # Backfill URLA.X138 if empty (even when field 33 is not being updated)
    if not current_urla_x138 and "URLA.X138" not in field_updates:
        # Use current_manner if present, otherwise use computed_manner
        manner_for_x138 = current_manner if current_manner else computed_manner
        field_updates["URLA.X138"] = _manner_to_urla_x138(manner_for_x138)
        actions.append(
            f"SET URLA.X138 = '{field_updates['URLA.X138']}' (backfill from manner='{manner_for_x138}')"
        )

    # ── A2. Profile-based manner held (fallback when title did not decide it) ───
    # Runs only when the Title Report did not already set/confirm field 33 above,
    # so the title commitment always takes precedence over the profile computation.
    if manner_done:
        pass
    elif not current_manner:
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
