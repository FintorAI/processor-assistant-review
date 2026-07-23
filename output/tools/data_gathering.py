"""Step 0: Data Gathering — Extended with dynamic document selection.

Originally auto-generated from field registry, then extended with:
  - Dynamic document type selection based on loan characteristics
  - eFolder GET-only flow (no POST /efolder/direct)
  - efolder_documents state population with DocRepo locations

The factory will NOT overwrite this file if it already exists.
To regenerate from scratch, delete this file first, then run `generate --all`.
"""
# FACTORY-LOCK: true

import json
import logging
import os
import re
from datetime import datetime
from typing import Annotated, Optional

from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.prebuilt import InjectedState
from langgraph.types import Command

logger = logging.getLogger(__name__)

# ── Config directory (output/config/) ──
_CONFIG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config")
_conditions_cache: dict | None = None
_doc_defs_cache: dict | None = None


def _load_conditions_config() -> dict:
    """Load required_docs_conditions.json (cached)."""
    global _conditions_cache
    if _conditions_cache is None:
        path = os.path.join(_CONFIG_DIR, "required_docs_conditions.json")
        with open(path) as f:
            _conditions_cache = json.load(f)
    return _conditions_cache


def _load_doc_definitions() -> dict:
    """Load required_docs.json (cached)."""
    global _doc_defs_cache
    if _doc_defs_cache is None:
        path = os.path.join(_CONFIG_DIR, "required_docs.json")
        with open(path) as f:
            _doc_defs_cache = json.load(f)
    return _doc_defs_cache


def get_doc_field_map() -> dict[str, list[str]]:
    """Build DOC_FIELD_MAP dynamically from required_docs.json.

    Returns dict: { "Credit Report": ["credit_score", "borrower_ssn", ...], ... }
    """
    defs = _load_doc_definitions()
    result: dict[str, list[str]] = {}
    for _key, doc_info in defs.get("documents", {}).items():
        doc_name = doc_info.get("name", "")
        fields = doc_info.get("fields_extracted", [])
        if doc_name and fields:
            result[doc_name] = fields
    return result


def get_required_documents_for_loan(
    loan_type: str,
    loan_purpose: str,
    borrower_count: int,
) -> tuple[list[str], dict[str, str]]:
    """Select required document types based on loan characteristics.

    Returns:
        (document_list, extraction_modes) where extraction_modes maps
        doc names to 'all' when multiple borrowers need full extraction.
    """
    conditions_cfg = _load_conditions_config()
    conditions = conditions_cfg.get("conditions", [])

    lt = (loan_type or "").strip()
    lp = (loan_purpose or "").strip()
    bc = borrower_count or 1

    # Try exact match first
    for entry in conditions:
        cond = entry.get("condition", {})
        if cond.get("fallback"):
            continue
        if (cond.get("loan_type", "").lower() == lt.lower()
                and cond.get("loan_purpose", "").lower() == lp.lower()
                and cond.get("borrower_count", 1) == bc):
            doc_list = entry.get("document_list", [])
            ext_modes = entry.get("extraction_mode", {})
            ext_modes.pop("_comment", None)
            logger.info(
                f"[DOC_SELECT] Matched: {lt}/{lp}/bc={bc} -> {len(doc_list)} docs"
            )
            return doc_list, ext_modes

    # Try match without borrower_count
    for entry in conditions:
        cond = entry.get("condition", {})
        if cond.get("fallback"):
            continue
        if (cond.get("loan_type", "").lower() == lt.lower()
                and cond.get("loan_purpose", "").lower() == lp.lower()):
            doc_list = entry.get("document_list", [])
            ext_modes = entry.get("extraction_mode", {})
            ext_modes.pop("_comment", None)
            logger.info(
                f"[DOC_SELECT] Partial match (type+purpose): {lt}/{lp} -> {len(doc_list)} docs"
            )
            return doc_list, ext_modes

    # Fallback
    for entry in conditions:
        cond = entry.get("condition", {})
        if cond.get("fallback"):
            doc_list = entry.get("document_list", [])
            logger.info(f"[DOC_SELECT] Using fallback -> {len(doc_list)} docs")
            return doc_list, {}

    logger.warning("[DOC_SELECT] No matching condition and no fallback!")
    return [], {}


def _derive_loan_characteristics(state: dict) -> tuple[str, str, int]:
    """Extract loan_type, loan_purpose, borrower_count from state."""
    los = state.get("los_fields", {})
    summary = state.get("loan_summary", {})

    # loan_type — prefer loan_summary (post-build_loan_summary), fall back to los_fields
    loan_type = ""
    if summary and summary.get("derived", {}).get("loan_type"):
        loan_type = summary["derived"]["loan_type"]
    elif los.get("loan_type", {}).get("value"):
        loan_type = los["loan_type"]["value"]

    # loan_purpose
    loan_purpose = ""
    if summary and summary.get("derived", {}).get("loan_purpose"):
        loan_purpose = summary["derived"]["loan_purpose"]
    elif los.get("loan_purpose", {}).get("value"):
        loan_purpose = los["loan_purpose"]["value"]

    # borrower_count — use coborrower_first_name as a presence signal
    borrower_count = 1
    if summary and summary.get("derived", {}).get("has_coborrower"):
        borrower_count = 2
    elif los.get("coborrower_first_name", {}).get("value"):
            borrower_count = 2

    return loan_type, loan_purpose, borrower_count

