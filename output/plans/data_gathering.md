# Step 0 - [VERIFICATION] Data Gathering

**Phase**: VERIFICATION
**Auto-generated**: Yes — this step is derived from fields used by all other steps.
**Tools**: `find_loan`, `fetch_los_fields`, `fetch_doc_fields`, `build_loan_summary`

## Purpose

Gather all data needed by the workflow in one upfront step:
- Find the loan GUID from the loan number
- Batch-read 73 LOS fields from Encompass
- Extract fields from 20 document types
- Build the Loan Summary (URLA) — a categorized snapshot that never changes

**NOTE**: Each substep has its own dedicated tool. State is automatically injected.

## Available Tools

| Tool | Purpose |
|------|---------|
| `find_loan` | Search Encompass for loan GUID |
| `fetch_los_fields` | Fetch all needed LOS fields in one batch call |
| `fetch_doc_fields` | Extract fields from specific document types |
| `build_loan_summary` | Build categorized URLA-style loan summary from los_fields |

## Tool Calls

```python
# Substep 0.1 - Find Loan
find_loan(loan_number=loan_number, borrower_name=borrower_name)

# Substep 0.2 - Fetch LOS Fields
fetch_los_fields(loan_guid=loan_id)

# Substep 0.3 - Fetch Doc Fields
fetch_doc_fields(loan_guid=loan_id)

# Substep 0.4 - Build Loan Summary (URLA)
build_loan_summary()
```

---

## Substeps

### Substep 0.1 - Find Loan
**Tools**: `find_loan`

Call `find_loan()` — it automatically reads `loan_number` and `borrower_name` from state.
You do NOT need to pass any arguments. This stores the loan GUID in state for all subsequent tools.

### Substep 0.2 - Fetch LOS Fields (73 fields)
**Tools**: `fetch_los_fields`

Call `fetch_los_fields` to batch-read all Encompass field IDs.
Results are stored in `state["los_fields"]` organized by key.

### Substep 0.3 - Fetch Doc Fields (20 document types)
**Tools**: `fetch_doc_fields`

Call `fetch_doc_fields` to extract fields from documents in the eFolder.
Results are stored in `state["doc_fields"]` organized by key.

### Substep 0.4 - Build Loan Summary (URLA) + Loan Profile Detection
**Tools**: `build_loan_summary`

Call `build_loan_summary()` AFTER fetch_los_fields completes.
This builds a categorized snapshot from `state["los_fields"]` into `state["loan_summary"]`
and detects the **loan profile** stored in `state["loan_profile"]`.

The loan summary includes:
- **borrower**: name, SSN (masked), DOB, marital status
- **property**: address, city, state, zip, county
- **loan_terms**: type, purpose, amount, rate, LTV, appraised value
- **dates**: closing, lock expiration, appraisal received
- **vesting**: manner held, final vesting, occupancy
- **preflight**: CTC status, CD status, overage
- **closing**: conditions text, elective insurance
- **derived**: has_coborrower, is_note_llc, is_trust, loan_type, loan_purpose, LTV

**Loan Profile** (5 discriminators for rule modifiers):
- **loan_type**: Conventional, FHA, VA, USDA (from field 1172)
- **purpose**: Purchase, Refinance, CashOutRefi (from field 19)
- **state**: 2-letter property state code (from field 14)
- **trust**: boolean trust involvement (from CX.CLOSE.TRUST)
- **note_llc**: boolean Note Mortgage LLC origination (from LO/processor email)

The loan profile drives `rule_modifiers` on all subsequent substeps.

⚠️ Both loan_summary and loan_profile are IMMUTABLE — set once, never updated.

---

## LOS Fields Reference

