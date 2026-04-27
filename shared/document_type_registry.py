"""Document Type Registry - Centralized document schema management.

This module provides:
1. Document type registry mapping document names to schema IDs
2. Schema loading from /tools/schema/*.json files
3. Field-to-document mapping for extraction
4. Variation tracking when multiple documents provide conflicting values

Usage:
    from shared.document_type_registry import (
        get_document_schema,
        get_schema_for_document,
        DOCUMENT_TYPE_REGISTRY,
    )
    
    # Get schema for a document type
    schema = get_document_schema("Appraisal Report")
    
    # Extract fields from document with variation tracking
    result = extract_with_variations(document_bytes, doc_type, existing_fields)
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger(__name__)

# Path to schema files
SCHEMA_DIR = Path(__file__).parent.parent / "schema"

# Cache for loaded schemas
_schema_cache: Dict[int, Dict] = {}

# =============================================================================
# DOCUMENT TYPE REGISTRY
# =============================================================================
# Maps our standardized document names to schema file IDs
# Each entry: document_name -> { schema_id, aliases, fields_provided, encompass_bucket }

DOCUMENT_TYPE_REGISTRY: Dict[str, Dict[str, Any]] = {
    # === ID Documents ===
    "Driver's License": {
        "schema_id": None,  # Uses LandingAI direct extraction
        "aliases": ["drivers license", "dl", "id card", "state id", "identification"],
        "encompass_buckets": [
            "ID Customer Identification Documentation",
            "ID Customer Identification",
        ],
        "fields_provided": [
            "borrower_first_name", "borrower_last_name", "borrower_dob", 
            "borrower_sex", "borrower_address",
            "id_expiration_date", "id_issue_date"  # Renamed from generic expiration_date/issue_date
        ],
        "extraction_method": "landingai",
    },
    "Passport": {
        "schema_id": None,
        "aliases": ["passport"],
        "encompass_buckets": ["ID Customer Identification Documentation", "Passport"],
        "fields_provided": [
            "borrower_first_name", "borrower_last_name", "borrower_dob",
            "id_expiration_date", "id_issue_date"  # Renamed from generic expiration_date/issue_date
        ],
        "extraction_method": "landingai",
    },
    "Permanent Resident Card": {
        "schema_id": None,
        "aliases": ["permanent resident", "perm res", "green card", "resident card", "i-551"],
        "encompass_buckets": ["ID Customer Identification Documentation", "Permanent Resident Card", "Green Card"],
        "fields_provided": [
            "borrower_first_name", "borrower_last_name", "borrower_dob",
            "id_expiration_date", "id_issue_date"  # Renamed from generic expiration_date/issue_date
        ],
        "extraction_method": "landingai",
    },
    
    # === Appraisal ===
    "Appraisal Report": {
        "schema_id": 162,  # From tools/schema/162.json
        "aliases": ["appraisal", "property appraisal"],
        "encompass_buckets": ["Appraisal", "Appraisal Report"],
        "fields_provided": [
            "property_address", "property_county", "property_state", 
            "appraised_value", "property_type", "property_year_built",
            "parcel_number", "appraisal_cost_estimate", "appraisal_pud",
            "is_condominium", "is_cooperative", "is_manufactured_home",
            "flood_zone", "nfip_map_number", "neighborhood_name",
            "appraiser_name", "appraiser_company_name", "appraiser_address",
            "appraiser_city", "appraiser_state", "appraiser_zip",
            "appraiser_phone", "appraiser_email",
            "appraisal_made_as_is", "appraisal_subject_to_completion",
            "appraisal_subject_to_repairs", "appraisal_subject_to_conditions"
        ],
        "extraction_method": "schema",
    },
    "Appraisal Invoice": {
        "schema_id": None,  # Uses tool_schema/appraisal_invoice.json
        "aliases": ["appraisal fee", "appraisal payment", "amc invoice"],
        "encompass_buckets": ["Invoices", "Appraisal Invoice", "AMC Invoice", "Appraisal Fee"],
        "fields_provided": [
            "appraisal_fee", "amc_company_name", "reinspection_fee",
            "invoice_date", "total_invoice_amount", "invoice_number",
            "payment_received", "balance_due"
        ],
        "extraction_method": "landingai",
    },
    "Appraisal Acknowledgement": {
        "schema_id": None,  # Uses tool_schema/appraisal_acknowledgement.json
        "aliases": ["appraisal acknowledgment", "receipt of appraisal", "appraisal receipt", "appraisal delivery email", "proof of delivery"],
        "encompass_buckets": ["Appraisal", "Appraisal Acknowledgement", "Appraisal Delivery", "Appraisal Acknowledgment Receipt"],
        "fields_provided": [
            "appraisal_delivery_date", "sender_name", "sender_email", 
            "recipient_names", "recipient_emails", "subject", "has_appraisal_attachment"
        ],
        "extraction_method": "landingai",
    },
    "Notice of Right to Appraisal": {
        "schema_id": None,  # Uses tool_schema/notice_of_right_appraisal.json
        "aliases": ["notice of right", "right to appraisal", "appraisal valuation notice", "right to receive appraisal", "notice of right to receive copy"],
        "encompass_buckets": ["Notice of Right to Receive Copy of Written Appraisal/Valuation", "Notice of Right"],
        "fields_provided": [
            "waiver_3day_waiting", "receive_copy_requested", 
            "borrower_signature_date", "coborrower_signature_date"
        ],
        "extraction_method": "landingai",
    },
    
    # === Credit ===
    "Credit Report": {
        "schema_id": None,  # Uses LandingAI for complex credit extraction
        "aliases": ["credit", "tri-merge", "credit report"],
        "encompass_buckets": ["Credit Report", "Credit"],
        "fields_provided": [
            "borrower_first_name", "borrower_last_name", "borrower_ssn",
            "borrower_dob", "coborrower_first_name", "coborrower_last_name",
            "coborrower_ssn", "borrower_aka", "coborrower_aka",
            "borrower_credit_score_factors", "employer_name",
            "credit_report_order_date",  # from Xactus header "Ordered:" field
            "credit_report_fee",  # fee/charge amount if shown on report cover
        ],
        "extraction_method": "landingai",
    },
    
    # === Fraud ===
    "Fraud Report": {
        "schema_id": None,  # Uses LandingAI with tool_schema/fraud_report.json
        "aliases": ["fraud", "fraud guard", "fraudguard", "loansafe", "identity verification"],
        "encompass_buckets": ["Fraud", "Fraud Report", "Fraud Guard", "FraudGuard", "Identity Fraud", "LoanSafe Fraud"],
        "fields_provided": [
            "fraud_aka", "fraud_coborrower_aka", "coborrower_fraud_aka",
            "borrower_name", "borrower_ssn",
            "coborrower_name", "coborrower_ssn",
            "fraud_alert_status", "fraud_score", "address_history"
        ],
        "extraction_method": "landingai",
        "attachment_keywords": ["fraud", "report", "aka", "also known as"],
    },
    
    # === Title & Property ===
    "Title Report": {
        "schema_id": "title_report",  # Uses tool_schema/title_report.json
        "aliases": ["prelim", "preliminary title", "title commitment"],
        "encompass_buckets": ["Title Report", "Prelim", "Preliminary Title"],
        "fields_provided": [
            "issuing_agent",
            "issuing_office",
            "property_address",
            "commitment_number",
            "lender_name",
            "contact",
            "title_company",
            "settlement_agent",
            "escrow_company",
            "bank_wire",
            "legal_description",
            "project_name",
            "effective_date",  # Used for EXPIRATION DATE calculation (+60 days)
            "vestingOwnership",  # Current ownership/vesting from Schedule A (for NBS check)
            "vestingType",  # Categorized: sole_and_separate, joint_tenants, community_property, etc.
            "policyFormType",  # T-2 vs Short Form T-2 for Texas
            "parcel_number", "apn",
        ],
        "extraction_method": "landingai",
    },
    
    # === Purchase Documents ===
    "Purchase Agreement": {
        "schema_id": None,  # Complex contract
        "aliases": ["purchase contract", "sales contract", "contract"],
        "encompass_buckets": ["Purchase Agreement", "Purchase Contract"],
        "fields_provided": [
            "borrower_first_name", "borrower_last_name", "purchase_price",
            "closing_date", "seller_credits", "cd_seller_names",
            "buyer_agent_company", "seller_agent_company", "seller_signature_name"
        ],
        "extraction_method": "landingai",
    },
    "Purchase Agreement Addendum": {
        "schema_id": None,
        "aliases": ["agreement addendum", "contract addendum"],
        "encompass_buckets": ["Purchase Agreement Addendums"],
        "fields_provided": ["seller_credits"],
        "extraction_method": "landingai",
    },
    
    # === Settlement Documents ===
    "ESS": {
        "schema_id": "ess",  # Uses tool_schema/ess.json
        "aliases": ["estimated settlement statement", "settlement statement", "preliminary settlement", "closing statement"],
        "encompass_buckets": ["ESS", "Estimated Settlement Statement", "Settlement Statement", "Preliminary Settlement"],
        "fields_provided": [
            "total_settlement_charges", "transfer_tax_name", "transfer_tax_amount", "transfer_tax_side",
            "fees", "notary_fee", "notary_fee_side", "hoa_fees", "hoa_fees_side",
            "home_warranty_amount", "home_warranty_side", "recording_fee", "recording_fee_side",
            "endorsement_fee", "endorsement_fee_side", "advance_fee", "advance_fee_side",
            "feb_dues", "feb_dues_side", "reimburse_fee", "reimburse_fee_side",
            "ess_balance_due", "balance_due_side", "broker_fee", "broker_fee_side"
        ],
        "extraction_method": "landingai",
    },
    
    # === Change of Circumstance (COC) ===
    "Change of circumstance (COC)": {
        "schema_id": None,
        "aliases": ["coc", "coc form", "change of circumstance", "changed circumstance"],
        "encompass_buckets": ["Change of circumstance (COC)", "Change of Circumstance", "COC", "COC Form", "Changed Circumstance"],
        "fields_provided": [
            "coc_reason", "coc_date", "coc_disclosed_by",
            "coc_original_values", "coc_revised_values"
        ],
        "extraction_method": "landingai",
    },
    
    # === SSPL (Pre-Application Worksheet) ===
    "2015 Settlement Service Provider List (SSPL)": {
        "schema_id": None,
        "aliases": ["sspl", "pre-application worksheet", "paw", "seller servicer pre-application", "settlement service provider list"],
        "encompass_buckets": ["2015 Settlement Service Provider List (SSPL)", "2015 Settlement Service Provider List", "SSPL", "Pre-Application Worksheet", "PAW", "Seller Servicer Pre-Application"],
        "fields_provided": [
            "sspl_loan_number", "sspl_borrower_name", "sspl_property_address",
            "sspl_loan_amount", "sspl_loan_type", "sspl_loan_purpose",
            "sspl_submission_date"
        ],
        "extraction_method": "landingai",
    },
    
    # === Closing & Disclosure ===
    "Closing Disclosure": {
        "schema_id": "closing_disclosure",  # Uses tool_schema/closing_disclosure.json
        "aliases": ["cd", "final cd"],
        "encompass_buckets": ["Closing Disclosure", "CD"],
        "fields_provided": [
            "loan_amount", "interest_rate", "apr", "monthly_pi",
            "closing_date", "note_date", "loan_number",
            "borrower_first_name", "borrower_last_name",
            "co_borrower_first_name", "co_borrower_last_name",
            "seller_credits", "loan_origination_fee",
            "appraisal_fee", "credit_report_fee", "processing_fee",
            "underwriting_fee",
            "attorney_fee", "title_settlement_fee",
            "cd_mic_reference", "cd_seller_names", "property_address",
            "settlement_agent_name", "escrow_number",
            "date_issued", "cd_received_date",
            # Section totals (Page 2)
            "origination_charges",
            "services_borrower_did_not_shop_for",
            "services_borrower_did_shop_for",
            "section_b_line_items",
            "section_c_line_items",
            "total_closing_costs",
            "lender_credits", "discount_points",
            "recording_fees", "government_recording_charges", "transfer_taxes",
            "initial_escrow_payment", "prepaid_interest",
            "homeowners_insurance_premium", "estimated_monthly_escrow",
            "property_taxes_prepaid",
            "mortgage_insurance_premium",
            # Section C individual line items (Title Charges → 2015 Itemization 1100)
            "closing_protection_letter_fee", "e_recording_fee",
            "endorsement_fee",
            "endorsement_fee_seller",
            "owners_policy_endorsement_fee", "lenders_policy_endorsement_fee",
            "owners_policy_endorsement_fee_seller", "lenders_policy_endorsement_fee_seller",
            "escrow_fee", "escrow_fee_seller",
            "lenders_title_insurance",
            "owners_title_insurance",
            "loan_tie_in_fee",
            "notary_fee", "notary_fee_seller",
            "overnight_courier_fee", "overnight_courier_fee_seller",
            "reconveyance_fee_seller",
            "wire_transfer_fee", "wire_transfer_fee_seller",
            "appraisal_reinspection_fee",
            # Page 2 — per-line payees (Closing Disclosure schema; CatchingDoc)
            "appraisal_fee_payee", "appraisal_reinspection_fee_payee",
            "credit_report_fee_payee", "tax_service_fee", "tax_service_fee_payee",
            "flood_cert_fee", "flood_cert_fee_payee",
            "loan_origination_fee_payee", "processing_fee_payee", "underwriting_fee_payee",
            "title_settlement_fee_payee", "closing_protection_letter_fee_payee",
            "endorsement_fee_payee", "escrow_fee_payee", "lenders_title_insurance_payee",
            "notary_fee_payee", "loan_tie_in_fee_payee", "wire_transfer_fee_payee",
            "overnight_courier_fee_payee", "e_recording_fee_payee", "attorney_fee_payee",
        ],
        "extraction_method": "landingai",
    },
    "Initial CD": {
        "schema_id": None,
        "aliases": ["initial closing disclosure"],
        "encompass_buckets": ["Closing Disclosure", "Initial CD"],
        "fields_provided": [
            "loan_amount", "interest_rate", "apr", "monthly_pi",
            "appraisal_fee", "credit_report_fee", "processing_fee"
        ],
        "extraction_method": "landingai",
    },
    "COC CD": {
        "schema_id": None,
        "aliases": ["change of circumstance", "revised cd"],
        "encompass_buckets": ["Change of Circumstance", "COC CD"],
        "fields_provided": ["loan_amount", "interest_rate", "apr"],
        "extraction_method": "landingai",
    },
    # NOTE: "LE" was a duplicate of "Loan Estimate" (identical aliases/buckets,
    # subset of fields). Consolidated into single entry to prevent double-extraction
    # and flat dict overwrites.
    "Loan Estimate": {
        "schema_id": None,  # Uses tool_schema/loan_estimate.json
        "aliases": ["loan estimate", "le", "good faith estimate", "initial le"],
        "encompass_buckets": ["Loan Estimate", "LE", "Initial LE"],
        "fields_provided": [
            "loan_amount", "interest_rate", "purchase_price", "monthly_pi",
            "discount_points", "loan_term",
            "loan_officer", "estimated_total_closing_costs",
            "nmls_loan_originator_id", "appraisal_fee", "credit_report_fee",
            "disclosed_apr", "rate_lock_date", "lock_expiration_date",
            "impounds_taxes", "impounds_insurance", "origination_charges", "lender_credits",
            "title_company", "settlement_agent",
            "date_issued", "loan_purpose", "loan_type", "loan_product"
        ],
        "extraction_method": "landingai",
    },
    
    # === Insurance ===
    "Evidence of Insurance": {
        "schema_id": None,
        "aliases": ["hoi", "homeowners insurance", "hazard insurance"],
        "encompass_buckets": ["Evidence of Hazard Insurance", "HOI"],
        "fields_provided": [
            # Carrier (underwriter) fields — separated from agency after schema fix
            "insurance_carrier_name", "insurance_carrier_address",
            # Nested insurance_company object fields (legacy)
            "insurance_company", "insurance_company.companyName", "insurance_company.address",
            "insurance_company.city", "insurance_company.state", "insurance_company.postalCode",
            "insurance_company.phone", "insurance_company.fax",
            # Nested insurance_agent object fields (legacy)
            "insurance_agent", "insurance_agent.agentName", "insurance_agent.agencyName",
            "insurance_agent.address", "insurance_agent.city", "insurance_agent.state",
            "insurance_agent.postalCode", "insurance_agent.phone", "insurance_agent.email",
            "insurance_agent.fax", "insurance_agent.licenseNumber",
            # Flat hazard_* fields from extraction config
            "hazard_insurance_insured_name", "insured_mailing_address",
            "policy_number", "hazard_insurance_effective_date", "hazard_insurance_expiration_date",
            "hazard_insurance_insured_location", "hazard_insurance_property_type",
            "hazard_insurance_deductible", "wind_hail_deductible",
            "hazard_insurance_premium", "hazard_annual_premium_hud42",
            "hazard_insurance_coverage", "hazard_face_amount",
            "other_structures_coverage", "hazard_insurance_personal_property",
            "loss_of_use_coverage", "hazard_insurance_personal_liability", "replacement_cost",
            "hazard_insurance_mortgagee_name", "hazard_insurance_mortgagee_address",
            "hazard_insurance_mortgagee_loan_number",
            "hazard_insurance_company", "hazard_insurance_address", "hazard_insurance_city",
            "hazard_insurance_state", "hazard_insurance_zip", "hazard_insurance_phone",
            "hazard_insurance_fax", "hazard_insurance_contact", "agent_email",
            "hazard_insurance_agent_email",
            "hazard_insurance_is_condo", "hazard_insurance_policy_form",
            # Payment/lender service center address fields
            "payment_address_name", "payment_address_street",
            "payment_address_city", "payment_address_state", "payment_address_zip"
        ],
        "extraction_method": "landingai",
    },
    "Flood Certificate": {
        "schema_id": "flood_certificate",  # Uses tool_schema/flood_certificate.json
        "aliases": ["flood cert", "flood determination", "sfhdf", "flood hazard determination"],
        "encompass_buckets": ["Flood Certificate", "Flood Certification", "Flood Determination"],
        "fields_provided": [
            "flood_zone",
            "in_sfha",
            "community_number",
            "map_panel",
            "map_date",
            "order_number",
            "property_address",
            "determination_date",
            "flood_determination_number",
            "flood_cert_fee",
        ],
        "extraction_method": "landingai",
    },
    "Flood Insurance": {
        "schema_id": None,
        "aliases": ["flood policy", "nfip", "flood insurance policy"],
        "encompass_buckets": ["Flood Insurance", "Flood Insurance Policy"],
        "fields_provided": [
            "floodInsurance",
            "flood_insurance_company",
            "flood_insurance_contact",
            "flood_insurance_phone",
            "flood_insurance_address",
            "flood_insurance_city",
            "flood_insurance_state",
            "flood_insurance_zip",
            "flood_policy_number",
            "flood_zone",
            "flood_coverage_amount",
            "flood_premium",
            "flood_effective_date",
            "flood_expiration_date",
            "nfip_map_number",
            "flood_determination_date"
        ],
        "extraction_method": "landingai",
        "attachment_selection": "latest",
    },
    
    # === Tax Documents ===
    "Tax Summary": {
        "schema_id": "tax_summary",  # Uses tool_schema/tax_summary.json
        "aliases": ["property tax", "tax certificate"],
        "encompass_buckets": ["Tax Summary", "Property Tax"],
        "fields_provided": ["annual_taxes", "tax_parcel_number", "property_address", "county_name", "monthly_taxes"],
        "extraction_method": "landingai",
    },
    "Tax Returns - Business": {
        "schema_id": "tax_returns_business",
        "aliases": ["business tax return", "corporate tax return", "1120", "1065", "schedule k-1"],
        "encompass_buckets": ["Tax Returns - Business", "Tax Return - Business", "Business Tax Returns"],
        "fields_provided": ["business_name"],
        "extraction_method": "landingai",
    },
    
    # === FHA Documents ===
    "FHA Transmittal Summary": {
        "schema_id": "fha_transmittal",  # Uses tool_schema/fha_transmittal.json
        "aliases": ["hud-92900lt", "fha loan transmittal", "92900", "fha transmittal"],
        "encompass_buckets": [
            "HUD-92900-LT FHA Loan Transmittal",
            "FHA Loan Transmittal Summary",
            "HUD-92900-LT",
        ],
        "fields_provided": [
            "agency_case_number", "borrower_name", "borrower_first_name", "borrower_last_name",
            "property_address", "property_type", "number_of_units", "sales_price", "appraised_value",
            "loan_amount", "interest_rate", "first_mortgage_pi", "monthly_mip", "hoa_fee",
            "lease_ground_rent", "second_mortgage_pi", "hazard_insurance", "supplemental_insurance",
            "taxes_special_assessments", "total_mortgage_payment", "proposed_hazard_insurance",
            "proposed_mortgage_insurance", "proposed_taxes", "upfront_mip"
        ],
        "extraction_method": "landingai",
    },
    "FHA Case Assignment": {
        "schema_id": None,
        "aliases": ["fha case", "case assignment", "fha case number"],
        "encompass_buckets": [
            "FHA Government Documents",
            "FHA Government Document",
            "FHA Case Assignment",
        ],
        "fields_provided": ["agency_case_number", "case_assigned_date"],
        "extraction_method": "landingai",
    },
    
    # === Conventional Transmittal ===
    "Transmittal Summary": {
        "schema_id": None,
        "aliases": ["1008", "fnma transmittal", "freddie transmittal", "uniform underwriting transmittal"],
        "encompass_buckets": [
            "Transmittal Summary",
            "1008",
            "1008 Transmittal Summary",
            "1008 - Transmittal Summary",
            "Transmittal Summary (1008)",
            "FNMA Transmittal",
            "FNMA 1008",
            "Uniform Underwriting and Transmittal Summary",
        ],
        "fields_provided": [
            # Borrower & Property
            "borrower_first_name", "borrower_last_name",
            "property_address", "property_type", "number_of_units",
            # Loan Terms
            "sales_price", "appraised_value", "loan_amount", "interest_rate",
            # Monthly Payment Breakdown (mirrors FHA Transmittal fields)
            "first_mortgage_pi", "hoa_fee", "lease_ground_rent",
            "second_mortgage_pi", "total_mortgage_payment",
            # Proposed Monthly Amounts (verified in Steps 2, 7, 8)
            "proposed_hazard_insurance", "proposed_mortgage_insurance",
            "proposed_taxes", "hazard_insurance",
            "taxes_special_assessments", "supplemental_insurance",
        ],
        "extraction_method": "landingai",
    },
    
    # === VA Documents ===
    # Encompass often stores VA PDFs under form-specific bucket titles (26-xxxx), not the
    # generic "VA Loan Summary" name used in required_docs_conditions.json.
    "VA Loan Summary": {
        "schema_id": None,
        "aliases": [
            "va lapp",
            "va sar summary",
            "va loan analysis",
            "26-0286",
            "26-6393",
            "va 26-0286",
            "va 26-6393",
        ],
        "encompass_buckets": [
            "VA Loan Summary",
            "VA Certificate",
            "VA 26-0286 Loan Summary",
            "VA 26-6393 Loan Analysis",
        ],
        "fields_provided": [
            # Borrower & Property
            "borrower_first_name", "borrower_last_name",
            "property_address", "property_type", "number_of_units",
            # Loan Terms
            "sales_price", "appraised_value", "loan_amount", "interest_rate",
            # Monthly Payment Breakdown (mirrors FHA Transmittal fields)
            "first_mortgage_pi", "hoa_fee", "lease_ground_rent",
            "second_mortgage_pi", "total_mortgage_payment",
            # Proposed Monthly Amounts (verified in Steps 2, 7, 8)
            "proposed_hazard_insurance", "proposed_mortgage_insurance",
            "proposed_taxes", "hazard_insurance",
            "taxes_special_assessments", "supplemental_insurance",
        ],
        "extraction_method": "landingai",
    },
    "VA Certificate of Eligibility": {
        "schema_id": None,
        "aliases": ["coe", "va coe", "certificate of eligibility", "va certificate"],
        "encompass_buckets": [
            "VA Certificate of Eligibility",
            "Certificate of Eligibility",
            "VA Documents Misc",
        ],
        "fields_provided": [
            "veteran_name",
            "va_case_number",
            "entitlement_code",
            "basic_entitlement",
            "additional_entitlement",
            "funding_fee_exempt",
            "service_connected_disability",
            "branch_of_service",
            "dates_of_service",
        ],
        "extraction_method": "landingai",
    },
    "VA Documents Misc": {
        "schema_id": None,
        "aliases": ["va misc", "va documents misc"],
        "encompass_buckets": [
            "VA Documents Misc",
        ],
        "fields_provided": [
            "veteran_name",
            "va_case_number",
            "entitlement_code",
            "basic_entitlement",
            "additional_entitlement",
            "funding_fee_exempt",
            "service_connected_disability",
            "branch_of_service",
            "dates_of_service",
            "document_description",
        ],
        "extraction_method": "landingai",
    },
    "VA Funding Fee Worksheet": {
        "schema_id": None,
        "aliases": ["funding fee", "va funding fee", "ff worksheet"],
        "encompass_buckets": [
            "VA Funding Fee Worksheet",
            "VA Funding Fee",
        ],
        "fields_provided": [],
        "extraction_method": "landingai",
    },
    "VA Nearest Living Relative": {
        "schema_id": None,
        "aliases": ["nearest living relative", "nearest relative", "va relative"],
        "encompass_buckets": [
            "VA Nearest Living Relative",
        ],
        "fields_provided": [],
        "extraction_method": "landingai",
    },
    "VA 26-1820": {
        "schema_id": None,
        "aliases": ["26-1820", "loan guaranty determination", "va 1820"],
        "encompass_buckets": [
            "VA 26-1820",
            "VA Form 26-1820",
        ],
        "fields_provided": [],
        "extraction_method": "landingai",
    },
    
    # === Loan Application ===
    "Initial 1003": {
        "schema_id": None,  # Complex loan application
        "aliases": ["1003", "urla", "loan application"],
        "encompass_buckets": ["1003", "URLA", "Loan Application"],
        "fields_provided": [
            "borrower_first_name", "borrower_last_name", "borrower_ssn",
            "borrower_dob", "borrower_marital_status", "borrower_sex",
            "coborrower_first_name", "coborrower_last_name",
            "coborrower_marital_status", "coborrower_sex", "property_address"
        ],
        "extraction_method": "landingai",
    },
    
    # === SSN Documents ===
    "SSN Card": {
        "schema_id": None,
        "aliases": ["social security card"],
        "encompass_buckets": ["ID Customer Identification Documentation", "SSN Card"],
        "fields_provided": ["borrower_ssn", "borrower_first_name", "borrower_last_name"],
        "extraction_method": "landingai",
    },
    "SSN Verification": {
        "schema_id": None,
        "aliases": ["ssn verification report"],
        "encompass_buckets": ["SSN Verification"],
        "fields_provided": ["borrower_ssn", "borrower_first_name", "borrower_last_name"],
        "extraction_method": "landingai",
    },
    
    # === Other Documents ===
    "Underwriting (DU / LP)": {
        "schema_id": None,
        "aliases": ["underwriting", "du findings", "lp findings", "aus"],
        "encompass_buckets": ["Underwriting", "DU Findings", "LP Findings"],
        "fields_provided": [
            "borrower_first_name", "borrower_last_name", 
            "coborrower_first_name", "coborrower_last_name",
            "loan_amount", "ltv", "appraised_value"
        ],
        "extraction_method": "landingai",
    },
    "Trust Document": {
        "schema_id": None,
        "aliases": ["trust certificate", "trust agreement", "trust paperwork", "beneficiary confirmation"],
        "encompass_buckets": ["Trust Certificate", "Trust Agreement", "Trust Document", "Trust Paperwork/Beneficiary Confirmation"],
        "fields_provided": ["vesting_name"],
        "extraction_method": "landingai",
    },
    "Closing Protection Letter": {
        "schema_id": "closing_protection_letter",  # Uses tool_schema/closing_protection_letter.json
        "aliases": ["cpl", "lender protection letter"],
        "encompass_buckets": ["Closing Protection Letter (CPL)", "CPL", "Lender Protection Letter"],
        "fields_provided": ["issue_date", "lender_name", "settlement_agent_name", "cpl_title_underwriter"],
        "extraction_method": "landingai",
    },
    
    # === Approval Documents ===
    "Approval Form": {
        "schema_id": None,  # Uses LandingAI with tool_schema/approval_form.json
        "aliases": ["conditional approval", "loan approval", "approval letter", "commitment letter"],
        "encompass_buckets": ["Approval", "Conditional Approval", "Conditional Loan Approval", "Loan Approval", "Commitment Letter"],
        "fields_provided": [
            "borrower_information", "property_information", "loan_information",
            "prior_to_docs_conditions", "prior_to_funding_conditions"
        ],
        "extraction_method": "landingai",
        "attachment_selection": "latest",  # Always get the most recent approval
    },
    
    # === MI Certificate ===
    "MI Certificate": {
        "schema_id": None,  # Uses tool_schema/mi_certificate.json
        "aliases": ["mi certificate", "mortgage insurance", "pmi certificate", "pmi"],
        "encompass_buckets": ["MI Certificate", "Mortgage Insurance", "PMI Certificate"],
        "fields_provided": [
            "premium_type", "first_renewal_percent", "first_renewal_months",
            "second_renewal_percent", "second_renewal_months", "cancel_at_percent",
            "upfront_premium_amount", "monthly_premium_amount", "mi_company_name",
            "mi_company_address", "mi_file_number", "certificate_number"
        ],
        "extraction_method": "landingai",
    },
    
    # === Invoice Documents ===
    "Credit Report Invoice": {
        "schema_id": None,  # Uses tool_schema/credit_report_invoice.json
        "aliases": ["credit invoice", "tri-merge invoice", "credit fee"],
        "encompass_buckets": ["Credit Report Invoice", "Credit Invoice", "Tri-Merge Invoice"],
        "fields_provided": [
            "credit_report_fee", "vendor_name", "bundled_charges",
            "credit_invoice_date", "credit_invoice_number"
        ],
        "extraction_method": "landingai",
    },
    
    # === Payoff Documents ===
    "Payoff Statement": {
        "schema_id": None,  # Uses tool_schema/payoff_statement.json
        "aliases": ["payoff", "loan payoff", "debt payoff", "payoff letter"],
        "encompass_buckets": ["Payoff", "Payoff Statement", "Loan Payoff", "Debt Payoff"],
        "fields_provided": [
            "creditor_name", "account_number", "payoff_amount",
            "per_diem", "good_through_date", "payee_address"
        ],
        "extraction_method": "landingai",
        "attachment_selection": "all",
    },
    
    # === Inspection Documents ===
    # NOTE: Fields are prefixed with doc type to avoid collisions in the flat
    # extracted_fields dict. Normalizers in document_review.py rename generic
    # extraction keys (inspection_date → home_inspection_date, etc.).
    "Home Inspection Report": {
        "schema_id": None,  # Uses tool_schema/home_inspection_report.json
        "aliases": ["home inspection", "property inspection", "inspection report"],
        "encompass_buckets": ["Home Inspection", "Property Inspection", "Inspection Report"],
        "fields_provided": [
            "home_inspection_date", "home_inspector_name", "home_inspector_company",
            "property_address", "major_issues", "home_inspection_result"
        ],
        "extraction_method": "landingai",
    },
    "Pest Inspection Report": {
        "schema_id": None,  # Uses tool_schema/pest_inspection_report.json
        "aliases": ["pest inspection", "termite inspection", "wdo inspection", "termite report"],
        "encompass_buckets": ["Pest Inspection", "Termite Inspection", "WDO Inspection"],
        "fields_provided": [
            "pest_inspection_date", "pest_inspector_name", "pest_inspector_company",
            "pest_found", "pest_treatment_required", "pest_inspection_result"
        ],
        "extraction_method": "landingai",
    },
}


# =============================================================================
# SCHEMA LOADING
# =============================================================================

def load_schema(schema_id: int) -> Optional[Dict[str, Any]]:
    """Load a schema from the schema directory by ID.
    
    Args:
        schema_id: The numeric schema ID (e.g., 162 for Appraisal Report)
        
    Returns:
        The loaded schema dict, or None if not found
    """
    global _schema_cache
    
    if schema_id in _schema_cache:
        return _schema_cache[schema_id]
    
    schema_file = SCHEMA_DIR / f"{schema_id}.json"
    if not schema_file.exists():
        logger.warning(f"Schema file not found: {schema_file}")
        return None
    
    try:
        with open(schema_file) as f:
            schema = json.load(f)
            _schema_cache[schema_id] = schema
            return schema
    except Exception as e:
        logger.error(f"Error loading schema {schema_id}: {e}")
        return None


def get_document_schema(document_type: str) -> Optional[Dict[str, Any]]:
    """Get the extraction schema for a document type.
    
    Args:
        document_type: The document type name (e.g., "Appraisal Report")
        
    Returns:
        The content_schema from the schema file, or None if not found/not applicable
    """
    registry_entry = DOCUMENT_TYPE_REGISTRY.get(document_type)
    if not registry_entry:
        # Try to find by alias
        for doc_name, entry in DOCUMENT_TYPE_REGISTRY.items():
            if document_type.lower() in [a.lower() for a in entry.get("aliases", [])]:
                registry_entry = entry
                break
    
    if not registry_entry:
        return None
    
    schema_id = registry_entry.get("schema_id")
    if not schema_id:
        # Uses LandingAI extraction, not a schema file
        return None
    
    schema = load_schema(schema_id)
    if schema:
        return schema.get("content_schema")
    return None


def get_document_fields(document_type: str) -> List[str]:
    """Get the list of fields a document type can provide.
    
    Args:
        document_type: The document type name
        
    Returns:
        List of field keys this document can extract
    """
    registry_entry = DOCUMENT_TYPE_REGISTRY.get(document_type)
    if registry_entry:
        return registry_entry.get("fields_provided", [])
    return []


def get_extraction_method(document_type: str) -> str:
    """Get the extraction method for a document type.
    
    Returns:
        'schema' for schema-based extraction, 'landingai' for LandingAI
    """
    registry_entry = DOCUMENT_TYPE_REGISTRY.get(document_type)
    if registry_entry:
        return registry_entry.get("extraction_method", "landingai")
    return "landingai"


def get_encompass_buckets(document_type: str) -> List[str]:
    """Get the Encompass eFolder bucket names for a document type."""
    registry_entry = DOCUMENT_TYPE_REGISTRY.get(document_type)
    if registry_entry:
        return registry_entry.get("encompass_buckets", [])
    return []


def find_document_type(bucket_title: str) -> Optional[str]:
    """Find the document type for an Encompass bucket title.
    
    Args:
        bucket_title: The eFolder document title
        
    Returns:
        The matching document type name, or None
    """
    bucket_lower = bucket_title.lower()
    
    for doc_type, entry in DOCUMENT_TYPE_REGISTRY.items():
        # Check bucket matches
        for bucket in entry.get("encompass_buckets", []):
            if bucket.lower() in bucket_lower or bucket_lower in bucket.lower():
                return doc_type
        
        # Check aliases
        for alias in entry.get("aliases", []):
            if alias.lower() in bucket_lower:
                return doc_type
    
    return None


# =============================================================================
# EXTRACTED FIELD STRUCTURE
# =============================================================================
# Each extracted field follows this structure:
#
# extracted_fields = {
#     "borrower_first_name": {                          # ← Schema field name (matches loan_field_summary key)
#         "value": "JOHN",                              # Primary extracted value
#         "primary_document": "Driver's License",       # Source of primary value
#         "confidence": 0.95,                           # Extraction confidence (0-1)
#         "extraction_method": "LandingAI",             # How it was extracted
#         "other_documents": ["ID", "Credit Report"],   # Secondary documents with this field
#         "alts": [{                                    # Alternative/conflicting values
#             "value": "JON",
#             "source": "Credit Report",
#             "action": "review"                        # ignore | review | use
#         }],
#         "field_id": "4000",                           # Encompass ID for API ops (optional)
#         "loan_field_summary": "borrower_first_name"   # Reference to loan_field_summary key
#     },
#     "borrower_ssn": {...},
#     ...
# }

def merge_extracted_field(
    existing_fields: Dict[str, Dict],
    field_key: str,
    new_value: Any,
    source_document: str,
    confidence: float = 0.95,
    extraction_method: str = "LandingAI",
    field_id: Optional[str] = None,
    loan_field_summary_key: Optional[str] = None,
) -> Dict[str, Dict]:
    """Merge a newly extracted field with the full structure.
    
    Creates/updates fields with the following structure:
    {
        "value": "JOHN",                              # Primary value
        "primary_document": "Driver's License",       # Source document
        "confidence": 0.95,                           # Extraction confidence
        "extraction_method": "LandingAI",             # How it was extracted
        "other_documents": ["Credit Report"],         # Other docs with this field
        "alts": [{                                    # Conflicting values
            "value": "JON",
            "source": "Credit Report",
            "action": "review"
        }],
        "field_id": "4000",                           # Encompass field ID (optional)
        "loan_field_summary": "borrower_first_name"   # Key in loan_field_summary
    }
    
    Args:
        existing_fields: Current extracted fields dict
        field_key: The field key (e.g., "borrower_first_name")
        new_value: The newly extracted value
        source_document: Document that provided this value
        confidence: Extraction confidence score
        extraction_method: How the value was extracted (default: "LandingAI")
        field_id: Encompass field ID (optional, for API operations)
        loan_field_summary_key: Key in loan_field_summary (defaults to field_key)
        
    Returns:
        Updated extracted_fields dict
    """
    # Use field_key as loan_field_summary key if not specified
    lfs_key = loan_field_summary_key or field_key
    
    if field_key not in existing_fields:
        # First extraction - set as primary with full structure
        existing_fields[field_key] = {
            "value": new_value,
            "primary_document": source_document,
            "confidence": confidence,
            "extraction_method": extraction_method,
            "loan_field_summary": lfs_key,
        }
        # Only add field_id if provided
        if field_id:
            existing_fields[field_key]["field_id"] = field_id
    else:
        existing = existing_fields[field_key]
        existing_value = existing.get("value")
        old_source = existing.get("primary_document", "unknown")

        if _values_match(existing_value, new_value):
            # Same value — just track the additional source
            other_docs = existing.get("other_documents", [])
            if source_document not in other_docs and source_document != old_source:
                other_docs.append(source_document)
                existing["other_documents"] = other_docs
            if confidence > existing.get("confidence", 0):
                existing["confidence"] = confidence
        else:
            # Different value — latest wins: promote new value, demote old to alts
            alts = existing.get("alts", [])

            # Demote current primary to alts (if not already there)
            if existing_value is not None:
                already_in_alts = any(
                    a.get("value") == existing_value and a.get("source") == old_source
                    for a in alts
                )
                if not already_in_alts:
                    alts.append({
                        "value": existing_value,
                        "source": old_source,
                        "action": "review",
                    })

            # Track old primary as other_document
            other_docs = existing.get("other_documents", [])
            if old_source not in other_docs and old_source != source_document:
                other_docs.append(old_source)
            existing["other_documents"] = other_docs

            # Promote new value to primary
            existing["value"] = new_value
            existing["primary_document"] = source_document
            existing["confidence"] = confidence
            existing["extraction_method"] = extraction_method
            existing["alts"] = alts

            logger.info(
                f"[MERGE] Field '{field_key}': updated primary "
                f"'{existing_value}' ({old_source}) → '{new_value}' ({source_document})"
            )
    
    return existing_fields


def _values_match(val1: Any, val2: Any) -> bool:
    """Check if two extracted values match (with normalization)."""
    if val1 is None or val2 is None:
        return val1 == val2
    
    # Convert to strings for comparison
    str1 = str(val1).strip().lower()
    str2 = str(val2).strip().lower()
    
    # Normalize whitespace
    str1 = " ".join(str1.split())
    str2 = " ".join(str2.split())
    
    return str1 == str2


def get_fields_with_conflicts(extracted_fields: Dict[str, Dict]) -> List[str]:
    """Get list of field keys that have alternative values."""
    return [
        key for key, data in extracted_fields.items()
        if data.get("alts")
    ]


def get_all_document_types() -> List[str]:
    """Get list of all registered document types."""
    return list(DOCUMENT_TYPE_REGISTRY.keys())


def get_field_to_document_mapping() -> Dict[str, List[str]]:
    """Get mapping of field_key -> list of documents that can provide it."""
    mapping: Dict[str, List[str]] = {}
    
    for doc_type, entry in DOCUMENT_TYPE_REGISTRY.items():
        for field_key in entry.get("fields_provided", []):
            if field_key not in mapping:
                mapping[field_key] = []
            mapping[field_key].append(doc_type)
    
    return mapping


# =============================================================================
# SCHEMA DISCOVERY
# =============================================================================

def discover_schemas_for_document(document_name: str) -> List[Dict]:
    """Search schema files for schemas matching a document name.
    
    Useful for finding schema IDs for new document types.
    
    Args:
        document_name: Document name to search for (e.g., "closing disclosure")
        
    Returns:
        List of matching schemas with id, name, and file
    """
    matches = []
    search_term = document_name.lower()
    
    for schema_file in SCHEMA_DIR.glob("*.json"):
        try:
            with open(schema_file) as f:
                schema = json.load(f)
                schema_name = (schema.get("name") or "").lower()
                
                if search_term in schema_name:
                    matches.append({
                        "id": schema.get("id"),
                        "name": schema.get("name"),
                        "file": schema_file.name,
                    })
        except:
            pass
    
    return matches


# =============================================================================
# INITIALIZATION
# =============================================================================

def clear_schema_cache():
    """Clear the loaded schema cache."""
    global _schema_cache
    _schema_cache = {}


# =============================================================================
# TOOL_SCHEMA REPLACEMENT FUNCTIONS
# =============================================================================
# These functions replace the tool_schema module functionality

def get_schema_for_document(doc_type: str) -> Optional[Dict[str, Any]]:
    """Get schema information for a document type.
    
    This replaces tool_schema.get_schema_for_document().
    Returns a dict with bucket_names, attachment_keywords, attachment_avoid, fields_provided.
    
    Args:
        doc_type: Document type name (e.g., "Driver's License")
        
    Returns:
        Schema dict or None if not found
    """
    registry_entry = DOCUMENT_TYPE_REGISTRY.get(doc_type)
    if not registry_entry:
        # Try to find by alias
        for doc_name, entry in DOCUMENT_TYPE_REGISTRY.items():
            if doc_type.lower() in [a.lower() for a in entry.get("aliases", [])]:
                registry_entry = entry
                doc_type = doc_name
                break
    
    if not registry_entry:
        return None
    
    return {
        "document_type": doc_type,
        "bucket_names": registry_entry.get("encompass_buckets", []),
        "attachment_keywords": registry_entry.get("attachment_keywords", []),
        "attachment_avoid": registry_entry.get("attachment_avoid", []),
        "attachment_selection": registry_entry.get("attachment_selection", "best"),
        "fields_provided": registry_entry.get("fields_provided", []),
        "extraction_method": registry_entry.get("extraction_method", "landingai"),
    }


def get_attachment_rules(doc_type: str) -> Dict[str, List[str]]:
    """Get attachment matching rules for a document type.
    
    This replaces tool_schema.get_attachment_rules().
    
    Returns:
        Dict with 'keywords' and 'avoid' lists
    """
    registry_entry = DOCUMENT_TYPE_REGISTRY.get(doc_type)
    if not registry_entry:
        # Try to find by alias
        for doc_name, entry in DOCUMENT_TYPE_REGISTRY.items():
            if doc_type.lower() in [a.lower() for a in entry.get("aliases", [])]:
                registry_entry = entry
                break
    
    if not registry_entry:
        return {"keywords": [], "avoid": []}
    
    return {
        "keywords": registry_entry.get("attachment_keywords", []),
        "avoid": registry_entry.get("attachment_avoid", []),
    }


def get_fields_provided(doc_type: str) -> List[str]:
    """Get list of fields provided by a document type.
    
    This replaces tool_schema.get_fields_provided().
    
    Args:
        doc_type: Document type name
        
    Returns:
        List of field keys this document type can provide
    """
    return get_document_fields(doc_type)


def list_supported_document_types() -> List[str]:
    """List all supported document types.
    
    This replaces tool_schema.list_supported_document_types().
    
    Returns:
        List of document type names
    """
    return get_all_document_types()


def get_all_schemas() -> Dict[str, Dict[str, Any]]:
    """Get schema info for all document types.
    
    This replaces tool_schema.get_all_schemas().
    
    Returns:
        Dict mapping document type to schema info
    """
    schemas = {}
    for doc_type in DOCUMENT_TYPE_REGISTRY:
        schema = get_schema_for_document(doc_type)
        if schema:
            schemas[doc_type] = schema
    return schemas


logger.info(f"Document Type Registry loaded with {len(DOCUMENT_TYPE_REGISTRY)} document types")