# ── Field mapping: field_id -> {key, field_name, category} ──
FIELD_MAP = {
    "1041": {"key": "property_type", "field_name": "Property Type", "category": "property"},
    "1040": {"key": "fha_case_number", "field_name": "FHA/VA Agency Case Number", "category": "loan_info"},
    "1039": {"key": "hud_section_of_act", "field_name": "Section of the Act", "category": "loan_info"},
    "1059": {"key": "hud_lender_id_code", "field_name": "Lender ID Code (HUD Addendum)", "category": "loan_info"},
    "1711": {"key": "hud_agency_type", "field_name": "Agency Type (HUD Addendum)", "category": "loan_info"},
    "900": {"key": "hud_22a_own_sold_re", "field_name": "22a - Own/Sold Other Real Estate", "category": "loan_info"},
    "1065": {"key": "hud_occupancy_cert", "field_name": "25(2) Occupancy (HUD Addendum)", "category": "loan_info"},
    "1639": {"key": "hud_value_determination", "field_name": "25 Value Determination", "category": "loan_info"},
    "1399": {"key": "hud_valuation_awareness", "field_name": "25 Valuation Awareness", "category": "loan_info"},
    "1400": {"key": "hud_lead_paint", "field_name": "25(6) HUD Only - Lead Paint", "category": "loan_info"},
    "URLA.X188": {"key": "branch_street_address", "field_name": "Company/Branch Street Address", "category": "loan_info"},
    "1018": {"key": "borrower_caivrs_number", "field_name": "Borrower CAIVRS Number", "category": "loan_info"},
    "1144": {"key": "coborrower_caivrs_number", "field_name": "Co-Borrower CAIVRS Number", "category": "loan_info"},
    "3067": {"key": "caivrs_date_updated", "field_name": "CAIVRS Date Updated", "category": "loan_info"},
    "3068": {"key": "caivrs_updated_by", "field_name": "CAIVRS Updated By", "category": "loan_info"},
    "233": {"key": "hoa_dues_monthly", "field_name": "Proposed Homeowner Assoc. Dues (Monthly)", "category": "property"},
    "52": {"key": "borrower_marital_status", "field_name": "Borrower Marital Status", "category": "borrower_info"},
    "53": {"key": "borrower_dependents_count", "field_name": "Borrower Dependents Count", "category": "borrower_info"},
    "54": {"key": "borrower_dependent_ages", "field_name": "Borrower Dependent Ages", "category": "borrower_info"},
    "84": {"key": "coborrower_marital_status", "field_name": "Co-Borrower Marital Status", "category": "borrower_info"},
    "1068": {"key": "employment_start_date", "field_name": "Employment Start Date (Hire Date)", "category": "employment"},
    "1072": {"key": "base_monthly_income", "field_name": "Base Monthly Income", "category": "income"},
    "1073": {"key": "years_in_profession", "field_name": "Years in Profession", "category": "employment"},
    "11": {"key": "property_address", "field_name": "Property Street Address", "category": "property"},
    "URLA.X73": {"key": "property_address_urla", "field_name": "Property Street Address (URLA)", "category": "property"},
    "1109": {"key": "loan_amount", "field_name": "Loan Amount", "category": "loan_info"},
    "1169": {"key": "employer_name", "field_name": "Employer Name", "category": "employment"},
    "1172": {"key": "loan_type", "field_name": "Mortgage Type", "category": "loan_info"},
    # "1182": invalid field ID in Encompass batch API — removed 2026-05-14
    "12": {"key": "property_city", "field_name": "Property City", "category": "property"},
    # "1286": invalid field ID in Encompass batch API — removed 2026-05-14
    "14": {"key": "property_state", "field_name": "Property State", "category": "property"},
    "1402": {"key": "borrower_dob", "field_name": "Borrower Date of Birth", "category": "borrower_info"},
    "1480": {"key": "coborrower_cell_phone", "field_name": "Co-Borrower Cell Phone", "category": "borrower_info"},
    "1490": {"key": "borrower_cell_phone", "field_name": "Borrower Cell Phone", "category": "borrower_info"},
    # "1491": invalid field ID in Encompass batch API — removed 2026-05-14
    "15": {"key": "property_zip", "field_name": "Property ZIP", "category": "property"},
    "541": {"key": "los_flood_zone", "field_name": "Flood Zone (Flood Information form)", "category": "property"},
    "2363": {"key": "los_flood_cert_number", "field_name": "Flood Certification Number (Cert #)", "category": "property"},
    "1544": {"key": "borrower_ethnicity", "field_name": "Borrower Ethnicity", "category": "borrower_info"},
    "172": {"key": "other_income_type", "field_name": "Other Income Type", "category": "income"},
    "173": {"key": "other_income_amount", "field_name": "Other Income Amount (Monthly)", "category": "income"},
    "1811": {"key": "occupancy", "field_name": "Occupancy", "category": "loan_info"},
    # EMD amount is the URLA Other Assets Cash/Market Value (NOT field 186, which is
    # the Escrow Company's Escrow Case #). The authoritative dollar value is synced
    # from the otherAssets API by review_urla_emd; this flat read is the initial value.
    "URLAROA0103": {"key": "emd_amount", "field_name": "EMD Amount", "category": "assets"},
    # Field 186 = Escrow Company "Escrow Case #" (same data as the contacts API
    # referenceNumber — verified in the Test instance). Written from the settlement
    # statement File # by review_file_contacts.
    "186": {"key": "escrow_case_number", "field_name": "Escrow Case #", "category": "file_contacts"},
    "19": {"key": "loan_purpose", "field_name": "Loan Purpose", "category": "loan_info"},
    # "218": invalid field ID in Encompass batch API — removed 2026-05-14
    "231": {"key": "gift_amount", "field_name": "Gift Amount", "category": "assets"},
    "3": {"key": "note_rate", "field_name": "Note Rate", "category": "loan_info"},
    # Field 33 = Manner in Which Title Will Be Held (Borrower Vesting form).
    # URLA.X138 = same data in the 1003 URLA Lender form — always write both together.
    # Field 1066 = Estate Will Be Held In (dropdown: FeeSimple / Leasehold) — 1003 URLA Lender.
    "33": {"key": "manner_of_title", "field_name": "Manner in Which Title Will Be Held", "category": "title"},
    "URLA.X138": {"key": "manner_of_title_lender", "field_name": "Manner in Which Title Will Be Held (Lender Form)", "category": "title"},
    "1066": {"key": "estate_held", "field_name": "Estate Will Be Held In", "category": "title"},
    # URLA.X136 = Title Names — everyone actually vested on title, which can include a
    # non-borrowing spouse who never appears in the coborrower fields (feedback video 6).
    "URLA.X136": {"key": "title_names", "field_name": "Title Names", "category": "title"},
    "35": {"key": "borrower_current_address", "field_name": "Borrower Current Street Address", "category": "borrower_info"},
    "350": {"key": "total_monthly_payments", "field_name": "Total Monthly Liabilities", "category": "liabilities"},
    "353": {"key": "ltv", "field_name": "LTV", "category": "insurance"},
    "356": {"key": "appraised_value", "field_name": "Appraised / Estimated Value", "category": "collateral"},
    "364": {"key": "loan_number", "field_name": "Loan Number", "category": "loan_info"},
    "4000": {"key": "borrower_first_name", "field_name": "Borrower First Name", "category": "borrower_info"},
    "4001": {"key": "borrower_middle_name", "field_name": "Borrower Middle Name", "category": "borrower_info"},
    "4002": {"key": "borrower_last_name", "field_name": "Borrower Last Name", "category": "borrower_info"},
    "1869": {"key": "borrower_aka", "field_name": "Borrower Alias / AKA (URLA)", "category": "borrower_info"},
    "1874": {"key": "coborrower_aka", "field_name": "Co-Borrower Alias / AKA (URLA)", "category": "borrower_info"},
    "4004": {"key": "coborrower_first_name", "field_name": "Co-Borrower First Name", "category": "borrower_info"},
    "558": {"key": "owned_properties_count", "field_name": "Number of Owned Properties (REO)", "category": "assets"},
    "65": {"key": "borrower_ssn", "field_name": "Borrower SSN", "category": "borrower_info"},
    "66": {"key": "borrower_home_phone", "field_name": "Borrower Home Phone", "category": "borrower_info"},
    "732": {"key": "total_assets", "field_name": "Total Assets", "category": "assets"},
    "733": {"key": "checking_balance", "field_name": "Checking Account Balance", "category": "assets"},
    "734": {"key": "savings_balance", "field_name": "Savings Account Balance", "category": "assets"},
    "762": {"key": "lock_expires", "field_name": "Lock Expiration Date", "category": "lock"},
    "1387": {"key": "mi_cert_number", "field_name": "MI Certificate Number", "category": "insurance"},
    "232": {"key": "mi_monthly_premium", "field_name": "Proposed Monthly Mortgage Insurance", "category": "insurance"},
    "Document.ExpirationDate.MORTGAGE INSURANCE": {"key": "mi_doc_expiration", "field_name": "Mortgage Insurance Document Expiration Date", "category": "insurance"},
    "1014": {"key": "qualifying_rate", "field_name": "Qualifying Rate (Transmittal Summary)", "category": "loan_info"},
    "1012": {"key": "project_type_1012", "field_name": "Project Type (Transmittal Summary dropdown)", "category": "property"},
    # Field 1067 = Construction Status — read/written from the HUD Transmittal tool
    # (organizationally filed under FHA-Specific Forms) but the field is shared with
    # other forms and applies regardless of loan type. Verified live value
    # "ExistingConstruction" on a Conventional loan.
    "1067": {"key": "construction_status", "field_name": "Construction Status", "category": "property"},
    # Field 2996 = "FHA Management" Property Type/Units field — written "1 Unit"
    # for a confirmed single-family/no-HOA property. Verified live value "1 Unit"
    # (exact string) on a Conventional loan — the field is shared across forms and
    # applies regardless of loan type (notes.txt:470, video 2/6 feedback).
    "2996": {"key": "fha_property_type_units", "field_name": "FHA Management — Property Type", "category": "property"},
    "1541": {"key": "property_review_type", "field_name": "Level of Property Review (Exterior/Interior)", "category": "property"},
    "1542": {"key": "appraisal_form_number", "field_name": "Appraisal Form Number (e.g. 1004, 1073, 1025)", "category": "property"},
    "TSUM.PropertyFormType": {"key": "property_form_type", "field_name": "Property Form Type (Transmittal Summary)", "category": "property"},
    "1553": {"key": "transmittal_project_type", "field_name": "Project Type (Transmittal Summary)", "category": "property"},
    "CX.AMI.ELIGIBILITY": {"key": "ami_eligibility", "field_name": "AMI / Affordable Loan Eligibility", "category": "grant_program"},
    "CX.AMI.PERCENTAGE": {"key": "ami_percentage", "field_name": "AMI Percentage", "category": "grant_program"},
    "CX.APPRAISAL.WAIVER": {"key": "appraisal_waiver", "field_name": "Appraisal Waiver", "category": "collateral"},
    "CX.ATTACHMENT.TYPE": {"key": "attachment_type", "field_name": "Attachment Type (Attached/Detached)", "category": "property"},
    "CX.AUS.COLLATERAL.RELIEF": {"key": "aus_collateral_relief", "field_name": "AUS Collateral Relief", "category": "aus"},
    "3050": {"key": "condo_project_id", "field_name": "CPM Project ID", "category": "property"},
    "1298": {"key": "condo_project_name", "field_name": "Condo Project Name (Transmittal Summary)", "category": "property"},
    "CX.CONDO.PROJECT.TYPE": {"key": "condo_project_type", "field_name": "Condo Project Type", "category": "property"},
    "CX.DOC.TYPE": {"key": "doc_type", "field_name": "Doc Type (Wet / E-sign / Hybrid)", "category": "processor_workflow"},
    "CX.FINAL.VESTING": {"key": "final_vesting", "field_name": "Final Vesting", "category": "title"},
    "CX.FNMA.ADDITIONAL.DATA": {"key": "fnma_additional_data", "field_name": "Fannie Mae Additional Data - AMI", "category": "grant_program"},
    "CX.INVESTOR.TYPE": {"key": "investor_type", "field_name": "Investor Type (Conforming / Non-Del)", "category": "processor_workflow"},
    "CX.KM.CL.ADDITIONAL.NOTES": {"key": "cover_letter_additional_notes", "field_name": "Cover Letter - Additional Notes (pre-populated)", "category": "cover_letter"},
    "CX.KM.CL.APPRAISAL": {"key": "cover_letter_appraisal", "field_name": "Cover Letter - Appraisal (pre-populated)", "category": "cover_letter"},
    "CX.KM.CL.TITLE.COMPANY": {"key": "cover_letter_title_company", "field_name": "Cover Letter - Title Company (pre-populated)", "category": "cover_letter"},
    "CX.KM.SUBMISSION.NOTES": {"key": "submission_notes", "field_name": "Submission Notes (Cover Letter)", "category": "cover_letter"},
    "CX.LOAN.LOCKED": {"key": "loan_locked", "field_name": "Loan Locked Status", "category": "lock"},
    "CX.LOCKED.LE.PRESENT": {"key": "locked_le_present", "field_name": "Locked LE Present", "category": "disclosures"},
    "CX.MILESTONE.CURRENT": {"key": "current_milestone", "field_name": "Current Milestone", "category": "loan_info"},
    "CX.PROCESSOR.NAME": {"key": "processor_name", "field_name": "Processor Name", "category": "loan_info"},
    "CX.REALTOR.EMAIL": {"key": "realtor_email", "field_name": "Realtor Email", "category": "file_contacts"},
    "CX.REQUIRED.FIELDS.STATUS": {"key": "required_fields_status", "field_name": "Encompass Required Fields Status", "category": "submission"},
    "CUST50FV": {"key": "signing_date", "field_name": "Signing Date", "category": "closing"},
    # ── Processor Closing — Certifications section (all format X checkboxes; verified 2026-07-23) ──
    "CX.VESTINGVERIFTITLE": {"key": "cert_vesting_verif_title", "field_name": "Vesting Verified - Title", "category": "closing"},
    "CX.VESTINGVERIFBOR": {"key": "cert_vesting_verif_borrower", "field_name": "Vesting Verified - Borrower", "category": "closing"},
    "CX.WIREINSTINFILE": {"key": "cert_wire_instructions_in_file", "field_name": "Escrow wire instructions in file", "category": "closing"},
    "CX.ESCROWEOINFILE": {"key": "cert_escrow_eo_in_file", "field_name": "Escrow E&O insurance in file", "category": "closing"},
    "CX.CPLINFILE": {"key": "cert_cpl_in_file", "field_name": "CPL in file (correct names, loan #, addressed to AWM)", "category": "closing"},
    "CX.HOIEFFECTIVE": {"key": "cert_hoi_effective", "field_name": "HOI effective on/before Note Date (Wet) / Funding Date (Dry)", "category": "closing"},
    "CX.TAXES": {"key": "taxes_dropdown", "field_name": "Taxes (Unimproved/Improved)", "category": "closing"},
    "CX.TITLE.COMPANY.EMAIL": {"key": "title_company_email", "field_name": "Title Company Email", "category": "file_contacts"},
    "CX.TITLE.COMPANY.NAME": {"key": "title_company_name", "field_name": "Title Company Name", "category": "file_contacts"},
    "CX.VESTING.DESCRIPTION": {"key": "vesting_description", "field_name": "Vesting Description", "category": "title"},
    "CX.WIREDATELO": {"key": "wire_requested_date", "field_name": "Wire Requested Date", "category": "closing"},
    "748": {"key": "actual_closing_date", "field_name": "Closing Date (Actual)", "category": "closing"},
    # ── Borrower Contact Info ──
    "1240": {"key": "borrower_email", "field_name": "Borrower Email", "category": "borrower_info"},
    "1179": {"key": "coborrower_email", "field_name": "Co-Borrower Email", "category": "borrower_info"},
    "1715": {"key": "borrower_work_phone", "field_name": "Borrower Business/Work Phone", "category": "borrower_info"},
    "1716": {"key": "coborrower_work_phone", "field_name": "Co-Borrower Business/Work Phone", "category": "borrower_info"},
    "4533": {"key": "borr_p1_work_phone", "field_name": "Borrower Work Phone (1003 URLA Page 1)", "category": "borrower_info"},
    "4534": {"key": "coborr_p1_work_phone", "field_name": "Co-Borrower Work Phone (1003 URLA Page 1)", "category": "borrower_info"},
    "FE0117": {"key": "borr_part2_phone", "field_name": "Borrower Phone (1003 URLA Part 2)", "category": "borrower_info"},
    "FE0217": {"key": "coborr_part2_phone", "field_name": "Co-Borrower Phone (1003 URLA Part 2)", "category": "borrower_info"},
    "98": {"key": "coborrower_home_phone", "field_name": "Co-Borrower Home Phone", "category": "borrower_info"},
    "4920": {"key": "borrower_accept_sms", "field_name": "Borrower Accept Text/SMS", "category": "borrower_info"},
    "4935": {"key": "coborrower_accept_sms", "field_name": "Co-Borrower Accept Text/SMS", "category": "borrower_info"},
    "4003": {"key": "borrower_name_suffix", "field_name": "Borrower Name Suffix", "category": "borrower_info"},
    "97": {"key": "coborrower_ssn", "field_name": "Co-Borrower SSN", "category": "borrower_info"},
    "1403": {"key": "coborrower_dob", "field_name": "Co-Borrower Date of Birth", "category": "borrower_info"},
    "4114": {"key": "borrower_est_closing_date", "field_name": "Borrower Est Closing Date", "category": "borrower_info"},
    # ── Credit ──
    "67": {"key": "experian_score", "field_name": "Borrower Experian/FICO Score", "category": "credit"},
    "60": {"key": "coborrower_experian_score", "field_name": "Co-Borrower Experian/FICO Score", "category": "credit"},
    "1414": {"key": "equifax_score", "field_name": "Borrower Equifax/Beacon Score", "category": "credit"},
    "1415": {"key": "coborrower_equifax_score", "field_name": "Co-Borrower Equifax/Beacon Score", "category": "credit"},
    "1450": {"key": "transunion_score", "field_name": "Borrower TransUnion/Empirica Score", "category": "credit"},
    "1452": {"key": "coborrower_transunion_score", "field_name": "Co-Borrower TransUnion/Empirica Score", "category": "credit"},
    "300": {"key": "credit_reference_number", "field_name": "Credit Reference Number", "category": "credit"},
    "VASUMM.X23": {"key": "credit_score_decision", "field_name": "Credit Score for Decision Making", "category": "credit"},
    # ── Loan Info (Borrower Summary fields) ──
    "1264": {"key": "lender", "field_name": "Lender", "category": "loan_info"},
    "1401": {"key": "loan_program", "field_name": "Loan Program", "category": "loan_info"},
    "140": {"key": "subordinate_financing", "field_name": "Subordinate Financing", "category": "loan_info"},
    "URLA.X230": {"key": "dpa_subordinate_amount", "field_name": "DPA Subordinate Finance Amount", "category": "loan_info"},
    "1785": {"key": "closing_cost_program", "field_name": "Closing Cost Program", "category": "loan_info"},
    "1051": {"key": "mers_min", "field_name": "MERS MIN", "category": "loan_info"},
    "420": {"key": "lien_position", "field_name": "Lien Position", "category": "loan_info"},
    "608": {"key": "amort_type", "field_name": "Amortization Type", "category": "loan_info"},
    "4": {"key": "loan_term_months", "field_name": "Loan Term (Months)", "category": "loan_info"},
    "325": {"key": "term_due_in_months", "field_name": "Term Due In (Months)", "category": "loan_info"},
    "3293": {"key": "undiscounted_rate", "field_name": "Undiscounted Rate", "category": "loan_info"},
    "3941": {"key": "secondary_registration", "field_name": "Secondary Registration", "category": "loan_info"},
    "432": {"key": "lock_days", "field_name": "Lock Period (# of Days)", "category": "loan_info"},
    "761": {"key": "lock_date", "field_name": "Lock Date", "category": "loan_info"},
    "2400": {"key": "rate_is_locked", "field_name": "Rate Is Locked (Y/N)", "category": "loan_info"},
    "3253": {"key": "last_rate_set_date", "field_name": "Last Rate Set Date", "category": "loan_info"},
    "3259": {"key": "rate_lock_disclosure_date", "field_name": "Rate Lock Disclosure Date", "category": "loan_info"},
    # ── Income / Payment ──
    "5": {"key": "monthly_payment", "field_name": "Monthly Payment (P&I)", "category": "income"},
    "912": {"key": "total_monthly_payment", "field_name": "Total Monthly Payment", "category": "income"},
    "736": {"key": "monthly_income", "field_name": "Monthly Income", "category": "income"},
    # ── Assets / Down Payment ──
    "136": {"key": "los_purchase_price", "field_name": "Purchase Price (LOS)", "category": "assets"},
    "1771": {"key": "down_payment_pct", "field_name": "Down Payment %", "category": "assets"},
    "1335": {"key": "down_payment_amount", "field_name": "Down Payment Amount", "category": "assets"},
    # ── Property ──
    "13": {"key": "property_county", "field_name": "Property County", "category": "property"},
    "1821": {"key": "estimated_value", "field_name": "Estimated Value", "category": "property"},
    # ── Closing ──
    "763": {"key": "closing_date", "field_name": "Est Closing Date", "category": "closing"},
    # ── Declarations ──
    "418": {"key": "declaration_primary_residence", "field_name": "Declaration 5a — Will Occupy as Primary Residence", "category": "declarations"},
    "403": {"key": "declaration_ownership_3yr", "field_name": "Declaration 5a(A) — Ownership Interest Past 3 Years", "category": "declarations"},
    "981": {"key": "prior_property_type", "field_name": "Declaration 5a(A)(1) — Type of Prior Property", "category": "declarations"},
    "1069": {"key": "prior_title_held", "field_name": "Declaration 5a(A)(2) — How Title Was Held (Prior Property)", "category": "declarations"},
    "1108": {"key": "coborr_ownership_3yr", "field_name": "Declaration 5a(A) — Co-Borrower Ownership Interest Past 3 Years", "category": "declarations"},
    # Note: field 1491 is invalid in the Encompass batch API — removed 2026-05-19
    # Note: field 218 is invalid in the Encompass batch API — removed 2026-06-02
    # ── URLA Part 4 Section 4c — Rental Income (Purchase only) ──
    "1005": {"key": "rental_income", "field_name": "Expected Monthly Rental Income (4c)", "category": "income"},
    "1487": {"key": "rental_occupancy_rate", "field_name": "Occupancy Rate % (4c)", "category": "income"},
    # ── Step 01 — File Contacts ──
    "638": {"key": "seller_1_name", "field_name": "Seller 1 Name", "category": "file_contacts"},
    # ── Step 02 — Co-Borrower Name ──
    "4006": {"key": "coborrower_last_name", "field_name": "Co-Borrower Last Name", "category": "borrower_info"},
    # ── Step 03 — URLA Page 1 ──
    "16": {"key": "property_units", "field_name": "Subject Property Number of Units", "category": "property"},
    "85": {"key": "coborr_dependents_count", "field_name": "Co-Borrower Number of Dependents", "category": "borrower_info"},
    "86": {"key": "coborr_dependents_ages", "field_name": "Co-Borrower Dependents Ages", "category": "borrower_info"},
    "URLA.X1": {"key": "borrower_citizenship", "field_name": "Borrower Citizenship", "category": "borrower_info"},
    "URLA.X2": {"key": "coborrower_citizenship", "field_name": "Co-Borrower Citizenship", "category": "borrower_info"},
    "URLA.X21": {"key": "borr_language_preference", "field_name": "Borrower Language Preference", "category": "borrower_info"},
    "URLA.X22": {"key": "coborr_language_preference", "field_name": "Co-Borrower Language Preference", "category": "borrower_info"},
    "URLA.X13": {"key": "borr_military_service", "field_name": "Borrower Military Service Indicator", "category": "borrower_info"},
    "URLA.X14": {"key": "coborr_military_service", "field_name": "Co-Borrower Military Service Indicator", "category": "borrower_info"},
    "URLA.X19": {"key": "borr_military_surviving_spouse", "field_name": "Borrower Surviving Spouse", "category": "borrower_info"},
    "URLA.X20": {"key": "coborr_military_surviving_spouse", "field_name": "Co-Borrower Surviving Spouse", "category": "borrower_info"},
    "URLA.X123": {"key": "borr_military_active_duty", "field_name": "Borrower Currently Serving on Active Duty", "category": "borrower_info"},
    "URLA.X124": {"key": "borr_military_retired", "field_name": "Borrower Retired/Discharged/Separated", "category": "borrower_info"},
    "URLA.X125": {"key": "borr_military_reserve", "field_name": "Borrower Non-Activated Reserve/National Guard", "category": "borrower_info"},
    "URLA.X126": {"key": "coborr_military_active_duty", "field_name": "Co-Borrower Currently Serving on Active Duty", "category": "borrower_info"},
    "URLA.X127": {"key": "coborr_military_retired", "field_name": "Co-Borrower Retired/Discharged/Separated", "category": "borrower_info"},
    "URLA.X128": {"key": "coborr_military_reserve", "field_name": "Co-Borrower Non-Activated Reserve/National Guard", "category": "borrower_info"},
    "URLA.X265": {"key": "borr_former_addr_does_not_apply", "field_name": "Borrower Former Address Does Not Apply", "category": "borrower_info"},
    "URLA.X266": {"key": "coborr_former_addr_does_not_apply", "field_name": "Co-Borrower Former Address Does Not Apply", "category": "borrower_info"},
    "FR0126": {"key": "borr_present_addr", "field_name": "Borrower Present Street Address", "category": "borrower_info"},
    "FR0106": {"key": "borr_present_city", "field_name": "Borrower Present City", "category": "borrower_info"},
    "FR0107": {"key": "borr_present_state", "field_name": "Borrower Present State", "category": "borrower_info"},
    "FR0108": {"key": "borr_present_zip", "field_name": "Borrower Present Zip", "category": "borrower_info"},
    "FR0112": {"key": "borr_present_yrs", "field_name": "Borrower Years at Current Address", "category": "borrower_info"},
    "FR0115": {"key": "borr_housing_type", "field_name": "Borrower Current Housing Type", "category": "borrower_info"},
    "FR0116": {"key": "borr_housing_amount", "field_name": "Borrower Current Housing Expense Amount", "category": "borrower_info"},
    "FR0124": {"key": "borr_present_mos", "field_name": "Borrower Months at Current Address", "category": "borrower_info"},
    "FR0212": {"key": "coborr_present_yrs", "field_name": "Co-Borrower Years at Current Address", "category": "borrower_info"},
    "FR0224": {"key": "coborr_present_mos", "field_name": "Co-Borrower Months at Current Address", "category": "borrower_info"},
    "FR0326": {"key": "borr_former_addr", "field_name": "Borrower Former Street Address", "category": "borrower_info"},
    "FR0306": {"key": "borr_former_city", "field_name": "Borrower Former City", "category": "borrower_info"},
    "FR0307": {"key": "borr_former_state", "field_name": "Borrower Former State", "category": "borrower_info"},
    "FR0308": {"key": "borr_former_zip", "field_name": "Borrower Former Zip", "category": "borrower_info"},
    "FR0315": {"key": "borr_former_housing_type", "field_name": "Borrower Former Housing Type", "category": "borrower_info"},
    "FR0316": {"key": "borr_former_housing_amount", "field_name": "Borrower Former Housing Expense Amount", "category": "borrower_info"},
    "FR0415": {"key": "coborr_housing_type", "field_name": "Co-Borrower Current Housing Type", "category": "borrower_info"},
    "FR0416": {"key": "coborr_housing_amount", "field_name": "Co-Borrower Current Housing Expense Amount", "category": "borrower_info"},
    "FR0426": {"key": "coborr_former_addr", "field_name": "Co-Borrower Former Street Address", "category": "borrower_info"},
    "FR0406": {"key": "coborr_former_city", "field_name": "Co-Borrower Former City", "category": "borrower_info"},
    "FR0407": {"key": "coborr_former_state", "field_name": "Co-Borrower Former State", "category": "borrower_info"},
    "FR0408": {"key": "coborr_former_zip", "field_name": "Co-Borrower Former Zip", "category": "borrower_info"},
    "1819": {"key": "borr_mailing_same_as_present", "field_name": "Borrower Mailing Address Same as Present", "category": "borrower_info"},
    "1820": {"key": "coborr_mailing_same_as_present", "field_name": "Co-Borrower Mailing Address Same as Present", "category": "borrower_info"},
    # ── Residence Address Unit Type / Unit # (normalized in review_urla_page1) ──
    "FR0125": {"key": "borr_present_unit_type", "field_name": "Borrower Present Address Unit Type", "category": "borrower_info"},
    "FR0127": {"key": "borr_present_unit_number", "field_name": "Borrower Present Address Unit #", "category": "borrower_info"},
    "FR0225": {"key": "coborr_present_unit_type", "field_name": "Co-Borrower Present Address Unit Type", "category": "borrower_info"},
    "FR0227": {"key": "coborr_present_unit_number", "field_name": "Co-Borrower Present Address Unit #", "category": "borrower_info"},
    "FR0325": {"key": "borr_former_unit_type", "field_name": "Borrower Former Address Unit Type", "category": "borrower_info"},
    "FR0327": {"key": "borr_former_unit_number", "field_name": "Borrower Former Address Unit #", "category": "borrower_info"},
    "FR0425": {"key": "coborr_former_unit_type", "field_name": "Co-Borrower Former Address Unit Type", "category": "borrower_info"},
    "FR0427": {"key": "coborr_former_unit_number", "field_name": "Co-Borrower Former Address Unit #", "category": "borrower_info"},
    # ── Government ID + Type (populated from Driver's License doc content) ──
    "5053": {"key": "borrower_gov_id", "field_name": "Borrower Government ID", "category": "borrower_info"},
    "5055": {"key": "borrower_gov_id_type", "field_name": "Borrower Government ID Type", "category": "borrower_info"},
    "5054": {"key": "coborrower_gov_id", "field_name": "Co-Borrower Government ID", "category": "borrower_info"},
    "5056": {"key": "coborrower_gov_id_type", "field_name": "Co-Borrower Government ID Type", "category": "borrower_info"},
    # ── Step 04 — Employment / Income ──
    # ── Section 1b: Employee/Employer Income ──────────────────────────────────
    "FE0119": {"key": "borr_base_monthly_income", "field_name": "Borrower — Base Monthly Income (Section 1b)", "category": "income"},
    "FE0219": {"key": "coborr_base_monthly_income", "field_name": "Co-Borrower — Base Monthly Income (Section 1b)", "category": "income"},
    "URLA.X199": {"key": "borr_1b_dna", "field_name": "Borrower — Section 1b Does Not Apply", "category": "income"},
    "URLA.X200": {"key": "coborr_1b_dna", "field_name": "Co-Borrower — Section 1b Does Not Apply", "category": "income"},
    # ── Section 1c: Additional/Self-Employment Income ─────────────────────────
    # Borrower FE03xx
    "FE0112": {"key": "borr_1c_total_gross_income", "field_name": "Borrower 1c — Total Gross Income", "category": "income"},
    "FE0156": {"key": "borr_1c_monthly_income", "field_name": "Borrower 1c — Monthly Income (or Loss)", "category": "income"},
    "FE0212": {"key": "coborr_1c_total_gross_income", "field_name": "Co-Borrower 1c — Total Gross Income", "category": "income"},
    "FE0256": {"key": "coborr_1c_monthly_income", "field_name": "Co-Borrower 1c — Monthly Income (or Loss)", "category": "income"},
    "FE0302": {"key": "borr_1c_employer_name", "field_name": "Borrower 1c — Employer or Business Name", "category": "income"},
    "FE0380": {"key": "borr_1c_foreign_address", "field_name": "Borrower 1c — Foreign Address", "category": "income"},
    "FE0360": {"key": "borr_1c_street", "field_name": "Borrower 1c — Street Address", "category": "income"},
    "FE0358": {"key": "borr_1c_unit_type", "field_name": "Borrower 1c — Unit Type", "category": "income"},
    "FE0359": {"key": "borr_1c_unit_number", "field_name": "Borrower 1c — Unit Number", "category": "income"},
    "FE0305": {"key": "borr_1c_city", "field_name": "Borrower 1c — City", "category": "income"},
    "FE0306": {"key": "borr_1c_state", "field_name": "Borrower 1c — State", "category": "income"},
    "FE0307": {"key": "borr_1c_zip", "field_name": "Borrower 1c — Zip", "category": "income"},
    "FE0317": {"key": "borr_1c_phone", "field_name": "Borrower 1c — Phone", "category": "income"},
    "FE0310": {"key": "borr_1c_position_title", "field_name": "Borrower 1c — Position or Title", "category": "income"},
    "FE0351": {"key": "borr_1c_start_date", "field_name": "Borrower 1c — Start Date", "category": "income"},
    "FE0316": {"key": "borr_1c_years_in_line", "field_name": "Borrower 1c — Years in Line of Work", "category": "income"},
    "FE0352": {"key": "borr_1c_months_in_line", "field_name": "Borrower 1c — Months in Line of Work", "category": "income"},
    # Co-Borrower FE04xx (same structure, FE04 prefix)
    "FE0402": {"key": "coborr_1c_employer_name", "field_name": "Co-Borrower 1c — Employer or Business Name", "category": "income"},
    "FE0480": {"key": "coborr_1c_foreign_address", "field_name": "Co-Borrower 1c — Foreign Address", "category": "income"},
    "FE0460": {"key": "coborr_1c_street", "field_name": "Co-Borrower 1c — Street Address", "category": "income"},
    "FE0458": {"key": "coborr_1c_unit_type", "field_name": "Co-Borrower 1c — Unit Type", "category": "income"},
    "FE0459": {"key": "coborr_1c_unit_number", "field_name": "Co-Borrower 1c — Unit Number", "category": "income"},
    "FE0405": {"key": "coborr_1c_city", "field_name": "Co-Borrower 1c — City", "category": "income"},
    "FE0406": {"key": "coborr_1c_state", "field_name": "Co-Borrower 1c — State", "category": "income"},
    "FE0407": {"key": "coborr_1c_zip", "field_name": "Co-Borrower 1c — Zip", "category": "income"},
    "FE0417": {"key": "coborr_1c_phone", "field_name": "Co-Borrower 1c — Phone", "category": "income"},
    "FE0410": {"key": "coborr_1c_position_title", "field_name": "Co-Borrower 1c — Position or Title", "category": "income"},
    "FE0451": {"key": "coborr_1c_start_date", "field_name": "Co-Borrower 1c — Start Date", "category": "income"},
    "FE0416": {"key": "coborr_1c_years_in_line", "field_name": "Co-Borrower 1c — Years in Line of Work", "category": "income"},
    "FE0452": {"key": "coborr_1c_months_in_line", "field_name": "Co-Borrower 1c — Months in Line of Work", "category": "income"},
    # DNA checkboxes for 1c (was mislabeled as "Employment Income" — these are Section 1c)
    "URLA.X201": {"key": "borr_1c_dna", "field_name": "Borrower — Section 1c Does Not Apply", "category": "income"},
    "URLA.X202": {"key": "coborr_1c_dna", "field_name": "Co-Borrower — Section 1c Does Not Apply", "category": "income"},
    # ── Section 1d: Previous Employment and Income ────────────────────────────
    # Borrower FE05xx
    "FE0312": {"key": "borr_1d_total_gross_income", "field_name": "Borrower 1d — Total Gross Income", "category": "income"},
    "FE0356": {"key": "borr_1d_monthly_income", "field_name": "Borrower 1d — Monthly Income (or Loss)", "category": "income"},
    "FE0412": {"key": "coborr_1d_total_gross_income", "field_name": "Co-Borrower 1d — Total Gross Income", "category": "income"},
    "FE0456": {"key": "coborr_1d_monthly_income", "field_name": "Co-Borrower 1d — Monthly Income (or Loss)", "category": "income"},
    "FE0502": {"key": "borr_1d_employer_name", "field_name": "Borrower 1d — Employer or Business Name", "category": "income"},
    "FE0580": {"key": "borr_1d_foreign_address", "field_name": "Borrower 1d — Foreign Address", "category": "income"},
    "FE0560": {"key": "borr_1d_street", "field_name": "Borrower 1d — Street Address", "category": "income"},
    "FE0558": {"key": "borr_1d_unit_type", "field_name": "Borrower 1d — Unit Type", "category": "income"},
    "FE0559": {"key": "borr_1d_unit_number", "field_name": "Borrower 1d — Unit Number", "category": "income"},
    "FE0505": {"key": "borr_1d_city", "field_name": "Borrower 1d — City", "category": "income"},
    "FE0506": {"key": "borr_1d_state", "field_name": "Borrower 1d — State", "category": "income"},
    "FE0507": {"key": "borr_1d_zip", "field_name": "Borrower 1d — Zip", "category": "income"},
    "FE0517": {"key": "borr_1d_phone", "field_name": "Borrower 1d — Phone", "category": "income"},
    "FE0510": {"key": "borr_1d_position_title", "field_name": "Borrower 1d — Position or Title", "category": "income"},
    "FE0551": {"key": "borr_1d_start_date", "field_name": "Borrower 1d — Start Date", "category": "income"},
    "FE0514": {"key": "borr_1d_end_date", "field_name": "Borrower 1d — End Date", "category": "income"},
    # FE0516/FE0552 crash (500) or are invalid (400) on fieldReader — correct IDs are BE03xx:
    "BE0316": {"key": "borr_1d_years_in_line", "field_name": "Borrower 1d — Years in Line of Work", "category": "income"},
    "BE0352": {"key": "borr_1d_months_in_line", "field_name": "Borrower 1d — Months in Line of Work", "category": "income"},
    # Co-Borrower FE06xx (same structure, FE06 prefix)
    "FE0602": {"key": "coborr_1d_employer_name", "field_name": "Co-Borrower 1d — Employer or Business Name", "category": "income"},
    "FE0680": {"key": "coborr_1d_foreign_address", "field_name": "Co-Borrower 1d — Foreign Address", "category": "income"},
    "FE0660": {"key": "coborr_1d_street", "field_name": "Co-Borrower 1d — Street Address", "category": "income"},
    "FE0658": {"key": "coborr_1d_unit_type", "field_name": "Co-Borrower 1d — Unit Type", "category": "income"},
    "FE0659": {"key": "coborr_1d_unit_number", "field_name": "Co-Borrower 1d — Unit Number", "category": "income"},
    "FE0605": {"key": "coborr_1d_city", "field_name": "Co-Borrower 1d — City", "category": "income"},
    "FE0606": {"key": "coborr_1d_state", "field_name": "Co-Borrower 1d — State", "category": "income"},
    "FE0607": {"key": "coborr_1d_zip", "field_name": "Co-Borrower 1d — Zip", "category": "income"},
    "FE0617": {"key": "coborr_1d_phone", "field_name": "Co-Borrower 1d — Phone", "category": "income"},
    "FE0610": {"key": "coborr_1d_position_title", "field_name": "Co-Borrower 1d — Position or Title", "category": "income"},
    "FE0651": {"key": "coborr_1d_start_date", "field_name": "Co-Borrower 1d — Start Date", "category": "income"},
    "FE0614": {"key": "coborr_1d_end_date", "field_name": "Co-Borrower 1d — End Date", "category": "income"},
    # FE0616/FE0652 crash (500) or are invalid (400) on fieldReader — correct IDs are BE04xx:
    "BE0416": {"key": "coborr_1d_years_in_line", "field_name": "Co-Borrower 1d — Years in Line of Work", "category": "income"},
    "BE0452": {"key": "coborr_1d_months_in_line", "field_name": "Co-Borrower 1d — Months in Line of Work", "category": "income"},
    # DNA checkboxes for 1d
    "URLA.X203": {"key": "borr_1d_dna", "field_name": "Borrower — Section 1d Does Not Apply", "category": "income"},
    "URLA.X204": {"key": "coborr_1d_dna", "field_name": "Co-Borrower — Section 1d Does Not Apply", "category": "income"},
    # ── Section 1e: Other Income Sources ─────────────────────────────────────
    "URLA.X40": {"key": "borr_other_income_dna", "field_name": "Borrower — Section 1e Does Not Apply", "category": "income"},
    "URLA.X41": {"key": "coborr_other_income_dna", "field_name": "Co-Borrower — Section 1e Does Not Apply", "category": "income"},
    "BE0102": {"key": "be01_employer_name", "field_name": "Employment 1 — Employer Name", "category": "employment"},
    "BE0105": {"key": "be01_employer_city", "field_name": "Employment 1 — Employer City", "category": "employment"},
    "BE0106": {"key": "be01_employer_state", "field_name": "Employment 1 — Employer State", "category": "employment"},
    "BE0107": {"key": "be01_employer_zip", "field_name": "Employment 1 — Employer Zip", "category": "employment"},
    "BE0108": {"key": "be01_voe_is_for", "field_name": "Employment 1 — VOE Is For (Borrower / Co-Borrower)", "category": "employment"},
    "BE0109": {"key": "be01_employment_type", "field_name": "Employment 1 — Type (Current / Prior)", "category": "employment"},
    "BE0110": {"key": "be01_position_title", "field_name": "Employment 1 — Position / Title", "category": "employment"},
    "BE0113": {"key": "be01_years_in_job", "field_name": "Employment 1 — Years in This Job", "category": "employment"},
    "BE0114": {"key": "be01_date_terminated", "field_name": "Employment 1 — Date Terminated", "category": "employment"},
    "BE0116": {"key": "be01_years_in_line_of_work", "field_name": "Employment 1 — Years in Line of Work", "category": "employment"},
    "BE0117": {"key": "be01_employer_phone", "field_name": "Employment 1 — Employer Phone", "category": "employment"},
    "BE0119": {"key": "be01_monthly_base_pay", "field_name": "Employment 1 — Monthly Base Pay", "category": "employment"},
    "BE0133": {"key": "be01_months_in_job", "field_name": "Employment 1 — Months in This Job", "category": "employment"},
    "BE0151": {"key": "be01_date_hired", "field_name": "Employment 1 — Date Hired", "category": "employment"},
    "BE0152": {"key": "be01_months_in_line_of_work", "field_name": "Employment 1 — Months in Line of Work", "category": "employment"},
    "BE0158": {"key": "be01_employer_unit_type", "field_name": "Employment 1 — Unit Type", "category": "employment"},
    "BE0159": {"key": "be01_employer_unit_number", "field_name": "Employment 1 — Unit Number", "category": "employment"},
    "BE0160": {"key": "be01_employer_street", "field_name": "Employment 1 — Employer Street Address", "category": "employment"},
    "BE0180": {"key": "be01_foreign_address", "field_name": "Employment 1 — Foreign Address", "category": "employment"},
    "BE0236": {"key": "be01_authorization_printed", "field_name": "Employment 1 — Print Authorization", "category": "employment"},
    "BE0202": {"key": "be02_employer_name", "field_name": "Employment 2 — Employer Name", "category": "employment"},
    "BE0205": {"key": "be02_employer_city", "field_name": "Employment 2 — Employer City", "category": "employment"},
    "BE0206": {"key": "be02_employer_state", "field_name": "Employment 2 — Employer State", "category": "employment"},
    "BE0207": {"key": "be02_employer_zip", "field_name": "Employment 2 — Employer Zip", "category": "employment"},
    "BE0208": {"key": "be02_voe_is_for", "field_name": "Employment 2 — VOE Is For", "category": "employment"},
    "BE0209": {"key": "be02_employment_type", "field_name": "Employment 2 — Type (Current / Prior)", "category": "employment"},
    "BE0210": {"key": "be02_position_title", "field_name": "Employment 2 — Position / Title", "category": "employment"},
    "BE0213": {"key": "be02_years_in_job", "field_name": "Employment 2 — Years in This Job", "category": "employment"},
    "BE0214": {"key": "be02_date_terminated", "field_name": "Employment 2 — Date Terminated", "category": "employment"},
    "BE0216": {"key": "be02_years_in_line_of_work", "field_name": "Employment 2 — Years in Line of Work", "category": "employment"},
    "BE0217": {"key": "be02_employer_phone", "field_name": "Employment 2 — Employer Phone", "category": "employment"},
    "BE0219": {"key": "be02_monthly_base_pay", "field_name": "Employment 2 — Monthly Base Pay", "category": "employment"},
    "BE0233": {"key": "be02_months_in_job", "field_name": "Employment 2 — Months in This Job", "category": "employment"},
    "BE0251": {"key": "be02_date_hired", "field_name": "Employment 2 — Date Hired", "category": "employment"},
    "BE0252": {"key": "be02_months_in_line_of_work", "field_name": "Employment 2 — Months in Line of Work", "category": "employment"},
    "BE0258": {"key": "be02_employer_unit_type", "field_name": "Employment 2 — Unit Type", "category": "employment"},
    "BE0259": {"key": "be02_employer_unit_number", "field_name": "Employment 2 — Unit Number", "category": "employment"},
    "BE0260": {"key": "be02_employer_street", "field_name": "Employment 2 — Employer Street Address", "category": "employment"},
    "BE0280": {"key": "be02_foreign_address", "field_name": "Employment 2 — Foreign Address", "category": "employment"},
    "BE0302": {"key": "be03_employer_name", "field_name": "Employment 3 — Employer Name", "category": "employment"},
    "BE0308": {"key": "be03_voe_is_for", "field_name": "Employment 3 — VOE Is For", "category": "employment"},
    "BE0309": {"key": "be03_employment_type", "field_name": "Employment 3 — Type (Current / Prior)", "category": "employment"},
    "BE0310": {"key": "be03_position_title", "field_name": "Employment 3 — Position / Title", "category": "employment"},
    "BE0313": {"key": "be03_years_in_job", "field_name": "Employment 3 — Years in This Job", "category": "employment"},
    "BE0314": {"key": "be03_date_terminated", "field_name": "Employment 3 — Date Terminated", "category": "employment"},
    "BE0319": {"key": "be03_monthly_base_pay", "field_name": "Employment 3 — Monthly Base Pay", "category": "employment"},
    "BE0333": {"key": "be03_months_in_job", "field_name": "Employment 3 — Months in This Job", "category": "employment"},
    "BE0351": {"key": "be03_date_hired", "field_name": "Employment 3 — Date Hired", "category": "employment"},
    # ── Step 08 — Borrower Vesting ──
    "479": {"key": "marital_status", "field_name": "Borrower Marital Status (Vesting)", "category": "vesting"},
    "471": {"key": "borrower_sex", "field_name": "Borrower Sex (Male/Female)", "category": "borrower_info"},
    "478": {"key": "coborrower_sex", "field_name": "Co-Borrower Sex (Male/Female)", "category": "borrower_info"},
    "1867": {"key": "final_vesting", "field_name": "Final Vesting", "category": "vesting"},
    "1868": {"key": "borrower_vesting_name", "field_name": "Borrower Vesting Name", "category": "vesting"},
    "1871": {"key": "borrower_vesting_type", "field_name": "Borrower Vesting Type", "category": "vesting"},
    "1872": {"key": "borrower_vesting_desc", "field_name": "Borrower Vesting Description", "category": "vesting"},
    "1873": {"key": "coborrower_vesting_name", "field_name": "Co-Borrower Vesting Name", "category": "vesting"},
    "1876": {"key": "coborrower_vesting_type", "field_name": "Co-Borrower Vesting Type", "category": "vesting"},
    "1877": {"key": "coborrower_vesting_desc", "field_name": "Co-Borrower Vesting Description", "category": "vesting"},
    "4005": {"key": "coborrower_middle_name", "field_name": "Co-Borrower Middle Name", "category": "borrower_info"},
    "Borr.OccupancyIntent": {"key": "borrower_occupancy_intent", "field_name": "Borrower Occupancy Intent", "category": "borrower_info"},
    "CoBorr.OccupancyIntent": {"key": "coborrower_occupancy_intent", "field_name": "Co-Borrower Occupancy Intent", "category": "borrower_info"},
    "CX.NBSFLAG": {"key": "nbs_flag", "field_name": "Non-Borrowing Spouse Flag", "category": "vesting"},
    "CX.NBSINFO": {"key": "nbs_info", "field_name": "Non-Borrowing Spouse Name", "category": "vesting"},
    # ── Step 14 — Processor Workflow ──
    "CX.PRODUCTTYPE": {"key": "product_type", "field_name": "Product Type", "category": "processor_workflow"},
    # CUST42FV replaces CX.NONDEL.INV.APPROVAL (which does not exist in the prod
    # instance — confirmed via customFields schema + EC UI, 2026-07-23).
    "CUST42FV": {"key": "non_del_inv_approval", "field_name": "Non-Del Inv. Approval (Prior Approval)", "category": "processor_workflow"},
    "CX.DOCUMENTATIONTYPE": {"key": "doc_type_submission", "field_name": "Documentation Type (Submission)", "category": "processor_workflow"},
    # ── Step 11 — Transmittal Summary manual-entry fields ──
    "1551": {"key": "community_lending_ahi", "field_name": "Community Lending / Affordable Housing Initiative", "category": "loan_info"},
    "1552": {"key": "homebuyer_education_cert", "field_name": "Home Buyers Education Certification", "category": "loan_info"},
}