| Field ID | Key | Name | Category |
|----------|-----|------|----------|
| `1041` | property_type | Property Type | property |
| `1065` | marital_status | Borrower Marital Status | borrower_info |
| `1068` | employment_start_date | Employment Start Date (Hire Date) | employment |
| `1072` | base_monthly_income | Base Monthly Income | income |
| `1073` | years_in_profession | Years in Profession | employment |
| `11` | property_address | Property Street Address | property |
| `1109` | loan_amount | Loan Amount | loan_info |
| `1169` | employer_name | Employer Name | employment |
| `1172` | loan_type | Mortgage Type | loan_info |
| `1182` | employer_address | Employer Address | employment |
| `12` | property_city | Property City | property |
| `1286` | job_title | Job Title | employment |
| `14` | property_state | Property State | property |
| `1402` | borrower_dob | Borrower Date of Birth | borrower_info |
| `1480` | borrower_cell_phone | Borrower Cell Phone | borrower_info |
| `1490` | declaration_primary_residence | Declaration - Primary Residence Intent | declarations |
| `1491` | declaration_ownership_3yr | Declaration - Owned Property in Last 3 Years | declarations |
| `15` | property_zip | Property ZIP | property |
| `1544` | borrower_ethnicity | Borrower Ethnicity | borrower_info |
| `172` | other_income_type | Other Income Type | income |
| `173` | other_income_amount | Other Income Amount (Monthly) | income |
| `1811` | occupancy | Occupancy | loan_info |
| `186` | emd_amount | EMD Amount | assets |
| `19` | loan_purpose | Loan Purpose | loan_info |
| `218` | rental_income | Rental Income | income |
| `231` | gift_amount | Gift Amount | assets |
| `3` | note_rate | Note Rate | loan_info |
| `33` | estate_held | Estate Will Be Held In | title |
| `34` | manner_of_title | Manner in Which Title Will Be Held | title |
| `35` | borrower_current_address | Borrower Current Street Address | borrower_info |
| `350` | total_monthly_payments | Total Monthly Liabilities | liabilities |
| `356` | appraised_value | Appraised / Estimated Value | collateral |
| `364` | loan_number | Loan Number | loan_info |
| `4000` | borrower_first_name | Borrower First Name | borrower_info |
| `4001` | borrower_middle_name | Borrower Middle Name | borrower_info |
| `4002` | borrower_last_name | Borrower Last Name | borrower_info |
| `4004` | coborrower_first_name | Co-Borrower First Name | borrower_info |
| `558` | owned_properties_count | Number of Owned Properties (REO) | assets |
| `65` | borrower_ssn | Borrower SSN | borrower_info |
| `66` | borrower_home_phone | Borrower Home Phone | borrower_info |
| `732` | total_assets | Total Assets | assets |
| `733` | checking_balance | Checking Account Balance | assets |
| `734` | savings_balance | Savings Account Balance | assets |
| `762` | lock_expiration | Lock Expiration Date | lock |
| `799` | qualifying_rate | Qualifying Rate | loan_info |
| `CX.AMI.ELIGIBILITY` | ami_eligibility | AMI / Affordable Loan Eligibility | grant_program |
| `CX.AMI.PERCENTAGE` | ami_percentage | AMI Percentage | grant_program |
| `CX.APPRAISAL.WAIVER` | appraisal_waiver | Appraisal Waiver | collateral |
| `CX.ATTACHMENT.TYPE` | attachment_type | Attachment Type (Attached/Detached) | property |
| `CX.AUS.COLLATERAL.RELIEF` | aus_collateral_relief | AUS Collateral Relief | aus |
| `CX.CONDO.PROJECT.ID` | condo_project_id | Condo Project ID | property |
| `CX.CONDO.PROJECT.NAME` | condo_project_name | Condo Project Name | property |
| `CX.CONDO.PROJECT.TYPE` | condo_project_type | Condo Project Type | property |
| `CX.DOC.TYPE` | doc_type | Doc Type (Wet / E-sign / Hybrid) | processor_workflow |
| `CX.FINAL.VESTING` | final_vesting | Final Vesting | title |
| `CX.FNMA.ADDITIONAL.DATA` | fnma_additional_data | Fannie Mae Additional Data - AMI | grant_program |
| `CX.INVESTOR.TYPE` | investor_type | Investor Type (Conforming / Non-Del) | processor_workflow |
| `CX.KM.CL.ADDITIONAL.NOTES` | cover_letter_additional_notes | Cover Letter - Additional Notes (pre-populated) | cover_letter |
| `CX.KM.CL.APPRAISAL` | cover_letter_appraisal | Cover Letter - Appraisal (pre-populated) | cover_letter |
| `CX.KM.CL.TITLE.COMPANY` | cover_letter_title_company | Cover Letter - Title Company (pre-populated) | cover_letter |
| `CX.KM.SUBMISSION.NOTES` | submission_notes | Submission Notes (Cover Letter) | cover_letter |
| `CX.LOAN.LOCKED` | loan_locked | Loan Locked Status | lock |
| `CX.LOCKED.LE.PRESENT` | locked_le_present | Locked LE Present | disclosures |
| `CX.MILESTONE.CURRENT` | current_milestone | Current Milestone | loan_info |
| `CX.PROCESSOR.NAME` | processor_name | Processor Name | loan_info |
| `CX.REALTOR.EMAIL` | realtor_email | Realtor Email | file_contacts |
| `CX.REQUIRED.FIELDS.STATUS` | required_fields_status | Encompass Required Fields Status | submission |
| `CX.SIGNING.DATE` | signing_date | Signing Date | closing |
| `CX.TITLE.COMPANY.EMAIL` | title_company_email | Title Company Email | file_contacts |
| `CX.TITLE.COMPANY.NAME` | title_company_name | Title Company Name | file_contacts |
| `CX.VESTING.DESCRIPTION` | vesting_description | Vesting Description | title |
| `CX.WIRE.REQUESTED.DATE` | wire_requested_date | Wire Requested Date | closing |

