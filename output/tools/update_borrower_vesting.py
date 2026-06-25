"""update_borrower_vesting — Tool for substep 9.1: Update Borrower Vesting

Step 9 (STEP_09): Borrower Info - Vesting
Phase: FORM_UPDATES

Ported from LG-docsOrch verify_vesting.py + write_borrower_vesting_info.py.

Vesting strategy:
  - Manner Held (field 33): computed from property state, marital status,
    co-borrower presence, NBS, and borrower sex. Written ONLY when empty.
    Exception: NV forces "As Joint Tenants" for married couples on title.
  - Final Vesting (field 1867): READ-ONLY. The Build Final Vesting button
    in Encompass generates the full survivorship language. We never overwrite
    it except to append the required unmarried/single suffix when missing
    for a single borrower with no co-borrower.
  - Borrower vesting name/type (1868/1871, 1873/1876): written from
    borrower/co-borrower names when empty.
  - Occupancy Intent (Borr/CoBorr.OccupancyIntent): always set from
    occupancy type + loan purpose.

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

# ── State-specific vesting rules ──────────────────────────────────────────────
# NOTE: Manner-held computation (state rules, URLA.X138 mapping) lives in the
# 1003 URLA Lender step (output/tools/update_urla_lender.py). This tool only
# reads field 33; it does not compute or write manner held / estate.

_VALID_UNMARRIED_SUFFIXES = [
    "A SINGLE MAN", "AN UNMARRIED MAN",
    "A SINGLE WOMAN", "AN UNMARRIED WOMAN",
    "A SINGLE PERSON", "AN UNMARRIED PERSON",
]

_VESTING_PLACEHOLDER_PATTERNS = [
    "need to confirm", "confirm w/", "confirm with",
    "tbd", "to be determined", "pending", "ask lo", "check w/",
]


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


def _compute_vesting_desc(marital_status: str, has_coborrower: bool,
                           has_nbs: bool, applicant_is_female: bool,
                           borrower_is_female: bool | None = None) -> str:
    """Compute the vesting description dropdown value (field 1872 / 1877).

    This is the per-entity vesting string that appears in the Borrower Vesting
    dialog. Build Final Vesting combines it with the borrower name (1868) and
    Manner Held (33) to produce the Final Vesting (1867).

    ``applicant_is_female`` is the gender of the person this description is for
    (borrower → field 1872, co-borrower → field 1877) and drives the
    unmarried/single/married-solo wording. ``borrower_is_female`` keys the
    married-couple joint phrase so both slots stay in agreement (preserves the
    prior behaviour where both 1872 and 1877 used the primary borrower's order).

    For UNMARRIED co-owners (e.g. unmarried partners, siblings) each person gets
    their own status ("AN UNMARRIED WOMAN" / "AN UNMARRIED MAN") rather than a
    shared "JOINT TENANTS" string — the joint relationship is captured in Manner
    Held (field 33 = "Tenancy in Common"). See notes.txt:624.

    Examples: "A SINGLE WOMAN", "AN UNMARRIED MAN", "HUSBAND AND WIFE"
    """
    marital = (marital_status or "").strip().upper()
    is_married = marital == "MARRIED"

    if has_coborrower and is_married:
        couple_is_female = borrower_is_female if borrower_is_female is not None else applicant_is_female
        return "WIFE AND HUSBAND" if couple_is_female else "HUSBAND AND WIFE"

    gender_word = "WOMAN" if applicant_is_female else "MAN"
    if marital in ("UNMARRIED", "NOT MARRIED"):
        return f"AN UNMARRIED {gender_word}"
    if marital in ("SINGLE",):
        return f"A SINGLE {gender_word}"
    if is_married:
        return f"A MARRIED {gender_word}"
    return f"A SINGLE {gender_word}"  # safe fallback


def _compute_occupancy_intent(occupancy_status: str, loan_purpose: str) -> str:
    occ = (occupancy_status or "").upper()
    purp = (loan_purpose or "").upper()
    if "INVESTMENT" in occ or "SECOND" in occ:
        return "Will Not Occupy"
    if "REFI" in purp:
        return "Currently Occupy"
    return "Will Occupy"


# ── Main tool ─────────────────────────────────────────────────────────────────

@tool
def update_borrower_vesting(
    tool_call_id: Annotated[str, InjectedToolCallId],
    state: Annotated[dict, InjectedState],
) -> Command:
    """Populate the Borrower Information - Vesting screen.

    Writes occupancy intent, borrower/co-borrower vesting name and type,
    and manner held (field 33) when empty. Reads final vesting (field 1867)
    and flags if missing or malformed for single/unmarried borrowers.

    Vesting descriptions (field 1872 borrower / 1877 co-borrower) are read-only
    in the fieldWriter API, so they are written via a separate loan-entity PATCH
    on applications[].{borrower|coBorrower}.powerOfAttorneyTitleDescription
    rather than the field_updates batch (which would 400 and poison the batch).

    Ported from LG-docsOrch verify_vesting + write_borrower_vesting_info.

    Call this tool during STEP_09 (Borrower Info - Vesting) as substep 9.1.
    """
    loan_id = state.get("loan_id")
    if not loan_id:
        return Command(update={"messages": [ToolMessage(
            content=json.dumps({"error": "No loan_id in state. Run find_loan first."}),
            tool_call_id=tool_call_id,
        )]})

    logger.info(f"[UPDATE_BORROWER_VESTING] Starting for loan {str(loan_id)[:8]}...")

    flags = []
    field_updates = {}
    # Vesting descriptions (1872 borrower / 1877 co-borrower) are READ-ONLY via the
    # fieldWriter API and must NOT go in field_updates (they'd 400 and poison the
    # whole batch). They are written separately via a loan-entity PATCH below.
    vesting_desc_patches: dict[str, str] = {}
    actions = []

    # ── Read fields ───────────────────────────────────────────────────────────
    property_state    = _los(state, "property_state")
    loan_purpose      = (_los(state, "loan_purpose") or "").strip().upper()
    occupancy_status  = (_los(state, "occupancy") or "").strip().upper()

    borr_first  = _los(state, "borrower_first_name")
    borr_middle = _los(state, "borrower_middle_name")
    borr_last   = _los(state, "borrower_last_name")
    borr_ssn    = _los(state, "borrower_ssn")
    borr_dob    = _los(state, "borrower_dob")
    borr_sex    = _los(state, "borrower_sex")
    # Marital status: prefer the Borrower Summary value (field 52) per processor
    # guidance (notes.txt:623); fall back to the Vesting-form copy (field 479).
    summary_marital = (_los(state, "borrower_marital_status") or "").strip()  # field 52
    vesting_marital = (_los(state, "marital_status") or "").strip()           # field 479
    marital_status = summary_marital or vesting_marital

    cobr_first  = _los(state, "coborrower_first_name")
    cobr_middle = _los(state, "coborrower_middle_name")
    cobr_last   = _los(state, "coborrower_last_name")
    cobr_ssn    = _los(state, "coborrower_ssn")
    cobr_dob    = _los(state, "coborrower_dob")
    cobr_sex    = _los(state, "coborrower_sex")

    nbs_flag = _los(state, "nbs_flag")
    nbs_info = _los(state, "nbs_info")

    current_manner  = (_los(state, "manner_of_title") or "").strip()  # field 33 (owned by 1003 URLA Lender step)
    current_vesting = (_los(state, "final_vesting") or "").strip()    # field 1867

    prev_borr_occ   = (_los(state, "borrower_occupancy_intent") or "").strip()
    prev_cobr_occ   = (_los(state, "coborrower_occupancy_intent") or "").strip()
    prev_borr_name  = (_los(state, "borrower_vesting_name") or "").strip()
    prev_cobr_name  = (_los(state, "coborrower_vesting_name") or "").strip()
    prev_borr_vdesc = (_los(state, "borrower_vesting_desc") or "").strip()    # field 1872
    prev_cobr_vdesc = (_los(state, "coborrower_vesting_desc") or "").strip()  # field 1877

    # ── Derived flags ─────────────────────────────────────────────────────────
    has_coborrower = bool(cobr_first and cobr_last)
    has_nbs = not has_coborrower and (nbs_flag or "").strip().upper() == "YES" and bool(nbs_info)
    prop_st = (property_state or "").strip().upper()
    is_refinance = "REFI" in loan_purpose
    is_female = (borr_sex or "").strip().upper() == "FEMALE"

    # Wife-first vesting order: if co-borrower is female and borrower is male,
    # list the wife first (slot 1868) and husband second (slot 1873).
    cobr_is_female = (cobr_sex or "").strip().upper() == "FEMALE"
    borr_is_male   = (borr_sex or "").strip().upper() == "MALE"
    wife_first = has_coborrower and cobr_is_female and borr_is_male

    logger.info(
        f"[UPDATE_BORROWER_VESTING] state={prop_st}, marital={marital_status} "
        f"(summary52={summary_marital or '-'}, vesting479={vesting_marital or '-'}), "
        f"has_coborrower={has_coborrower}, has_nbs={has_nbs}, purpose={loan_purpose}"
    )

    if summary_marital and vesting_marital and summary_marital.upper() != vesting_marital.upper():
        flags.append(_flag("9.1",
            "Marital Status Source Divergence",
            "info",
            (
                f"Borrower Summary marital status (field 52) = '{summary_marital}' but the "
                f"Vesting-form copy (field 479) = '{vesting_marital}'. Using the Summary value "
                f"'{summary_marital}' for vesting computation."
            ),
            "Confirm the borrower's marital status and align fields 52 and 479 in Encompass.",
        ))

    # ── A. Occupancy Intent ───────────────────────────────────────────────────
    occ_intent = _compute_occupancy_intent(occupancy_status, loan_purpose)

    field_updates["Borr.OccupancyIntent"] = occ_intent
    if prev_borr_occ != occ_intent:
        actions.append(
            f"SET Borr.OccupancyIntent = '{occ_intent}' "
            f"(was '{prev_borr_occ or '(empty)'}', "
            f"occupancy={occupancy_status or 'Primary'}, purpose={loan_purpose or 'Purchase'})"
        )
        flags.append(_flag("9.1",
            "Occupancy Intent Updated",
            "info-overwrite",
            f"Borrower occupancy intent was '{prev_borr_occ or '(empty)'}'. Set to '{occ_intent}'.",
            f"Set Borr.OccupancyIntent = '{occ_intent}'",
            resolved=True,
        ))
    else:
        actions.append(f"Borr.OccupancyIntent already '{occ_intent}' — no change")

    if has_coborrower:
        field_updates["CoBorr.OccupancyIntent"] = occ_intent
        if prev_cobr_occ != occ_intent:
            actions.append(f"SET CoBorr.OccupancyIntent = '{occ_intent}' (was '{prev_cobr_occ or '(empty)'}')")
            flags.append(_flag("9.1",
                "Co-Borrower Occupancy Intent Updated",
                "info-overwrite",
                f"Co-borrower occupancy intent was '{prev_cobr_occ or '(empty)'}'. Set to '{occ_intent}'.",
                f"Set CoBorr.OccupancyIntent = '{occ_intent}'",
                resolved=True,
            ))
        else:
            actions.append(f"CoBorr.OccupancyIntent already '{occ_intent}' — no change")

    # ── B. Borrower vesting name / type ───────────────────────────────────────
    borr_full = " ".join(p for p in [borr_first, borr_middle, borr_last] if p and str(p).strip()).strip()
    cobr_full_for_order = " ".join(p for p in [cobr_first, cobr_middle, cobr_last] if p and str(p).strip()).strip() if has_coborrower else ""

    # wife_first: co-borrower (wife) takes slot 1868, borrower (husband) takes 1873
    slot_1868_name = cobr_full_for_order if wife_first else borr_full
    slot_1868_label = "Co-Borrower (wife, listed first)" if wife_first else "Borrower"

    if slot_1868_name:
        if not prev_borr_name:
            field_updates["1868"] = slot_1868_name
            actions.append(f"SET 1868 (Vesting Name 1 — {slot_1868_label}) = '{slot_1868_name}'")
        elif prev_borr_name.upper() != slot_1868_name.upper():
            flags.append(_flag("9.1",
                "Vesting Name 1 (1868) Mismatch",
                "warning",
                f"Field 1868 has '{prev_borr_name}' but expected '{slot_1868_name}' ({slot_1868_label}).",
                f"Correct field 1868 to '{slot_1868_name}' — {'wife goes first per URLA order' if wife_first else 'borrower name from 4000/4001/4002'}",
            ))
    if wife_first:
        flags.append(_flag("9.1",
            "Vesting Order — Wife Listed First",
            "info",
            f"Co-borrower ({cobr_full_for_order}) is female and borrower ({borr_full}) is male. "
            "Wife placed in vesting slot 1868 (first), husband in slot 1873 (second) per URLA order convention.",
            "No action needed — order matches URLA convention.",
            resolved=True,
        ))

    field_updates["1871"] = "Individual"
    actions.append("SET 1871 (Borrower Vesting Type) = 'Individual'")

    # field 1872 — Borrower Vesting Description (the dropdown Build Final Vesting uses)
    computed_vdesc = _compute_vesting_desc(
        marital_status, has_coborrower, has_nbs, is_female, borrower_is_female=is_female,
    )
    if not prev_borr_vdesc:
        # Deferred to the loan-entity PATCH in section H2 (read-only in fieldWriter).
        # The authoritative success/failure flag is emitted there.
        vesting_desc_patches["borrower"] = computed_vdesc
        actions.append(f"SET 1872 (Borrower Vesting Desc) = '{computed_vdesc}' (was empty)")
    elif prev_borr_vdesc.upper() != computed_vdesc.upper():
        flags.append(_flag("9.1",
            "Borrower Vesting Description Mismatch (1872)",
            "warning",
            f"Field 1872 is '{prev_borr_vdesc}' but expected '{computed_vdesc}' (marital={marital_status}, co-borrower={has_coborrower}).",
            f"Update field 1872 to '{computed_vdesc}' or confirm value with team lead",
        ))
    else:
        actions.append(f"1872 already '{prev_borr_vdesc}' — no change")

    if borr_ssn:
        field_updates["65"] = borr_ssn
    if borr_dob:
        field_updates["1402"] = borr_dob

    # ── C. Co-borrower vesting name / type ────────────────────────────────────
    if has_coborrower:
        cobr_full = cobr_full_for_order  # already computed above

        # wife_first: husband (borrower) goes in slot 1873
        slot_1873_name  = borr_full if wife_first else cobr_full
        slot_1873_label = "Borrower (husband, listed second)" if wife_first else "Co-Borrower"

        if slot_1873_name:
            if not prev_cobr_name:
                field_updates["1873"] = slot_1873_name
                actions.append(f"SET 1873 (Vesting Name 2 — {slot_1873_label}) = '{slot_1873_name}'")
            elif prev_cobr_name.upper() != slot_1873_name.upper():
                flags.append(_flag("9.1",
                    "Vesting Name 2 (1873) Mismatch",
                    "warning",
                    f"Field 1873 has '{prev_cobr_name}' but expected '{slot_1873_name}' ({slot_1873_label}).",
                    f"Correct field 1873 to '{slot_1873_name}' — {'husband listed second per wife-first order' if wife_first else 'co-borrower name from 4004/4005/4006'}",
                ))
        field_updates["1876"] = "Individual"
        actions.append("SET 1876 (Co-Borrower Vesting Type) = 'Individual'")

        # field 1877 — Co-Borrower Vesting Description.
        # Use the CO-BORROWER's gender (fixes the prior bug where the borrower's
        # gender was used) so unmarried co-owners get their own status string;
        # the married-couple joint phrase stays keyed to the primary borrower.
        cobr_vdesc = _compute_vesting_desc(
            marital_status, has_coborrower, has_nbs, cobr_is_female, borrower_is_female=is_female,
        )
        if not prev_cobr_vdesc:
            vesting_desc_patches["coborrower"] = cobr_vdesc  # written via PATCH (read-only in fieldWriter)
            actions.append(f"SET 1877 (Co-Borrower Vesting Desc) = '{cobr_vdesc}' (was empty)")
        elif prev_cobr_vdesc.upper() != cobr_vdesc.upper():
            flags.append(_flag("9.1",
                "Co-Borrower Vesting Description Mismatch (1877)",
                "warning",
                f"Field 1877 is '{prev_cobr_vdesc}' but expected '{cobr_vdesc}'.",
                f"Update field 1877 to '{cobr_vdesc}'",
            ))

        if cobr_ssn:
            field_updates["97"] = cobr_ssn
        if cobr_dob:
            field_updates["1403"] = cobr_dob

    # ── D. Manner Held (field 33) — read-only ────────────────────────────────
    # Manner Held + Estate Will Be Held In are owned by the 1003 URLA Lender step
    # (STEP_03, substep 3.1, update_urla_lender), which computes and writes
    # field 33, URLA.X138, and 1066. Here we only READ field 33 for context and
    # to confirm Build Final Vesting has its required input.
    if current_manner:
        flags.append(_flag("9.1",
            "Manner Held (from 1003 URLA Lender)",
            "info",
            f"Manner Held (field 33) = '{current_manner}' (set in the 1003 URLA Lender step).",
            "No action needed — Manner Held is owned by the 1003 URLA Lender step (3.1).",
            resolved=True, docs=["Title Report"],
        ))
    else:
        flags.append(_flag("9.1",
            "Manner Held Empty",
            "warning",
            "Manner Held (field 33) is empty — it should have been set in the 1003 URLA Lender step (3.1). Build Final Vesting needs it.",
            "Run/verify the 1003 URLA Lender step (3.1) to set Manner Held before Build Final Vesting.",
            docs=["Title Report"],
        ))

    # ── F. Final Vesting (field 1867) — read-only ────────────────────────────
    if not current_vesting:
        flags.append(_flag("9.1",
            "Final Vesting Empty",
            "warning",
            "Final Vesting (field 1867) is empty. Click 'Build Final Vesting' in Encompass after setting Manner Held and borrower info.",
            "Click 'Build Final Vesting' in Encompass to populate field 1867",
            docs=["Title Report"],
        ))
    else:
        # Check for placeholder text
        if any(p in current_vesting.lower() for p in _VESTING_PLACEHOLDER_PATTERNS):
            flags.append(_flag("9.1",
                "Final Vesting Contains Placeholder",
                "info",
                f"Final Vesting (1867) appears to contain placeholder text: '{current_vesting}'.",
                "Confirm correct vesting language with LO and update field 1867",
                docs=["Title Report"],
            ))

        # Borrower name sanity check
        vest_up = current_vesting.upper()
        borr_in_vest = (
            (borr_first or "").upper() in vest_up
            and (borr_last or "").upper() in vest_up
        ) if borr_first and borr_last else True

        if not borr_in_vest:
            flags.append(_flag("9.1",
                "Final Vesting Name Mismatch",
                "warning",
                f"Final Vesting (1867): '{current_vesting}' does not contain borrower name '{borr_full}'.",
                "Verify borrower name in Final Vesting matches the 1003",
                docs=["Title Report"],
            ))
        else:
            flags.append(_flag("9.1",
                "Final Vesting Confirmed",
                "info",
                f"Final Vesting (1867): '{current_vesting}' — contains borrower name, read-only.",
                "No action needed",
                resolved=True, docs=["Title Report"],
            ))

        # Single/Unmarried suffix check (skip for refinance non-TX)
        marital_norm = (marital_status or "").strip().upper()
        skip_suffix_check = is_refinance and prop_st != "TX"
        is_single_borrower = marital_norm in ("UNMARRIED", "SINGLE", "NOT MARRIED") and not has_coborrower

        if is_single_borrower and not skip_suffix_check:
            has_valid_suffix = any(s in vest_up for s in _VALID_UNMARRIED_SUFFIXES)
            if not has_valid_suffix:
                gender_word = "WOMAN" if is_female else "MAN"
                correct_vesting = f"{borr_full.upper()}, AN UNMARRIED {gender_word}"
                field_updates["1867"] = correct_vesting
                actions.append(f"SET 1867 (Final Vesting) = '{correct_vesting}' (missing unmarried suffix)")
                flags.append(_flag("9.1",
                    "Vesting Suffix Auto-Corrected",
                    "info-overwrite",
                    (
                        f"Final Vesting was '{current_vesting}' but borrower is {marital_status}. "
                        f"Auto-set to '{correct_vesting}'."
                    ),
                    f"Set Final Vesting (1867) = '{correct_vesting}'",
                    resolved=True, docs=["Title Report"],
                ))
            else:
                flags.append(_flag("9.1",
                    "Vesting Suffix Correct",
                    "info",
                    f"Final Vesting format is correct for {marital_status} borrower (contains unmarried/single suffix).",
                    "No action needed",
                    resolved=True, docs=["Title Report"],
                ))
        elif is_refinance and not prop_st == "TX":
            flags.append(_flag("9.1",
                "Vesting As-Seen (Refinance)",
                "info",
                "Refinance loan — vesting taken 'as seen' from title report. Marital suffix check skipped (only TX mandates this).",
                "No action needed",
                resolved=True, docs=["Title Report"],
            ))

    # ── G. NBS reminder ───────────────────────────────────────────────────────
    if has_nbs:
        nbs_name = (nbs_info or "").strip()
        if nbs_name:
            flags.append(_flag("9.1",
                "NBS Detected — Set Title Only in Encompass",
                "info",
                f"Non-Borrowing Spouse '{nbs_name}' detected (CX.NBSFLAG=YES). Vesting type for NBS must be 'Title only' (TR0104). Set manually in the Vesting Entities screen.",
                "In Encompass, set NBS vesting type (TR0104) = 'Title only'; verify NBS name (TR0101) = '" + nbs_name + "'",
                docs=["Title Report"],
            ))
        else:
            flags.append(_flag("9.1",
                "NBS Flag Set But Name Missing",
                "warning",
                "CX.NBSFLAG is YES but CX.NBSINFO is empty — cannot identify NBS name.",
                "Enter NBS name in CX.NBSINFO in Encompass",
                docs=["Title Report"],
            ))

    # ── H. Write fields ───────────────────────────────────────────────────────
    _FIELD_LABELS = {
        "33": "Manner Held",
        "1066": "Estate Will Be Held In",
        "65": "Borrower SSN",
        "97": "Co-Borrower SSN",
        "1402": "Borrower DOB",
        "1403": "Co-Borrower DOB",
        "1867": "Final Vesting",
        "1868": "Borrower Vesting Name",
        "1871": "Borrower Vesting Type",
        "1873": "Co-Borrower Vesting Name",
        "1876": "Co-Borrower Vesting Type",
        "Borr.OccupancyIntent": "Borrower Occupancy Intent",
        "CoBorr.OccupancyIntent": "Co-Borrower Occupancy Intent",
    }
    if field_updates:
        _write_fields(loan_id, field_updates, substep="9.1", flags=flags, state=state, labels=_FIELD_LABELS)
        wrote_count = len(field_updates)
        logger.info(f"[UPDATE_BORROWER_VESTING] Submitted {wrote_count} field updates")

    # ── H2. Vesting descriptions (1872 / 1877) via loan-entity PATCH ─────────────
    # Fields 1872/1877 are read-only in the fieldWriter API ("Cannot update
    # readonly field"), so they are written here via
    # applications[].{borrower|coBorrower}.powerOfAttorneyTitleDescription.
    patched_vdesc: list[str] = []
    if vesting_desc_patches:
        _dry_run = False
        try:
            from output.registry import DEV_MODE
            _dry_run = getattr(DEV_MODE, "dry_run", False)
        except Exception:
            _dry_run = False

        from encompass_client import write_borrower_vesting_description
        for _applicant, _desc in vesting_desc_patches.items():
            _fid = "1877" if _applicant == "coborrower" else "1872"
            if _dry_run:
                actions.append(f"[DRY-RUN] would PATCH {_fid} ({_applicant} vesting desc) = '{_desc}'")
                continue
            _res = write_borrower_vesting_description(loan_id, _desc, applicant_type=_applicant, state=state)
            if _res.get("success"):
                patched_vdesc.append(_fid)
                actions.append(f"PATCHed {_fid} ({_applicant} vesting desc) = '{_desc}' via loan-entity API")
                flags.append(_flag("9.1",
                    f"{'Co-Borrower ' if _applicant == 'coborrower' else 'Borrower '}Vesting Description Set ({_fid})",
                    "info-overwrite",
                    f"Field {_fid} was empty. Set to '{_desc}' via loan-entity PATCH "
                    f"(read-only in fieldWriter API).",
                    f"Verify field {_fid} = '{_desc}' in the Borrower Vesting screen.",
                    resolved=True,
                ))
            else:
                from shared.encompass_io import humanize_write_error
                flags.append(_flag("9.1",
                    f"Vesting Description Write Failed ({_fid})",
                    "warning",
                    f"Could not write {_applicant} vesting description (field {_fid}) = '{_desc}': "
                    f"{humanize_write_error(str(_res.get('error') or ''))}",
                    f"Set field {_fid} = '{_desc}' manually in the Borrower Vesting screen.",
                ))

    # ── Result ────────────────────────────────────────────────────────────────
    result = {
        "success": True,
        "substep": "9.1",
        "tool": "update_borrower_vesting",
        "los_manner_held": current_manner or "",
        "final_vesting": current_vesting or "",
        "occupancy_intent": occ_intent,
        "has_coborrower": has_coborrower,
        "has_nbs": has_nbs,
        "fields_updated": list(field_updates.keys()),
        "vesting_desc_patched": patched_vdesc,
        "flags_count": len(flags),
        "actions": actions,
        "message": (
            f"Borrower Vesting updated — manner='{current_manner or '(from 3.1)'}', "
            f"intent='{occ_intent}', "
            f"vesting={'SET' if current_vesting else 'EMPTY (needs Build Final Vesting)'}, "
            f"{len(field_updates)} field(s) submitted"
            + (f", {len(patched_vdesc)} vesting desc PATCHed" if patched_vdesc else "")
        ),
    }

    logger.info(f"[UPDATE_BORROWER_VESTING] {result['message']}")
    for a in actions:
        logger.info(f"[UPDATE_BORROWER_VESTING]   {a}")

    # Resolve doc-type names on flags (e.g. ["Title Report"]) into DocRepo
    # coordinate refs, dropping any that are not present in the eFolder. Title
    # Report is usually still on order, so most runs end up with no ref attached.
    _enrich_flag_docs(state, flags)

    update = {"messages": [ToolMessage(content=json.dumps(result), tool_call_id=tool_call_id)]}
    if flags:
        update["flags"] = flags
    return Command(update=update)