ALL_FIELD_IDS = list(FIELD_MAP.keys())

# ── Doc field mapping: built dynamically from required_docs.json ──
DOC_FIELD_MAP = get_doc_field_map()

# Flat set of all expected doc field keys (for quick lookup during normalization)
ALL_DOC_FIELD_KEYS = set()
for _keys in DOC_FIELD_MAP.values():
    ALL_DOC_FIELD_KEYS.update(_keys)

# ── ID document content-blob fallback ───────────────────────────────────────
# The extraction service sometimes returns only a raw OCR/markdown blob under
# the key ``document_content`` instead of the structured Driver's License schema
# fields (dl_expiry, dl_name, …). When that happens, parse the blob here so the
# downstream workflow still sees the expiry / name. Applies to the Driver's
# License and its identity fallbacks.
_ID_DOC_TYPES = {"Driver's License", "Passport", "Permanent Resident Card"}
_ID_DATE_RE = r"(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})"
# Encompass "Government ID Type" (field 5055/5056) codes keyed by doc type.
_ID_TYPE_CODE = {
    "Driver's License": "DL",
    "Passport": "PPT",
    "Permanent Resident Card": "AID",
}


def _parse_id_document_content(text: str, doc_type: str = "Driver's License") -> dict[str, str]:
    """Best-effort parse of an ID's raw OCR text into structured fields.

    Used only as a fallback when extraction returns a ``document_content`` blob
    with no structured fields. Returns whatever it can find (dl_expiry,
    borrower_dob, dl_name, dl_borrower_name, dl_gov_id, dl_gov_id_type) plus
    dl_present='Y'.
    """
    out: dict[str, str] = {}
    if not text:
        return out
    t = str(text)

    def _find(pattern: str) -> str | None:
        m = re.search(pattern, t, re.IGNORECASE)
        return m.group(1).strip() if m else None

    expiry = _find(r"(?:date\s+of\s+exp\w*|exp(?:iration|iry)?\s*date)\s*[:#\-]?\s*" + _ID_DATE_RE)
    if expiry:
        out["dl_expiry"] = expiry

    dob = _find(r"date\s+of\s+birth\s*[:#\-]?\s*" + _ID_DATE_RE)
    if dob:
        out["borrower_dob"] = dob

    family = _find(r"family\s+name\s*[:#\-]?\s*([A-Za-z][A-Za-z'\-]+)")
    given = _find(r"given\s+names?\s*[:#\-]?\s*([A-Za-z][A-Za-z'\- ]+?)\s*(?:\n|address\b|date\s+of\b|sex\b|$)")
    full_name = " ".join(p for p in (given, family) if p).strip()
    if full_name:
        out["dl_name"] = full_name
        out["dl_borrower_name"] = full_name

    # Government ID number — the "Customer identifier" (e.g. "MD-10272427156");
    # fall back to a license-number label. Strip a leading state prefix ("MD-").
    gov_id = _find(r"customer\s+identifier\s*[:#\-]?\s*([A-Za-z0-9][A-Za-z0-9\-]*)")
    if not gov_id:
        gov_id = _find(r"(?:dln|license|id)\s*(?:no\.?|number|#)?\s*[:#\-]?\s*([A-Za-z0-9][A-Za-z0-9\-]{4,})")
    if gov_id:
        gov_id = re.sub(r"^[A-Za-z]{2}-", "", gov_id).strip()
        if gov_id:
            out["dl_gov_id"] = gov_id

    if out:
        type_code = _ID_TYPE_CODE.get(doc_type)
        if type_code:
            out["dl_gov_id_type"] = type_code
        out["dl_present"] = "Y"
    return out