## Document Types

- 1003 URLA
- Assets
- Bank Statement
- Borrower's Certification and Authorization
- Driver's License
- Estimated Settlement Statement
- Income Calc
- Lock Confirmation
- MI Quote
- Mortgage Statement
- Ocrolus Processing
- Paystubs
- Purchase Agreement
- Transmittal Summary
- Unassigned
- Underwriting (DU / LP)
- VOD
- VOE
- VOL
- W2

---

## Substep 0.5 — Validate Property Address (USPS)

**Tool:** `validate_property_address`

Reads the subject property address from `state["los_fields"]` (keys: `property_address`, `property_city`, `property_state`, `property_zip`) and the Purchase Contract address from `state["doc_fields"]` (key: `purchase_property_address`), then calls the USPS Address Validation API v3.

**Stores in state:**
```json
{
  "address_validation": {
    "valid": true,
    "normalized": "123 MAIN ST, IRVINE, CA 92612",
    "dpv_confirmation": "Y",
    "error": null,
    "warnings": [],
    "mismatch_with_purchase_contract": false,
    "purchase_contract_address": "123 Main Street",
    "los_address": "123 Main St, Irvine, CA 92612"
  }
}
```

`dpv_confirmation` values: `Y` = confirmed, `S` = confirmed (no secondary), `D` = missing secondary, `N` = not confirmed.

Downstream tool `review_borrower_summary` reads `state["address_validation"]` to produce flags — it does NOT perform its own address comparison.

---

## Step Completion

When ALL 5 substeps above are completed (0.1 through 0.5):
1. Call `save_step_report(step_name="STEP_00", status="completed", ...)`
2. Call `write_todo(step_id="STEP_00", status="completed")` to advance to the next step
3. Call `write_todo(step_id="STEP_01", status="in_progress")` to start STEP_01 (Pre-Checks)

⚠️ You MUST call write_todo to advance — do NOT produce a text-only response.