def _normalize_efolder_output(
    documents: list[dict],
    field_map: dict[str, list[str]] | None = None,
    multi_copy_types: set[str] | None = None,
) -> dict:
    """Normalize efolderGet (GET /efolder) response into state['doc_fields'] format.

    The GET /efolder API returns DynamoDB records with PascalCase keys:
      DocType, Status, ExtractedFields, DocRepoLocation, etc.

    For multi-copy doc types (extraction_mode="all"), field entries include a
    ``copies`` list so that values from every copy are preserved.

    Args:
        documents: List of document dicts from efolderGet response.
        field_map: Optional override for DOC_FIELD_MAP (defaults to module-level).
        multi_copy_types: Doc types where every copy should be kept (extraction_mode="all").

    Returns:
        Dict keyed by field_key with value, source_document, confidence, all_sources,
        raw_key, and optionally ``copies`` for multi-copy doc types.
    """
    active_map = field_map or DOC_FIELD_MAP
    multi_copy = multi_copy_types or set()
    all_keys = set()
    for _keys in active_map.values():
        all_keys.update(_keys)

    doc_fields: dict = {}

    def _upsert_field(field_key: str, value, doc_type: str, confidence: float,
                      raw_key: str, copy_index: int | None):
        """Insert or update a single field entry, handling multi-copy copies list."""
        has_value = value is not None and str(value).strip() != ""
        is_multi = doc_type in multi_copy

        if field_key not in doc_fields:
            entry: dict = {
                "value": value,
                "source_document": doc_type,
                "confidence": confidence,
                "raw_key": raw_key,
                "all_sources": [doc_type],
            }
            if is_multi:
                entry["copies"] = [{
                    "value": value,
                    "source_document": doc_type,
                    "confidence": confidence,
                    "copy_index": copy_index if copy_index is not None else 0,
                }]
            doc_fields[field_key] = entry
        else:
            existing = doc_fields[field_key]
            existing["all_sources"].append(doc_type)

            if is_multi:
                if "copies" not in existing:
                    existing["copies"] = [{
                        "value": existing["value"],
                        "source_document": existing["source_document"],
                        "confidence": existing["confidence"],
                        "copy_index": 0,
                    }]
                existing["copies"].append({
                    "value": value,
                    "source_document": doc_type,
                    "confidence": confidence,
                    "copy_index": copy_index if copy_index is not None else len(existing["copies"]),
                })
                if has_value:
                    primary_empty = existing.get("value") is None or str(existing.get("value", "")).strip() == ""
                    if primary_empty:
                        existing["value"] = value
                        existing["source_document"] = doc_type
                        existing["confidence"] = confidence
                        existing["raw_key"] = raw_key
            else:
                existing_val = existing.get("value")
                existing_empty = existing_val is None or str(existing_val).strip() == ""
                if existing_empty and has_value:
                    existing["value"] = value
                    existing["source_document"] = doc_type
                    existing["confidence"] = confidence
                    existing["raw_key"] = raw_key
                elif has_value and confidence > existing.get("confidence", 0):
                    existing["value"] = value
                    existing["source_document"] = doc_type
                    existing["confidence"] = confidence
                    existing["raw_key"] = raw_key

    for doc in documents:
        doc_type = doc.get("DocType") or doc.get("doc_type", "")
        doc_status = (doc.get("Status") or doc.get("status", "")).lower()

        if doc_status not in ("completed", "stored_no_extraction", "success"):
            continue

        extracted = doc.get("ExtractedFields") or doc.get("extracted_fields", {})
        if not extracted:
            continue

        copy_index = doc.get("_copy_index")
        expected_keys = set(active_map.get(doc_type, []))

        normalized_extracted = {}
        for raw_key, raw_val in extracted.items():
            norm_key = raw_key.strip().lower().replace(" ", "_").replace("-", "_")
            normalized_extracted[norm_key] = (raw_key, raw_val)

        # Fallback: the service returned only a content blob (no structured ID
        # fields) — parse the blob for the missing fields. Lower confidence so a
        # real schema extraction always wins if both are present.
        if doc_type in _ID_DOC_TYPES and "document_content" in normalized_extracted:
            _dc_raw = normalized_extracted["document_content"][1]
            _dc_text = _dc_raw.get("value") if isinstance(_dc_raw, dict) else _dc_raw
            for _pk, _pv in _parse_id_document_content(_dc_text, doc_type).items():
                if _pv in (None, "", "null"):
                    continue
                # Fill missing keys AND replace structured fields that came back
                # empty/unreadable (e.g. blank dl_expiry / dl_gov_id) — keep the
                # lower confidence so a real schema value still wins on conflict.
                _existing = normalized_extracted.get(_pk)
                _existing_val = None
                if _existing is not None:
                    _ev = _existing[1]
                    _existing_val = _ev.get("value") if isinstance(_ev, dict) else _ev
                if _existing is None or _existing_val in (None, "", "null"):
                    normalized_extracted[_pk] = (_pk, {"value": _pv, "confidence": 0.6})

        for expected_key in expected_keys:
            if expected_key in normalized_extracted:
                raw_key, raw_val = normalized_extracted[expected_key]
                value = raw_val if not isinstance(raw_val, dict) else raw_val.get("value", raw_val)
                confidence = raw_val.get("confidence", 1.0) if isinstance(raw_val, dict) else 1.0
                _upsert_field(expected_key, value, doc_type, confidence, raw_key, copy_index)

        for norm_key, (raw_key, raw_val) in normalized_extracted.items():
            if norm_key in all_keys:
                value = raw_val if not isinstance(raw_val, dict) else raw_val.get("value", raw_val)
                confidence = raw_val.get("confidence", 1.0) if isinstance(raw_val, dict) else 1.0
                _upsert_field(norm_key, value, doc_type, confidence, raw_key, copy_index)

    return doc_fields


@tool
def find_loan(
    tool_call_id: Annotated[str, InjectedToolCallId],
    state: Annotated[dict, InjectedState],
    loan_number: Optional[str] = None,
    borrower_name: Optional[str] = None,
) -> Command:
    """Find the loan GUID using loan number or borrower name.

    Uses the Encompass API to search for the loan and return its GUID.
    The GUID is stored in state['loan_id'] for all subsequent tools.

    Args:
        loan_number: The loan number to search for (preferred).
        borrower_name: Borrower name to search for (fallback).
    """
    from shared.encompass_io import is_guid, sanitize_guid

    ln = loan_number or state.get("loan_number")
    bn = borrower_name or state.get("borrower_name")

    # If state already has a REAL Encompass GUID (set by a prior find_loan
    # call), short-circuit. Critically, we validate the shape — without this,
    # an orchestrator that passes a loan_number through state["loan_id"]
    # (the original orchestrator contract bug) would cause find_loan to
    # "succeed" trusting the loan_number as a GUID, then every downstream
    # tool would 404 against /v3/loans/<loan_number>. See LG-discOrch
    # UAT2 §42 for the bug class.
    existing_loan_id = state.get("loan_id")
    if existing_loan_id and is_guid(str(existing_loan_id)):
        sanitized = sanitize_guid(str(existing_loan_id))
        logger.info(f"[FIND_LOAN] Loan ID already in state: {sanitized[:8]}...")
        result = {
            "success": True,
            "loan_id": sanitized,
            "loan_number": ln,
            "message": f"Loan ID already available: {sanitized[:8]}...",
            "source": "state",
        }
        return Command(update={
            "loan_id": sanitized,
            "loan_number": ln,
            "messages": [ToolMessage(content=json.dumps(result), tool_call_id=tool_call_id)],
        })

    if existing_loan_id and not is_guid(str(existing_loan_id)):
        # The orchestrator gave us garbage in state["loan_id"] (probably a
        # loan number). Treat it as a loan_number hint if we don't have one
        # yet, then fall through to the real Encompass search.
        if not ln:
            ln = str(existing_loan_id).strip()
            logger.info(
                f"[FIND_LOAN] state.loan_id={existing_loan_id!r} is not a GUID — "
                f"treating as loan_number={ln!r} and searching Encompass."
            )

    if not ln and not bn:
        return Command(update={"messages": [ToolMessage(
            content=json.dumps({"error": "No loan_number or borrower_name provided"}),
            tool_call_id=tool_call_id,
        )]})

    logger.info(f"[FIND_LOAN] Searching for loan: {ln or bn}")

    try:
        # Import the encompass client from the project's client module
        try:
            from encompass_client import get_encompass_client
        except ImportError:
            # Fallback: try to extract loan_id from additional_info, but only
            # if it actually looks like a GUID (same hallucination defense
            # as the main state.loan_id short-circuit above).
            additional = state.get("additional_info", {})
            additional_loan_id = additional.get("loan_id") if isinstance(additional, dict) else None
            if additional_loan_id and is_guid(str(additional_loan_id)):
                loan_id = sanitize_guid(str(additional_loan_id))
                result = {
                    "success": True,
                    "loan_id": loan_id,
                    "loan_number": ln,
                    "message": f"Found loan GUID from additional_info: {loan_id[:8]}...",
                    "source": "additional_info",
                }
                return Command(update={
                    "loan_id": loan_id,
                    "loan_number": ln,
                    "messages": [ToolMessage(content=json.dumps(result), tool_call_id=tool_call_id)],
                })
            return Command(update={"messages": [ToolMessage(
                content=json.dumps({"error": "Encompass client not available. Provide loan_id in additional_info."}),
                tool_call_id=tool_call_id,
            )]})

        client = get_encompass_client(state=state)
        if ln:
            results = client.search_loans_pipeline(loan_number=ln)
        else:
            results = client.search_loans_pipeline(borrower_name=bn)

        if not results:
            return Command(update={"messages": [ToolMessage(
                content=json.dumps({"error": f"No loan found for {ln or bn}"}),
                tool_call_id=tool_call_id,
            )]})

        raw_loan_id = results[0] if isinstance(results[0], str) else results[0].get("loanGuid", results[0].get("id"))
        loan_id = sanitize_guid(str(raw_loan_id)) if raw_loan_id else ""

        if not is_guid(loan_id):
            # Encompass returned something that doesn't look like a GUID.
            # Refuse to commit it to state — downstream tools would 404.
            return Command(update={"messages": [ToolMessage(
                content=json.dumps({
                    "error": f"Encompass returned non-GUID loan identifier: {raw_loan_id!r}",
                    "search_term": ln or bn,
                }),
                tool_call_id=tool_call_id,
            )]})

        result = {
            "success": True,
            "loan_id": loan_id,
            "loan_number": ln,
            "message": f"Found loan GUID: {loan_id[:8]}...",
            "source": "encompass_search",
        }

        logger.info(f"[FIND_LOAN] Found: {loan_id[:8]}...")

        return Command(update={
            "loan_id": loan_id,
            "loan_number": ln,
            "messages": [ToolMessage(content=json.dumps(result), tool_call_id=tool_call_id)],
        })

    except Exception as e:
        logger.error(f"[FIND_LOAN] Error: {e}")
        return Command(update={"messages": [ToolMessage(
            content=json.dumps({"error": str(e)}),
            tool_call_id=tool_call_id,
        )]})


@tool
def fetch_los_fields(
    tool_call_id: Annotated[str, InjectedToolCallId],
    state: Annotated[dict, InjectedState],
) -> Command:
    """Batch-read all 73 LOS fields from Encompass.

    Reads all field IDs in a single batch API call and organizes them into
    state['los_fields'] keyed by the internal field key.

    Each entry: {key: {value, field_id, field_name, category}}
    """
    from shared.encompass_io import read_fields

    loan_id = state.get("loan_id")
    if not loan_id:
        return Command(update={"messages": [ToolMessage(
            content=json.dumps({"error": "No loan_id in state. Run find_loan first."}),
            tool_call_id=tool_call_id,
        )]})

    logger.info(f"[FETCH_LOS] Reading {len(ALL_FIELD_IDS)} fields for loan {loan_id[:8]}...")

    try:
        raw_results = read_fields(loan_id, ALL_FIELD_IDS, context="step0_los", state=state)

        los_fields = {}
        found_count = 0
        missing_count = 0

        for field_id, value in raw_results.items():
            mapping = FIELD_MAP.get(field_id)
            if not mapping:
                continue

            key = mapping["key"]
            stripped = str(value).strip() if value is not None else ""
            has_value = value is not None and stripped != "" and stripped != "//"

            los_fields[key] = {
                "value": value if has_value else None,
                "field_id": field_id,
                "field_name": mapping["field_name"],
                "category": mapping["category"],
            }

            if has_value:
                found_count += 1
            else:
                missing_count += 1

        result = {
            "success": True,
            "fields_found": found_count,
            "fields_missing": missing_count,
            "total_fields": len(ALL_FIELD_IDS),
            "coverage_percent": round(found_count / max(len(ALL_FIELD_IDS), 1) * 100, 1),
            "message": f"Fetched {found_count}/{len(ALL_FIELD_IDS)} LOS fields ({missing_count} missing)",
        }

        logger.info(f"[FETCH_LOS] {result['message']}")

        return Command(update={
            "los_fields": los_fields,
            "messages": [ToolMessage(content=json.dumps(result), tool_call_id=tool_call_id)],
        })

    except Exception as e:
        logger.error(f"[FETCH_LOS] Error: {e}")
        return Command(update={"messages": [ToolMessage(
            content=json.dumps({"error": str(e)}),
            tool_call_id=tool_call_id,
        )]})


def _build_encompass_bucket_map(
    loan_id: str,
    state: dict,
    doc_defs: dict,
) -> tuple[dict[str, str], dict[str, int]]:
    """Build canonical_name → actual Encompass eFolder bucket title mapping.

    Calls GET /v3/loans/{loanId}/documents once to get all bucket titles for this loan,
    then matches them against encompass_buckets aliases in required_docs_conditions.json.

    Returns:
        bucket_map:  {"1003 URLA": "1003", "Transmittal Summary": "1008/LT...", ...}
        attach_count_by_canonical: {"1003 URLA": 1, "Driver's License": 0, ...}
            Number of attached files in the matched eFolder bucket, taken DIRECTLY from the
            Encompass document listing (NOT the DynamoDB extraction cache). This is the
            ground-truth presence signal: > 0 means the document physically exists in the
            eFolder even if field extraction later fails (status=not_found). Used by
            _efolder_present to distinguish "present but unextracted" from "truly absent".
    Falls back to ({}, {}) on error (caller will use canonical names / copy_count fallback).
    """
    import requests as _requests

    from encompass_client import get_encompass_client

    try:
        enc_client = get_encompass_client(state=state)
        headers = {
            "Authorization": f"Bearer {enc_client.access_token}",
            "Accept": "application/json",
        }
        url = f"{enc_client.api_base_url}/encompass/v3/loans/{loan_id}/documents"
        resp = _requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        raw_docs = resp.json()
        actual_buckets: set[str] = {d.get("title", "") for d in raw_docs if d.get("title")}
        # Attachment count per bucket title from the live eFolder listing (sum across
        # duplicate bucket titles, which Encompass allows).
        attach_by_bucket: dict[str, int] = {}
        for d in raw_docs:
            title = d.get("title")
            if title:
                attach_by_bucket[title] = attach_by_bucket.get(title, 0) + len(d.get("attachments") or [])
    except Exception as e:
        logger.warning(
            f"[BUCKET_MAP] GET /v3/loans/{loan_id[:8]}.../documents failed: {e} "
            "— will POST with canonical names (may yield not_found for aliased buckets)"
        )
        return {}, {}

    bucket_map: dict[str, str] = {}
    for canonical, defn in doc_defs.items():
        if canonical.startswith("_"):
            continue
        # Try each known alias in order; use the first one present in this loan's eFolder
        for alias in defn.get("encompass_buckets", []):
            if alias in actual_buckets:
                bucket_map[canonical] = alias
                break
        # If no alias matched, check if the canonical name itself is an exact bucket title
        if canonical not in bucket_map and canonical in actual_buckets:
            bucket_map[canonical] = canonical

    # Map attachment counts back to canonical names via the resolved bucket title
    attach_count_by_canonical: dict[str, int] = {}
    for canonical in doc_defs:
        if canonical.startswith("_"):
            continue
        resolved = bucket_map.get(canonical)
        attach_count_by_canonical[canonical] = attach_by_bucket.get(resolved, 0) if resolved else 0

    matched = len(bucket_map)
    total = sum(1 for k in doc_defs if not k.startswith("_"))
    logger.info(
        f"[BUCKET_MAP] Resolved {matched}/{total} canonical doc types to actual eFolder buckets. "
        f"Unresolved (no eFolder bucket): "
        + ", ".join(sorted(k for k in doc_defs if not k.startswith("_") and k not in bucket_map))
    )
    return bucket_map, attach_count_by_canonical


def _sequential_extract_and_collect(
    client,
    loan_number: str,
    env: str,
    required_doc_types: list[str],
    normalize_fn,
    bucket_map: dict[str, str] | None = None,
    extraction_modes: dict[str, str] | None = None,
    total_timeout: int = 120,
    poll_interval: int = 5,
) -> list[dict]:
    """Fire POST /efolder/direct for every required doc type, then poll GET until resolved.

    Phase 1 — POST each type individually using actual eFolder bucket names (via bucket_map).
              selectionMode=All for docs with extraction_mode='all' (e.g. VOE, Paystubs, Underwriting)
              so CatchingDoc returns every attachment in the bucket, not just the best one.
    Phase 2 — Poll GET /efolder until no types are pending (max total_timeout seconds).
    Phase 3 — Final GET with fields to collect extracted results.

    Returns list of raw doc dicts in GET /efolder format (DocType, Status, ExtractedFields…).
    """
    import time as _time

    ext_env = env.lower() if env else "prod"
    ext_client_id = "AWM-prod" if ext_env in ("prod", "production") else "AWM-test"
    _bucket_map = bucket_map or {}
    _extraction_modes = extraction_modes or {}

    logger.info(
        f"[EXTRACT] Fire-and-poll for {len(required_doc_types)} doc types "
        f"(loan={loan_number}, client={ext_client_id})"
    )

    # ── Phase 1: POST each doc type individually ──
    # Use actual eFolder bucket name when known — CatchingDoc matches by exact bucket title.
    # selectionMode="All" for multi-copy doc types so every attachment in the bucket is extracted.
    immediately_done = 0
    pending_types: set[str] = set()
    post_failed = 0

    for idx, doc_type in enumerate(required_doc_types, 1):
        bucket_name = _bucket_map.get(doc_type, doc_type)
        # selectionMode=All → CatchingDoc returns every PDF attachment in the bucket
        # selectionMode=Best → CatchingDoc returns only the single best-match attachment
        sel_mode = "All" if str(_extraction_modes.get(doc_type, "")).lower() == "all" else "Best"
        if bucket_name != doc_type:
            logger.info(f"[EXTRACT] POST [{idx}/{len(required_doc_types)}]: {doc_type} (bucket: {bucket_name!r}, mode: {sel_mode})")
        else:
            logger.info(f"[EXTRACT] POST [{idx}/{len(required_doc_types)}]: {doc_type} (mode: {sel_mode})")
        try:
            resp = client._call_api(
                loan_number=loan_number,
                client_id=ext_client_id,
                document_types=[bucket_name],
                environment=ext_env,
                selection_mode=sel_mode,
                use_cache=True,
                override_not_found=True,
            )
        except Exception as e:
            logger.warning(f"[EXTRACT]   POST failed for {doc_type}: {e}")
            post_failed += 1
            continue

        if not resp.get("success"):
            logger.warning(f"[EXTRACT]   API error for {doc_type}: {resp.get('error', '?')}")
            post_failed += 1
            continue

        # Response doc_type may be bucket_name — check both
        immediate_status = ""
        for d in resp.get("body", {}).get("documents", []):
            d_type = d.get("doc_type", "")
            if d_type in (doc_type, bucket_name):
                immediate_status = (d.get("status") or "").lower()
                break

        terminal = (
            "success", "completed", "not_found", "failed",
            "stored_no_extraction", "error-dl", "error-ext",
            "error-sch", "error-timeout",
        )
        if immediate_status in terminal:
            immediately_done += 1
        else:
            pending_types.add(doc_type)

    logger.info(
        f"[EXTRACT] Phase 1 done — {immediately_done} immediate, "
        f"{len(pending_types)} pending, {post_failed} POST errors"
    )

    # ── Phase 2: Poll GET until all pending types resolve ──
    if pending_types:
        logger.info(f"[EXTRACT] Phase 2 — polling for {len(pending_types)} pending types...")
        deadline = _time.time() + total_timeout
        poll_n = 0
        while pending_types and _time.time() < deadline:
            _time.sleep(poll_interval)
            poll_n += 1
            try:
                cache_resp = client.get_documents(loan_number, include_fields=False)
            except Exception as e:
                logger.warning(f"[EXTRACT]   poll {poll_n} error: {e}")
                continue

            for doc_type in list(pending_types):
                resolved = False
                still_pending = False
                doc_bucket = _bucket_map.get(doc_type, doc_type)
                for d in cache_resp.get("documents", []):
                    d_dt = d.get("DocType", "")
                    if d_dt not in (doc_type, doc_bucket):
                        continue
                    st = (d.get("Status") or "").lower()
                    if st == "pending":
                        still_pending = True
                    else:
                        resolved = True
                if resolved and not still_pending:
                    pending_types.discard(doc_type)

            elapsed = _time.time() - (deadline - total_timeout)
            if poll_n % 4 == 0 or not pending_types:
                logger.info(
                    f"[EXTRACT]   poll {poll_n}: {len(pending_types)} still pending ({elapsed:.0f}s)"
                )

        if pending_types:
            logger.warning(
                f"[EXTRACT] Phase 2 timed out — {len(pending_types)} types still pending: "
                + ", ".join(sorted(pending_types)[:10])
            )
    else:
        logger.info("[EXTRACT] Phase 2 skipped — nothing pending after Phase 1")

    # ── Phase 3: Final GET with fields ──
    logger.info("[EXTRACT] Phase 3 — final GET with fields...")
    get_resp = client.get_documents(loan_number, include_fields=True)
    if get_resp.get("error"):
        logger.warning(f"[EXTRACT] Final GET failed: {get_resp['error']}")
        return []

    results: list[dict] = []
    completed_count = 0
    skipped_count = 0
    for doc in get_resp.get("documents", []):
        status = (doc.get("Status") or "").lower()
        if status in ("not_found", "failed", "error-dl", "error-ext", "error-sch", "error-timeout"):
            skipped_count += 1
            continue
        raw_dt = doc.get("DocType", "")
        if raw_dt:
            doc["DocType"] = normalize_fn(raw_dt)
        if doc.get("DocType"):
            results.append(doc)
            completed_count += 1

    logger.info(
        f"[EXTRACT] Phase 3 done — {completed_count} usable, {skipped_count} skipped (not_found/failed)"
    )
    return results


@tool
def fetch_doc_fields(
    tool_call_id: Annotated[str, InjectedToolCallId],
    state: Annotated[dict, InjectedState],
) -> Command:
    """Fetch required document fields from eFolder, triggering extraction when cache is empty.

    1. Derives which doc types are REQUIRED based on loan characteristics
       (loan_type, loan_purpose, borrower_count) from required_docs_conditions.json.
    2. Calls GET /efolder?loanNumber=X&includeFields=true — reads DynamoDB cache.
    3. If DynamoDB cache is empty, triggers extraction via POST /efolder/direct for each
       required doc type (fire-and-poll pattern), then collects results.
    4. Filters response to ONLY required doc types (ignores irrelevant docs).
    5. Stores required documents in state['efolder_documents'].
    6. Normalizes expected doc fields into state['doc_fields'].

    Flow: GET /efolder -> (if empty: POST each type → poll → final GET) -> filter -> normalize -> state
    """
    loan_number = state.get("loan_number")
    if not loan_number:
        return Command(update={"messages": [ToolMessage(
            content=json.dumps({"error": "No loan_number in state."}),
            tool_call_id=tool_call_id,
        )]})

    env = state.get("env", "Test").lower()

    # ── Derive loan characteristics for dynamic doc type selection ──
    loan_type, loan_purpose, borrower_count = _derive_loan_characteristics(state)
    required_doc_types, extraction_modes = get_required_documents_for_loan(
        loan_type, loan_purpose, borrower_count,
    )

    logger.info(
        f"[FETCH_DOCS] Loan: type={loan_type or '?'}, purpose={loan_purpose or '?'}, "
        f"borrowers={borrower_count} -> {len(required_doc_types)} required doc types"
    )
    logger.info(f"[FETCH_DOCS] Fetching docs for loan {loan_number} ({env}) via GET /efolder...")

    try:
        from shared.efolder_client import EfolderClient

        client = EfolderClient()

        # ── Step 1: GET /efolder — read DynamoDB cache ──
        get_resp = client.get_documents(loan_number, include_fields=True)

        if "error" in get_resp:
            logger.warning(f"[FETCH_DOCS] efolderGet error: {get_resp['error']} — treating as empty cache")
            get_resp = {"documents": []}

        all_documents = get_resp.get("documents", [])
        logger.info(f"[FETCH_DOCS] GET /efolder returned {len(all_documents)} total documents from DynamoDB")

        # ── Build eFolder bucket map for this loan ──
        # CatchingDoc matches by exact eFolder bucket title — "1003 URLA" won't match the
        # bucket named "1003". This map resolves canonical names to actual bucket titles.
        loan_id = state.get("loan_id", "")
        _conditions_cfg = _load_conditions_config()
        _doc_defs = _conditions_cfg.get("document_definitions", {})
        _encompass_bucket_map: dict[str, str] = {}
        _efolder_attach_counts: dict[str, int] = {}
        if loan_id:
            _encompass_bucket_map, _efolder_attach_counts = _build_encompass_bucket_map(loan_id, state, _doc_defs)
        else:
            logger.warning("[FETCH_DOCS] No loan_id in state — cannot build eFolder bucket map; extraction may yield not_found for aliased buckets")

        # Build reverse map: bucket_name → canonical (for _norm_dt normalization)
        _bucket_to_canonical: dict[str, str] = {
            bucket: canonical for canonical, bucket in _encompass_bucket_map.items()
        }
        # Also add encompass_buckets aliases from config as fallback (handles DynamoDB reads)
        for canonical, defn in _doc_defs.items():
            if canonical.startswith("_"):
                continue
            for alias in defn.get("encompass_buckets", []):
                if alias not in _bucket_to_canonical:
                    _bucket_to_canonical[alias] = canonical

        # ── Doctype normalisation: map alias/bucket names → canonical required name ──
        required_set = set(required_doc_types)
        _req_lower = {r.lower(): r for r in required_doc_types}

        def _norm_dt(raw: str) -> str:
            if raw in required_set:
                return raw
            # Exact bucket → canonical
            if raw in _bucket_to_canonical:
                return _bucket_to_canonical[raw]
            # Case-insensitive fallback
            return _req_lower.get(raw.lower(), raw)

        for doc in all_documents:
            raw_dt = doc.get("DocType", "")
            if raw_dt:
                doc["DocType"] = _norm_dt(raw_dt)

        # ── Step 2: Trigger extraction for any required doc not already in cache ──
        # force_extract=True in state bypasses cache entirely and re-extracts everything.
        force_extract = state.get("force_extract", False)

        cached_types = {doc.get("DocType", "") for doc in all_documents}

        # A cached type still needs (re-)extraction when ALL of its cached records
        # are not_found/failed BUT the live eFolder listing shows the bucket has
        # attachments (efolder_listing_count > 0). This self-heals stale not_found
        # records left by earlier extraction attempts that POSTed the canonical name
        # (e.g. "1003 URLA") instead of the actual bucket title ("1003"). Phase 1
        # below already POSTs the resolved bucket title, so re-running it succeeds —
        # no separate retrigger pass needed. Once it succeeds the success record is
        # cached, so this only fires until the doc extracts cleanly.
        _GOOD_STATUSES = {"completed", "success", "stored_no_extraction"}
        _cached_statuses: dict[str, set[str]] = {}
        for _doc in all_documents:
            _cached_statuses.setdefault(_doc.get("DocType", ""), set()).add(
                (_doc.get("Status", "") or "").lower()
            )

        def _present_but_unextracted(dt: str) -> bool:
            statuses = _cached_statuses.get(dt, set())
            if statuses & _GOOD_STATUSES:
                return False  # already have a usable extraction
            return _efolder_attach_counts.get(dt, 0) > 0

        if force_extract:
            types_to_extract = required_doc_types
            logger.info(
                f"[FETCH_DOCS] force_extract=True — ignoring {len(all_documents)} cached docs, "
                f"re-extracting all {len(required_doc_types)} types..."
            )
            all_documents = []
        else:
            types_to_extract = [
                dt for dt in required_doc_types
                if dt not in cached_types or _present_but_unextracted(dt)
            ]
            _reextract = [dt for dt in types_to_extract if dt in cached_types]
            if _reextract:
                logger.info(
                    f"[FETCH_DOCS] Re-extracting {len(_reextract)} cached-but-not_found type(s) "
                    f"present in the live eFolder: {_reextract[:5]}{'...' if len(_reextract) > 5 else ''}"
                )

        if types_to_extract:
            logger.info(
                f"[FETCH_DOCS] Triggering extraction for {len(types_to_extract)} "
                f"doc type(s) not in cache: {types_to_extract[:5]}{'...' if len(types_to_extract) > 5 else ''}"
            )
            extracted = _sequential_extract_and_collect(
                client, loan_number, env, types_to_extract, _norm_dt,
                bucket_map=_encompass_bucket_map,
                extraction_modes=extraction_modes,
            )
            # `extracted` is a fresh full GET (usable records only, DocType normalized).
            # Drop any prior cached records for the same types so the fresh/usable record
            # wins — this prevents a stale not_found stub from shadowing a now-successful
            # extraction and avoids duplicating already-good multi-copy types.
            _extracted_types = {d.get("DocType", "") for d in extracted}
            all_documents = [
                d for d in all_documents if d.get("DocType", "") not in _extracted_types
            ] + extracted
            logger.info(
                f"[FETCH_DOCS] Extraction complete — {len(extracted)} new docs, "
                f"{len(all_documents)} total"
            )
        else:
            logger.info(f"[FETCH_DOCS] All {len(required_doc_types)} required types found in cache — skipping extraction")

        # Group ALL documents by DocType (keep every copy)
        all_docs_by_type: dict[str, list[dict]] = {}
        for doc in all_documents:
            dt = doc.get("DocType", "")
            if not dt:
                continue
            all_docs_by_type.setdefault(dt, []).append(doc)

        # Filter to ONLY required doc types — ignore irrelevant docs
        docs_by_type: dict[str, list[dict]] = {
            dt: docs for dt, docs in all_docs_by_type.items() if dt in required_set
        }
        ignored_count = len(all_docs_by_type) - len(docs_by_type)

        # For "all" extraction mode keep every copy; otherwise keep best single doc.
        # A doc type's cache can hold both a stale not_found stub (keyed by the canonical
        # name) and a fresh usable record (keyed by the bucket title) that both normalize
        # to the same DocType. Drop the not_found/failed stubs when at least one usable
        # copy exists so empty-coordinate stubs never shadow the real document.
        _BAD_STATUSES = {"not_found", "failed", "error-dl", "error-ext", "error-sch", "error-timeout"}

        def _is_usable(d: dict) -> bool:
            return (d.get("Status", "") or "").lower() not in _BAD_STATUSES

        multi_copy_types = {dt for dt, mode in extraction_modes.items() if str(mode).lower() == "all"}
        flat_docs_for_normalize: list[dict] = []
        for dt, doc_list in docs_by_type.items():
            usable = [d for d in doc_list if _is_usable(d)]
            effective = usable if usable else doc_list
            if dt in multi_copy_types:
                for idx, d in enumerate(effective):
                    d["_copy_index"] = idx
                    flat_docs_for_normalize.append(d)
            else:
                flat_docs_for_normalize.append(effective[0])

        total_docs_kept = len(flat_docs_for_normalize)
        logger.info(
            f"[FETCH_DOCS] DynamoDB: {len(all_docs_by_type)} unique types, "
            f"{len(docs_by_type)} required ({total_docs_kept} docs incl. copies), "
            f"{ignored_count} ignored"
        )

        # ── Normalize to doc_fields — only from required docs ──
        doc_fields = _normalize_efolder_output(
            flat_docs_for_normalize, multi_copy_types=multi_copy_types,
        )

        # ── Build document inventory — required docs only ──
        efolder_documents = {}
        completed_count = 0
        not_found_types = []
        pending_types = []
        failed_types = []

        for dt, doc_list in docs_by_type.items():
            is_multi = dt in multi_copy_types
            copies_info: list[dict] = []
            dt_completed = 0

            for idx, doc in enumerate(doc_list):
                status = (doc.get("Status", "") or "").lower()
                extracted = doc.get("ExtractedFields", {})

                fields_summary = {}
                for field_name, field_val in extracted.items():
                    if isinstance(field_val, dict):
                        fields_summary[field_name] = {
                            "value": field_val.get("value", field_val),
                            "confidence": field_val.get("confidence", 1.0),
                        }
                    else:
                        fields_summary[field_name] = {
                            "value": field_val,
                            "confidence": 1.0,
                        }

                copy_entry = {
                    "copy_index": idx,
                    "status": status,
                    "source": doc.get("Source", ""),
                    "document_title": doc.get("DocumentTitle", ""),
                    "attachment_id": doc.get("AttachmentID", ""),
                    "attachment_name": doc.get("AttachmentName", ""),
                    "file_size": doc.get("FileSizeBytes", 0),
                    "extracted_fields_count": doc.get("ExtractedFieldsCount", len(extracted)),
                    "extracted_fields": fields_summary,
                    "docrepo_location": doc.get("DocRepoLocation", ""),
                    "docrepo_bucket": doc.get("Bucket", ""),
                    "docrepo_client_id": doc.get("Client", ""),
                    "error": doc.get("FailureReason"),
                }
                copies_info.append(copy_entry)

                if status in ("completed", "stored_no_extraction"):
                    dt_completed += 1
                elif status == "pending" and dt not in pending_types:
                    pending_types.append(dt)
                elif status.startswith("error") and dt not in failed_types:
                    failed_types.append(dt)

                logger.info(
                    f"[FETCH_DOCS]   {dt}[{idx}]: status={status}, "
                    f"fields={len(extracted)}, "
                    f"docrepo={doc.get('DocRepoLocation', '') or 'N/A'}"
                )

            if dt_completed > 0:
                completed_count += 1

            primary = copies_info[0] if copies_info else {}
            efolder_documents[dt] = {
                "doc_type": dt,
                "copy_count": len(doc_list),
                "is_multi_copy": is_multi,
                "status": primary.get("status", "unknown") if len(copies_info) == 1 else "multiple",
                "source": primary.get("source", ""),
                "document_title": primary.get("document_title", ""),
                "attachment_id": primary.get("attachment_id", ""),
                "attachment_name": primary.get("attachment_name", ""),
                "file_size": primary.get("file_size", 0),
                "extracted_fields_count": primary.get("extracted_fields_count", 0),
                "extracted_fields": primary.get("extracted_fields", {}),
                "docrepo_location": primary.get("docrepo_location", ""),
                "docrepo_bucket": primary.get("docrepo_bucket", ""),
                "docrepo_client_id": primary.get("docrepo_client_id", ""),
                "extraction_mode": extraction_modes.get(dt, "best"),
                "error": primary.get("error"),
                "copies": copies_info,
                # Actual eFolder bucket name used for extraction (may differ from canonical doc_type)
                "encompass_bucket": _encompass_bucket_map.get(dt, dt),
                # Ground-truth presence: # of attachments in the actual eFolder bucket
                # (from GET /documents). > 0 means the file physically exists even when a
                # cached DynamoDB record reports status=not_found. Used by _efolder_present.
                "efolder_listing_count": _efolder_attach_counts.get(dt, 0),
            }

        # Mark required docs that are missing from DynamoDB
        for dt in required_doc_types:
            if dt not in docs_by_type:
                not_found_types.append(dt)
                efolder_documents[dt] = {
                    "doc_type": dt,
                    "copy_count": 0,
                    "is_multi_copy": dt in multi_copy_types,
                    "status": "not_found",
                    "source": "",
                    "document_title": "",
                    "attachment_id": "",
                    "attachment_name": "",
                    "file_size": 0,
                    "extracted_fields_count": 0,
                    "extracted_fields": {},
                    "docrepo_location": "",
                    "docrepo_bucket": "",
                    "docrepo_client_id": "",
                    "extraction_mode": extraction_modes.get(dt, "best"),
                    "error": "Not found in DynamoDB cache",
                    "copies": [],
                    # Actual eFolder bucket name used for extraction (may differ from canonical doc_type)
                    "encompass_bucket": _encompass_bucket_map.get(dt, dt),
                    # Ground-truth presence: # of attachments in the actual eFolder bucket
                    # (from GET /documents). > 0 means the file physically exists even though
                    # extraction failed / cache missed. Used by _efolder_present.
                    "efolder_listing_count": _efolder_attach_counts.get(dt, 0),
                }
                logger.info(f"[FETCH_DOCS]   {dt}: NOT FOUND in DynamoDB")

        # Overall metadata
        first_doc_list = next(iter(docs_by_type.values()), [])
        first_doc = first_doc_list[0] if first_doc_list else {}
        efolder_documents["_meta"] = {
            "loan_number": loan_number,
            "loan_guid": first_doc.get("LoanGuid", ""),
            "loan_type": loan_type,
            "loan_purpose": loan_purpose,
            "borrower_count": borrower_count,
            "multi_copy_doc_types": sorted(list(multi_copy_types)),
            "total_in_dynamodb": len(all_docs_by_type),
            "total_required": len(required_doc_types),
            "ignored_non_required": ignored_count,
            "completed": completed_count,
            "not_found": len(not_found_types),
            "pending": len(pending_types),
            "failed": len(failed_types),
            "not_found_doc_types": not_found_types,
            "pending_doc_types": pending_types,
            "failed_doc_types": failed_types,
            "required_doc_types": required_doc_types,
            "source": "efolderExtract (POST+poll)" if types_to_extract else "efolderGet (GET /efolder DynamoDB)",
            "retrieved_at": datetime.now().isoformat(),
        }

        # Track which expected doc fields are still missing
        expected_total = len(ALL_DOC_FIELD_KEYS)
        found_keys = set(doc_fields.keys())
        missing_keys = ALL_DOC_FIELD_KEYS - found_keys

        result = {
            "success": True,
            "loan_type": loan_type,
            "loan_purpose": loan_purpose,
            "borrower_count": borrower_count,
            "total_in_dynamodb": len(all_docs_by_type),
            "required_doc_types": len(required_doc_types),
            "ignored_non_required": ignored_count,
            "completed": completed_count,
            "not_found": len(not_found_types),
            "pending": len(pending_types),
            "fields_normalized": len(doc_fields),
            "fields_expected": expected_total,
            "fields_missing": sorted(list(missing_keys)),
            "not_found_doc_types": not_found_types,
            "pending_doc_types": pending_types,
            "message": (
                f"{completed_count}/{len(required_doc_types)} required docs found "
                f"({ignored_count} non-required ignored). "
                f"Normalized {len(doc_fields)}/{expected_total} doc fields. "
                f"Loan: {loan_type} {loan_purpose}, {borrower_count} borrower(s)."
            ),
        }

        if not_found_types:
            result["message"] += f" Missing: {', '.join(not_found_types)}."
        if pending_types:
            result["message"] += (
                f" EXTRACTION STILL IN PROGRESS for: {', '.join(pending_types)}. "
                "Call wait_for_pending_docs([...]) before any step that needs these documents."
            )

        logger.info(f"[FETCH_DOCS] {result['message']}")

        return Command(update={
            "doc_fields": doc_fields,
            "efolder_documents": efolder_documents,
            "messages": [ToolMessage(content=json.dumps(result), tool_call_id=tool_call_id)],
        })

    except Exception as e:
        logger.error(f"[FETCH_DOCS] Error: {e}")
        return Command(update={"messages": [ToolMessage(
            content=json.dumps({"error": str(e)}),
            tool_call_id=tool_call_id,
        )]})


@tool
def wait_for_pending_docs(
    doc_types: list[str],
    tool_call_id: Annotated[str, InjectedToolCallId],
    state: Annotated[dict, InjectedState],
) -> Command:
    """Poll GET /efolder until the specified doc types finish extraction, then merge into state.

    Call this before any step that needs a document that was still pending after
    fetch_doc_fields (i.e. efolder_documents[doc_type].status == 'pending').

    Args:
        doc_types: List of document type names to wait for (e.g. ["1003 URLA", "Underwriting"]).

    Returns a summary of which docs resolved, which are still pending, and which failed.
    The agent should proceed regardless — do not block the whole workflow on one slow doc.
    """
    import time as _time

    loan_number = state.get("loan_number")
    if not loan_number:
        return Command(update={"messages": [ToolMessage(
            content=json.dumps({"error": "No loan_number in state."}),
            tool_call_id=tool_call_id,
        )]})

    if not doc_types:
        return Command(update={"messages": [ToolMessage(
            content=json.dumps({"success": True, "message": "No doc_types specified — nothing to wait for."}),
            tool_call_id=tool_call_id,
        )]})

    # Derive required doc config for extraction modes
    loan_type, loan_purpose, borrower_count = _derive_loan_characteristics(state)
    _, extraction_modes = get_required_documents_for_loan(loan_type, loan_purpose, borrower_count)
    multi_copy_types = {dt for dt, mode in extraction_modes.items() if str(mode).lower() == "all"}

    POLL_TIMEOUT = 90
    POLL_INTERVAL = 5

    logger.info(
        f"[WAIT_DOCS] Waiting for {len(doc_types)} pending doc(s): {doc_types} "
        f"(loan={loan_number}, timeout={POLL_TIMEOUT}s)"
    )

    try:
        from shared.efolder_client import EfolderClient
        client = EfolderClient()

        pending_set = set(doc_types)
        resolved: list[str] = []
        failed: list[str] = []
        deadline = _time.time() + POLL_TIMEOUT
        poll_n = 0

        while pending_set and _time.time() < deadline:
            _time.sleep(POLL_INTERVAL)
            poll_n += 1
            try:
                resp = client.get_documents(loan_number, include_fields=False)
            except Exception as e:
                logger.warning(f"[WAIT_DOCS] poll {poll_n} GET error: {e}")
                continue

            for doc_type in list(pending_set):
                for d in resp.get("documents", []):
                    if d.get("DocType") != doc_type:
                        continue
                    st = (d.get("Status") or "").lower()
                    if st == "pending":
                        break
                    pending_set.discard(doc_type)
                    if st in ("completed", "stored_no_extraction", "success"):
                        resolved.append(doc_type)
                    else:
                        failed.append(doc_type)
                    break

            if poll_n % 3 == 0:
                logger.info(f"[WAIT_DOCS] poll {poll_n}: {len(pending_set)} still pending")

        timed_out = list(pending_set)
        if timed_out:
            logger.warning(f"[WAIT_DOCS] Timeout — still pending: {timed_out}")

        # ── Fetch full fields for newly resolved docs and merge into state ──
        efolder_documents = dict(state.get("efolder_documents") or {})
        doc_fields = dict(state.get("doc_fields") or {})

        if resolved:
            logger.info(f"[WAIT_DOCS] Fetching fields for {len(resolved)} resolved doc(s)...")
            get_resp = client.get_documents(loan_number, include_fields=True)
            all_docs = get_resp.get("documents", []) if not get_resp.get("error") else []

            for doc in all_docs:
                dt = doc.get("DocType", "")
                if dt not in resolved:
                    continue
                status = (doc.get("Status") or "").lower()
                extracted = doc.get("ExtractedFields", {})
                is_multi = dt in multi_copy_types

                # Build fields_summary
                fields_summary: dict = {}
                for field_name, field_val in extracted.items():
                    if isinstance(field_val, dict):
                        fields_summary[field_name] = {
                            "value": field_val.get("value", field_val),
                            "confidence": field_val.get("confidence", 1.0),
                        }
                    else:
                        fields_summary[field_name] = {"value": field_val, "confidence": 1.0}

                efolder_documents[dt] = {
                    "doc_type": dt,
                    "copy_count": 1,
                    "is_multi_copy": is_multi,
                    "status": status,
                    "source": doc.get("Source", ""),
                    "document_title": doc.get("DocumentTitle", ""),
                    "attachment_id": doc.get("AttachmentID", ""),
                    "attachment_name": doc.get("AttachmentName", ""),
                    "file_size": doc.get("FileSizeBytes", 0),
                    "extracted_fields_count": doc.get("ExtractedFieldsCount", len(extracted)),
                    "extracted_fields": fields_summary,
                    "docrepo_location": doc.get("DocRepoLocation", ""),
                    "docrepo_bucket": doc.get("Bucket", ""),
                    "docrepo_client_id": doc.get("Client", ""),
                    "extraction_mode": extraction_modes.get(dt, "best"),
                    "error": doc.get("FailureReason"),
                    "copies": [],
                }

                # Merge extracted fields into doc_fields using same normalisation logic
                new_fields = _normalize_efolder_output([doc], multi_copy_types=multi_copy_types)
                doc_fields.update(new_fields)
                logger.info(f"[WAIT_DOCS]   {dt}: {len(extracted)} fields merged into state")

        result = {
            "success": True,
            "resolved": resolved,
            "failed": failed,
            "timed_out": timed_out,
            "message": (
                f"Resolved {len(resolved)}, failed {len(failed)}, "
                f"still pending after timeout: {len(timed_out)}. "
                + (f"Proceed without: {timed_out}." if timed_out else "All requested docs ready.")
            ),
        }
        logger.info(f"[WAIT_DOCS] {result['message']}")

        return Command(update={
            "efolder_documents": efolder_documents,
            "doc_fields": doc_fields,
            "messages": [ToolMessage(content=json.dumps(result), tool_call_id=tool_call_id)],
        })

    except Exception as e:
        logger.error(f"[WAIT_DOCS] Error: {e}")
        return Command(update={"messages": [ToolMessage(
            content=json.dumps({"error": str(e)}),
            tool_call_id=tool_call_id,
        )]})


# ── URLA field key → loan_summary section mapping ──
# Maps los_fields keys to their slot in the loan_summary dict.
# This is auto-generated from the field registry.
URLA_BORROWER_KEYS = [
    "borrower_first_name", "borrower_middle_name", "borrower_last_name",
    "borrower_ssn", "borrower_dob", "borrower_marital_status",
    "borrower_sex", "borrower_aka",
]
URLA_PROPERTY_KEYS = [
    "property_address", "property_city", "property_state",
    "property_zip", "property_county",
]
URLA_LOAN_TERMS_KEYS = [
    "preflight_mortgage_type", "preflight_loan_purpose",
    "preflight_loan_amount", "preflight_appraised_value",
    "preflight_ltv", "preflight_note_rate",
    "occupancy_status",
]
URLA_DATES_KEYS = [
    "closing_date", "preflight_lock_expiration",
]
URLA_VESTING_KEYS = [
    "final_vesting", "manner_held",
]
URLA_PREFLIGHT_KEYS = [
    "preflight_ctc_status", "preflight_cd_status",  # field 2305 (Clear to Close date), CX.CD.APPROVED.DATE
    "preflight_over_under",
]
URLA_CLOSING_KEYS = [
    "closing_conditions_text", "elective_insurance",
]


def _safe_get(los_fields: dict, key: str) -> str | None:
    """Safely extract a value from los_fields."""
    entry = los_fields.get(key)
    if entry and isinstance(entry, dict):
        v = entry.get("value")
        if v is not None and str(v).strip():
            return str(v).strip()
    return None


def _mask_ssn(ssn: str | None) -> str | None:
    """Mask SSN to show only last 4 digits."""
    if not ssn:
        return None
    digits = ssn.replace("-", "").replace(" ", "")
    if len(digits) >= 4:
        return f"***-**-{digits[-4:]}"
    return ssn


@tool
def build_loan_summary(
    tool_call_id: Annotated[str, InjectedToolCallId],
    state: Annotated[dict, InjectedState],
) -> Command:
    """Build the loan summary (URLA equivalent) from los_fields.

    Creates a categorized, human-readable snapshot of the loan stored in
    state['loan_summary']. This runs once in Step 0.4 and NEVER changes
    afterwards — it is the single source of truth for the loan profile.

    Categories: borrower, property, loan_terms, dates, vesting, preflight,
    closing, derived (has_coborrower, is_note_llc), _meta.
    """
    los = state.get("los_fields", {})

    if not los:
        return Command(update={"messages": [ToolMessage(
            content=json.dumps({"error": "los_fields is empty. Run fetch_los_fields first."}),
            tool_call_id=tool_call_id,
        )]})

    logger.info("[LOAN_SUMMARY] Building loan summary (URLA) from los_fields...")

    def _get(key):
        return _safe_get(los, key)

    # ── Borrower ──
    borrower = {}
    for k in URLA_BORROWER_KEYS:
        v = _get(k)
        if k == "borrower_ssn":
            v = _mask_ssn(v)
        if v is not None:
            borrower[k] = v

    # ── Property ──
    prop = {}
    for k in URLA_PROPERTY_KEYS:
        v = _get(k)
        if v is not None:
            prop[k] = v

    # ── Loan Terms ──
    loan_terms = {}
    for k in URLA_LOAN_TERMS_KEYS:
        v = _get(k)
        if v is not None:
            loan_terms[k] = v

    # ── Dates ──
    dates = {}
    for k in URLA_DATES_KEYS:
        v = _get(k)
        if v is not None:
            dates[k] = v

    # ── Vesting ──
    vesting = {}
    for k in URLA_VESTING_KEYS:
        v = _get(k)
        if v is not None:
            vesting[k] = v

    # ── Preflight ──
    preflight = {}
    for k in URLA_PREFLIGHT_KEYS:
        v = _get(k)
        if v is not None:
            preflight[k] = v

    # ── Closing ──
    closing = {}
    for k in URLA_CLOSING_KEYS:
        v = _get(k)
        if v is not None:
            closing[k] = v

    # ── Derived flags ──
    has_coborrower = False
    coborrower_last = _get("preflight_has_coborrower")
    if coborrower_last:
        has_coborrower = True

    # is_note_llc: check LO/processor email, lender name, or CD page 5 lender
    is_note_llc = False
    prop_state = (_get("property_state") or "").upper()
    lo_email = (_get("lo_email") or "").lower()
    processor_email = (_get("processor_email") or "").lower()
    lender_name = (_get("lender_name_alt") or "").lower()
    cd5_lender = (_get("cd5_lender_name") or "").lower()
    _NOTE_LLC_NAMES = ("note mortgage", "note llc", "note mortgage llc")
    if (
        "@notemortgage.com" in lo_email
        or "@notemortgage.com" in processor_email
        or any(n in lender_name for n in _NOTE_LLC_NAMES)
        or any(n in cd5_lender for n in _NOTE_LLC_NAMES)
    ):
        is_note_llc = True

    # Trust flag
    is_trust = False
    trust_flag = (_get("close_trust_flag") or "").strip().lower()
    if trust_flag in ("true", "yes", "1", "y"):
        is_trust = True

    ltv_str = _get("preflight_ltv")
    ltv = None
    if ltv_str:
        try:
            ltv = float(ltv_str)
        except (ValueError, TypeError):
            ltv = None

    # Mortgage type / purpose: the preflight summary fields are often blank, so
    # fall back to the authoritative LOS fields (1172 Mortgage Type, loan_purpose)
    # before defaulting. Defaulting straight to "Conventional" silently mis-gates
    # FHA-specific logic on FHA loans where the preflight field never populated.
    _mortgage_type = _get("preflight_mortgage_type") or _get("loan_type")
    _loan_purpose = _get("preflight_loan_purpose") or _get("loan_purpose")

    derived = {
        "has_coborrower": has_coborrower,
        "is_note_llc": is_note_llc,
        "is_trust": is_trust,
        "loan_type": _mortgage_type,
        "loan_purpose": _loan_purpose,
        "ltv": ltv,
    }

    # ── Loan Profile (5 discriminators for rule modifiers) ──
    loan_profile = {
        "loan_type": _mortgage_type or "Conventional",
        "purpose": _loan_purpose or "Purchase",
        "state": prop_state,
        "trust": is_trust,
        "note_llc": is_note_llc,
    }

    # ── Coverage stats ──
    all_urla_keys = (
        URLA_BORROWER_KEYS + URLA_PROPERTY_KEYS + URLA_LOAN_TERMS_KEYS
        + URLA_DATES_KEYS + URLA_VESTING_KEYS + URLA_PREFLIGHT_KEYS
        + URLA_CLOSING_KEYS
    )
    available = sum(1 for k in all_urla_keys if _get(k) is not None)
    missing_keys = [k for k in all_urla_keys if _get(k) is None]

    loan_summary = {
        "borrower": borrower,
        "property": prop,
        "loan_terms": loan_terms,
        "dates": dates,
        "vesting": vesting,
        "preflight": preflight,
        "closing": closing,
        "derived": derived,
        "_meta": {
            "total_urla_fields": len(all_urla_keys),
            "fields_available": available,
            "fields_missing": missing_keys,
            "coverage_percent": round(available / max(len(all_urla_keys), 1) * 100, 1),
            "built_at": datetime.now().isoformat(),
            "source": "los_fields",
            "immutable": True,
        },
    }

    result = {
        "success": True,
        "coverage_percent": loan_summary["_meta"]["coverage_percent"],
        "fields_available": available,
        "fields_missing_count": len(missing_keys),
        "has_coborrower": has_coborrower,
        "loan_type": derived["loan_type"],
        "loan_purpose": derived["loan_purpose"],
        "ltv": ltv,
        "loan_profile": loan_profile,
        "message": (
            f"Loan summary (URLA) built: {available}/{len(all_urla_keys)} fields "
            f"({loan_summary['_meta']['coverage_percent']}%). "
            f"Loan: {derived['loan_type']} {derived['loan_purpose']}, "
            f"LTV: {ltv}, CoBorrower: {has_coborrower}. "
            f"Profile: type={loan_profile['loan_type']}, "
            f"purpose={loan_profile['purpose']}, state={loan_profile['state']}, "
            f"trust={loan_profile['trust']}, note_llc={loan_profile['note_llc']}"
        ),
    }

    logger.info(f"[LOAN_SUMMARY] {result['message']}")

    return Command(update={
        "loan_summary": loan_summary,
        "loan_profile": loan_profile,
        "messages": [ToolMessage(content=json.dumps(result), tool_call_id=tool_call_id)],
    })



# NOTE: validate_property_address (former substep 0.5) was consolidated into
# STEP_01 substep 1.3 — see output/tools/review_property_listing.py. It still
# writes the same state['address_validation'] shape for downstream consumers.

@tool
def fetch_vod_data(
    tool_call_id: Annotated[str, InjectedToolCallId],
    state: Annotated[dict, InjectedState],
) -> Command:
    """Fetch Verification of Deposit (VOD) entries directly from the Encompass v3 API.

    Calls GET /encompass/v3/loans/{loanId}/applications/{applicationId}/vods and
    normalises the response into a flat list of account rows.

    Stores:
        state['vod_data']  — list of normalised account row dicts, each with:
            {
              vod_id, vod_index, institution_name, borrower_type,
              account_type, account_holder, account_number, balance
            }

    Run this after fetch_los_fields so that loan_id is available in state.
    Used by downstream tools (e.g. review_urla_assets) to cross-check extracted
    bank-statement / asset doc fields against the LOS-entered VOD amounts.
    """
    from shared.encompass_io import read_vods

    loan_id = state.get("loan_id")
    if not loan_id:
        msg = {"error": "No loan_id in state. Run find_loan first."}
        return Command(update={
            "messages": [ToolMessage(content=json.dumps(msg), tool_call_id=tool_call_id)],
        })

    vod_not_created = False
    try:
        rows = read_vods(loan_id, state=state)
    except LookupError as e:
        # "collection does not exist" — no VOD form rows in Encompass yet
        logger.warning(f"[FETCH_VOD] VOD collection missing: {e}")
        rows = []
        vod_not_created = True
    except Exception as e:
        logger.error(f"[FETCH_VOD] Failed to read VODs: {e}")
        msg = {"error": f"VOD API call failed: {e}"}
        return Command(update={
            "messages": [ToolMessage(content=json.dumps(msg), tool_call_id=tool_call_id)],
        })

    summary = {
        "vod_rows":        len(rows),
        "institutions":    list({r["institution_name"] for r in rows if r["institution_name"]}),
        "total_balance":   round(sum(r["balance"] for r in rows), 2),
        "account_types":   list({r["account_type"] for r in rows}),
        "vod_not_created": vod_not_created,
    }
    logger.info(f"[FETCH_VOD] {summary}")

    update: dict = {
        "vod_data": rows,
        "messages": [ToolMessage(
            content=json.dumps({"status": "ok", **summary}),
            tool_call_id=tool_call_id,
        )],
    }

    if vod_not_created:
        from datetime import datetime, timezone
        update["flags"] = [{
            "substep": "0.7",
            "title": "VOD Not Created in Encompass",
            "severity": "warning",
            "details": (
                "The Encompass VOD form has no rows yet — "
                "GET /applications/{id}/vods returned 'collection does not exist'. "
                "Asset balances cannot be cross-referenced until VOD entries are added."
            ),
            "suggestion": "Open the VOD form in Encompass and add entries for each depository account.",
            "resolved": False,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }]

    return Command(update=update)


@tool
def extract_almas_images(
    tool_call_id: Annotated[str, InjectedToolCallId],
    state: Annotated[dict, InjectedState],
) -> Command:
    """Substep 0.6 — OCR images attached to Almas' notes via Claude vision.

    The frontend uploads any images that came with Almas' notes to DocRepo and
    passes them in ``additional_info.almas_notes_images`` (a list of dicts with a
    ``url`` plus optional DocRepo coordinate fields: client_id, doc_id, bucket).
    These images are NOT in the Encompass eFolder, so the CatchingDoc/LandingAI
    pipeline cannot reach them — Claude vision transcribes them here instead.

    Writes the enriched list (each item gains ``extracted_text`` + ``ocr_status``)
    to ``state['almas_notes_images']`` for downstream use by:
      - draft_cover_letter (7.1) — appends the text to CX.KM.SUBMISSION.NOTES and
        attaches each image's DocRepo coordinate as a flag reference document.
      - review_file_contacts (1.2) — cross-checks agent/broker details.

    This is a no-op when no images are provided — safe to call on every run.
    """
    images_in = (
        (state.get("additional_info") or {}).get("almas_notes_images")
        or state.get("almas_notes_images")
        or []
    )

    if not isinstance(images_in, list) or not images_in:
        result = {
            "success": True,
            "substep": "0.6",
            "tool": "extract_almas_images",
            "images": 0,
            "message": "No Almas-notes images provided — nothing to OCR.",
        }
        logger.info("[ALMAS_IMAGES] No images in additional_info.almas_notes_images — skipping.")
        return Command(update={
            "messages": [ToolMessage(content=json.dumps(result), tool_call_id=tool_call_id)],
        })

    from shared.llm_call import llm_vision_call

    _prompt = (
        "Transcribe the raw text content of this document faithfully as clean, "
        "readable plain text, preserving field labels and their values (one per line). "
        "Do not invent or infer values that are not visible. If the document contains "
        "contacts (buyer, seller, brokerage, agents, phones, emails, license numbers), "
        "list them under a 'KEY CONTACTS' heading at the end."
    )

    enriched: list[dict] = []
    ocr_ok = 0
    ocr_failed = 0

    for idx, item in enumerate(images_in):
        if isinstance(item, str):
            item = {"url": item}
        elif isinstance(item, dict):
            item = dict(item)
        else:
            logger.warning(f"[ALMAS_IMAGES] Skipping image[{idx}] — unrecognized type {type(item)}")
            continue

        url = item.get("url") or item.get("signed_url") or item.get("s3_url")
        if not url:
            item["extracted_text"] = ""
            item["ocr_status"] = "no_url"
            enriched.append(item)
            ocr_failed += 1
            logger.warning(f"[ALMAS_IMAGES] image[{idx}] has no url — cannot OCR.")
            continue

        res = llm_vision_call(prompt=_prompt, images=[{"url": url}], max_tokens=2048)
        if res.success:
            item["extracted_text"] = res.text
            item["ocr_status"] = "ok"
            item["ocr_model"] = res.model
            ocr_ok += 1
            logger.info(
                f"[ALMAS_IMAGES] image[{idx}] OCR ok — {len(res.text)} chars "
                f"(in={res.input_tokens} out={res.output_tokens})"
            )
        else:
            item["extracted_text"] = ""
            item["ocr_status"] = "error"
            item["ocr_error"] = res.error
            ocr_failed += 1
            logger.error(f"[ALMAS_IMAGES] image[{idx}] OCR failed: {res.error}")

        enriched.append(item)

    result = {
        "success": True,
        "substep": "0.6",
        "tool": "extract_almas_images",
        "images": len(enriched),
        "ocr_ok": ocr_ok,
        "ocr_failed": ocr_failed,
        "message": (
            f"OCR'd {ocr_ok}/{len(enriched)} Almas-notes image(s) via Claude vision"
            + (f"; {ocr_failed} failed" if ocr_failed else "")
            + "."
        ),
    }

    logger.info(f"[ALMAS_IMAGES] {result['message']}")

    update: dict = {
        "almas_notes_images": enriched,
        "messages": [ToolMessage(content=json.dumps(result), tool_call_id=tool_call_id)],
    }

    if ocr_ok or ocr_failed:
        update["flags"] = [{
            "substep": "0.6",
            "title": "Almas-Notes Images Transcribed",
            "severity": "info",
            "details": (
                f"Ran Claude vision OCR on {len(enriched)} image(s) attached to Almas' "
                f"notes: {ocr_ok} succeeded, {ocr_failed} failed. Transcribed text is "
                f"available to the Cover Letter (7.1) and File Contacts (1.2)."
            ),
            "suggestion": (
                "Review the transcribed text appended to the cover letter / submission notes."
                if ocr_ok else
                "OCR failed for the provided image(s); verify the DocRepo URL is reachable."
            ),
            "resolved": ocr_failed == 0,
            "timestamp": datetime.now().isoformat(),
        }]

    return Command(update=update)
