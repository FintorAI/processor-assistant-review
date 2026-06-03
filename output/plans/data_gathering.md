# Step 0 - [VERIFICATION] Data Gathering

**Phase**: VERIFICATION
**Auto-generated**: Yes — this step is derived from fields used by all other steps.
**Tools**: `find_loan`, `fetch_los_fields`, `fetch_doc_fields`, `build_loan_summary`

## Purpose

Gather all data needed by the workflow in one upfront step:
- Find the loan GUID from the loan number
- Batch-read 252 LOS fields from Encompass
- Extract fields from 29 document types
- Build the Loan Summary (URLA) — a categorized snapshot that never changes

**NOTE**: Each substep has its own dedicated tool. State is automatically injected.

## Available Tools

| Tool | Purpose |
|------|---------|
| `find_loan` | Search Encompass for loan GUID |
| `fetch_los_fields` | Fetch all needed LOS fields in one batch call |
| `fetch_doc_fields` | Extract fields from specific document types |
| `build_loan_summary` | Build categorized URLA-style loan summary from los_fields |
| `validate_property_address` | Validate subject property address via USPS |
| `extract_almas_images` | OCR images attached to Almas' notes via Claude vision |

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

### Substep 0.2 - Fetch LOS Fields (252 fields)
**Tools**: `fetch_los_fields`

Call `fetch_los_fields` to batch-read all Encompass field IDs.
Results are stored in `state["los_fields"]` organized by key.

### Substep 0.3 - Fetch Doc Fields (29 document types)
**Tools**: `fetch_doc_fields`

Call `fetch_doc_fields` to extract fields from documents in the eFolder.
Results are stored in `state["doc_fields"]` organized by key.

### Substep 0.6 - Extract Almas-Notes Images (Vision OCR)
**Tools**: `extract_almas_images`

Call `extract_almas_images()` to OCR any images attached to Almas' notes
(`additional_info.almas_notes_images`). Transcribed text is stored on
`state["almas_notes_images"]` for the Cover Letter (7.1) and File Contacts (1.2).
This is a no-op when no images were provided — safe to call always.

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
| `1012` | project_type_1012 | Project Type dropdown (Transmittal Summary) | property |
| `1014` | qualifying_rate | Qualifying Rate | loan_info |
| `1041` | property_type | Property Type | property |
| `1051` | mers_min | MERS MIN | loan_info |
| `1066` | estate_held | Estate Will Be Held In | title |
| `1069` | prior_title_held | Declaration 5a(A)(2) — How Title Was Held (Prior Property) | declarations |
| `11` | property_address | Property Street Address | property |
| `1108` | coborr_ownership_3yr | Declaration 5a(A) — Co-Borrower Ownership Interest Past 3 Years | declarations |
| `1109` | loan_amount | Loan Amount | loan_info |
| `1172` | loan_type | Mortgage Type | loan_info |
| `1179` | coborrower_email | Co-Borrower Email | borrower_info |
| `12` | property_city | Property City | property |
| `1240` | borrower_email | Borrower Email | borrower_info |
| `1264` | lender | Lender | loan_info |
| `13` | property_county | Property County | property |
| `1335` | down_payment_amount | Down Payment Amount | loan_info |
| `136` | los_purchase_price | Purchase Price (LOS) | loan_info |
| `14` | property_state | Property State | property |
| `1401` | loan_program | Loan Program | loan_info |
| `1402` | borrower_dob | Borrower Date of Birth | borrower_info |
| `1403` | coborrower_dob | Co-Borrower Date of Birth | borrower_info |
| `1414` | equifax_score | Borrower Equifax/Beacon Score | credit |
| `1415` | coborrower_equifax_score | Co-Borrower Equifax/Beacon Score | credit |
| `1450` | transunion_score | Borrower TransUnion/Empirica Score | credit |
| `1452` | coborrower_transunion_score | Co-Borrower TransUnion/Empirica Score | credit |
| `1480` | coborrower_cell_phone | Co-Borrower Cell Phone | borrower_info |
| `1490` | borrower_cell_phone | Borrower Cell Phone | borrower_info |
| `15` | property_zip | Property ZIP | property |
| `1541` | property_review_type | Level of Property Review (Exterior/Interior) | property |
| `1542` | appraisal_form_number | Appraisal Form Number | property |
| `1544` | borrower_ethnicity | Borrower Ethnicity | borrower_info |
| `1553` | transmittal_project_type | Project Type (Transmittal Summary) | property |
| `16` | property_units | Subject Property Number of Units | property |
| `1715` | borrower_work_phone | Borrower Business/Work Phone | borrower_info |
| `1716` | coborrower_work_phone | Co-Borrower Business/Work Phone | borrower_info |
| `172` | other_income_type | Other Income Type | income |
| `173` | other_income_amount | Other Income Amount (Monthly) | income |
| `1771` | down_payment_pct | Down Payment % | loan_info |
| `1785` | closing_cost_program | Closing Cost Program | loan_info |
| `1811` | occupancy | Property Will Be (Occupancy) | loan_info |
| `1819` | borr_mailing_same_as_present | Borrower Mailing Address Same as Present | borrower_info |
| `1820` | coborr_mailing_same_as_present | Co-Borrower Mailing Address Same as Present | borrower_info |
| `1821` | estimated_value | Estimated Value | collateral |
| `186` | emd_amount | EMD Amount | assets |
| `1867` | final_vesting | Final Vesting | title |
| `1868` | borrower_vesting_name | Borrower Vesting Name | title |
| `1871` | borrower_vesting_type | Borrower Vesting Type | title |
| `1872` | borrower_vesting_desc | Borrower Vesting Description | title |
| `1873` | coborrower_vesting_name | Co-Borrower Vesting Name | title |
| `1876` | coborrower_vesting_type | Co-Borrower Vesting Type | title |
| `1877` | coborrower_vesting_desc | Co-Borrower Vesting Description | title |
| `19` | loan_purpose | Loan Purpose | loan_info |
| `218` | rental_income | Rental Income | income |
| `231` | gift_amount | Gift Amount | assets |
| `2400` | rate_is_locked | Rate Is Locked (Y/N) | loan_info |
| `3` | note_rate | Note Rate | loan_info |
| `300` | credit_reference_number | Credit Reference Number | credit |
| `325` | term_due_in_months | Term Due In (Months) | loan_info |
| `3253` | last_rate_set_date | Last Rate Set Date | loan_info |
| `3259` | rate_lock_disclosure_date | Rate Lock Disclosure Date | loan_info |
| `3293` | undiscounted_rate | Undiscounted Rate | loan_info |
| `33` | manner_of_title | Manner in Which Title Will Be Held | title |
| `35` | borrower_current_address | Borrower Current Street Address | borrower_info |
| `350` | total_monthly_payments | Total Monthly Liabilities | liabilities |
| `356` | appraised_value | Appraised Value | collateral |
| `364` | loan_number | Loan Number | loan_info |
| `3941` | secondary_registration | Secondary Registration | loan_info |
| `4` | loan_term_months | Loan Term (Months) | loan_info |
| `4000` | borrower_first_name | Borrower First Name | borrower_info |
| `4001` | borrower_middle_name | Borrower Middle Name | borrower_info |
| `4002` | borrower_last_name | Borrower Last Name | borrower_info |
| `4003` | borrower_name_suffix | Borrower Name Suffix | borrower_info |
| `4004` | coborrower_first_name | Co-Borrower First Name | borrower_info |
| `4005` | coborrower_middle_name | Co-Borrower Middle Name | borrower_info |
| `4006` | coborrower_last_name | Co-Borrower Last Name | borrower_info |
| `403` | declaration_ownership_3yr | Declaration 5a(A) — Ownership Interest Past 3 Years | declarations |
| `4114` | borrower_est_closing_date | Borrower Est Closing Date | closing |
| `418` | declaration_primary_residence | Declaration - Primary Residence Intent (5a-A) | declarations |
| `420` | lien_position | Lien Position | loan_info |
| `432` | lock_days | Lock Period (# of Days) | loan_info |
| `471` | borrower_sex | Borrower Sex | borrower_info |
| `478` | coborrower_sex | Co-Borrower Sex | borrower_info |
| `479` | marital_status | Borrower Marital Status | borrower_info |
| `4920` | borrower_accept_sms | Borrower Accept Text/SMS | borrower_info |
| `4935` | coborrower_accept_sms | Co-Borrower Accept Text/SMS | borrower_info |
| `5` | monthly_payment | Monthly Payment (P&I) | loan_info |
| `52` | borrower_marital_status | Borrower Marital Status | borrower_info |
| `53` | borrower_dependents_count | Borrower Dependents Count | borrower_info |
| `54` | borrower_dependent_ages | Borrower Dependent Ages | borrower_info |
| `558` | owned_properties_count | Number of Owned Properties (REO) | assets |
| `60` | coborrower_experian_score | Co-Borrower Experian/FICO Score | credit |
| `608` | amort_type | Amortization Type | loan_info |
| `638` | seller_1_name | Seller 1 Name | transaction |
| `65` | borrower_ssn | Borrower SSN | borrower_info |
| `66` | borrower_home_phone | Borrower Home Phone | borrower_info |
| `67` | experian_score | Borrower Experian/FICO Score | credit |
| `732` | total_assets | Total Assets | assets |
| `733` | checking_balance | Checking Account Balance | assets |
| `734` | savings_balance | Savings Account Balance | assets |
| `736` | monthly_income | Monthly Income | income |
| `748` | closing_date | Closing Date | closing |
| `761` | lock_date | Lock Date | loan_info |
| `762` | lock_expires | Lock Expiration Date | loan_info |
| `763` | est_closing_date | Est Closing Date | closing |
| `84` | coborrower_marital_status | Co-Borrower Marital Status | borrower_info |
| `85` | coborr_dependents_count | Co-Borrower Number of Dependents | borrower_info |
| `86` | coborr_dependents_ages | Co-Borrower Dependents Ages | borrower_info |
| `912` | total_monthly_payment | Total Monthly Payment | loan_info |
| `97` | coborrower_ssn | Co-Borrower SSN | borrower_info |
| `98` | coborrower_home_phone | Co-Borrower Home Phone | borrower_info |
| `981` | prior_property_type | Declaration 5a(A)(1) — Type of Prior Property | declarations |
| `BE0102` | be01_employer_name | Employment 1 — Employer Name | employment |
| `BE0105` | be01_employer_city | Employment 1 — Employer City | employment |
| `BE0106` | be01_employer_state | Employment 1 — Employer State | employment |
| `BE0107` | be01_employer_zip | Employment 1 — Employer Zip | employment |
| `BE0108` | be01_voe_is_for | Employment 1 — VOE Is For (Borrower / Co-Borrower) | employment |
| `BE0109` | be01_employment_type | Employment 1 — Type (Current / Prior) | employment |
| `BE0110` | be01_position_title | Employment 1 — Position / Title / Type of Business | employment |
| `BE0113` | be01_years_in_job | Employment 1 — Years in This Job | employment |
| `BE0114` | be01_date_terminated | Employment 1 — Date Terminated | employment |
| `BE0116` | be01_years_in_line_of_work | Employment 1 — Years in Line of Work | employment |
| `BE0117` | be01_employer_phone | Employment 1 — Employer Phone | employment |
| `BE0119` | be01_monthly_base_pay | Employment 1 — Monthly Base Pay | income |
| `BE0133` | be01_months_in_job | Employment 1 — Months in This Job | employment |
| `BE0151` | be01_date_hired | Employment 1 — Date Hired | employment |
| `BE0152` | be01_months_in_line_of_work | Employment 1 — Months in Line of Work | employment |
| `BE0158` | be01_employer_unit_type | Employment 1 — Unit Type (Suite / Apt / etc.) | employment |
| `BE0159` | be01_employer_unit_number | Employment 1 — Unit Number | employment |
| `BE0160` | be01_employer_street | Employment 1 — Employer Street Address | employment |
| `BE0180` | be01_foreign_address | Employment 1 — Foreign Address Checkbox | employment |
| `BE0202` | be02_employer_name | Employment 2 — Employer Name | employment |
| `BE0205` | be02_employer_city | Employment 2 — Employer City | employment |
| `BE0206` | be02_employer_state | Employment 2 — Employer State | employment |
| `BE0207` | be02_employer_zip | Employment 2 — Employer Zip | employment |
| `BE0208` | be02_voe_is_for | Employment 2 — VOE Is For (Borrower / Co-Borrower) | employment |
| `BE0209` | be02_employment_type | Employment 2 — Type (Current / Prior) | employment |
| `BE0210` | be02_position_title | Employment 2 — Position / Title / Type of Business | employment |
| `BE0213` | be02_years_in_job | Employment 2 — Years in This Job | employment |
| `BE0214` | be02_date_terminated | Employment 2 — Date Terminated | employment |
| `BE0216` | be02_years_in_line_of_work | Employment 2 — Years in Line of Work | employment |
| `BE0217` | be02_employer_phone | Employment 2 — Employer Phone | employment |
| `BE0219` | be02_monthly_base_pay | Employment 2 — Monthly Base Pay | income |
| `BE0233` | be02_months_in_job | Employment 2 — Months in This Job | employment |
| `BE0236` | be01_authorization_printed | Employment 1 — Print Authorization on Signature Line | employment |
| `BE0251` | be02_date_hired | Employment 2 — Date Hired | employment |
| `BE0252` | be02_months_in_line_of_work | Employment 2 — Months in Line of Work | employment |
| `BE0258` | be02_employer_unit_type | Employment 2 — Unit Type | employment |
| `BE0259` | be02_employer_unit_number | Employment 2 — Unit Number | employment |
| `BE0260` | be02_employer_street | Employment 2 — Employer Street Address | employment |
| `BE0280` | be02_foreign_address | Employment 2 — Foreign Address Checkbox | employment |
| `BE0302` | be03_employer_name | Employment 3 — Employer Name | employment |
| `BE0308` | be03_voe_is_for | Employment 3 — VOE Is For (Borrower / Co-Borrower) | employment |
| `BE0309` | be03_employment_type | Employment 3 — Type (Current / Prior) | employment |
| `BE0310` | be03_position_title | Employment 3 — Position / Title / Type of Business | employment |
| `BE0313` | be03_years_in_job | Employment 3 — Years in This Job | employment |
| `BE0314` | be03_date_terminated | Employment 3 — Date Terminated | employment |
| `BE0319` | be03_monthly_base_pay | Employment 3 — Monthly Base Pay | income |
| `BE0333` | be03_months_in_job | Employment 3 — Months in This Job | employment |
| `BE0351` | be03_date_hired | Employment 3 — Date Hired | employment |
| `Borr.OccupancyIntent` | borrower_occupancy_intent | Borrower Occupancy Intent | borrower_info |
| `CUST50FV` | signing_date | Signing Date | closing |
| `CX.AMI.ELIGIBILITY` | ami_eligibility | AMI / Affordable Loan Eligibility | grant_program |
| `CX.AMI.PERCENTAGE` | ami_percentage | AMI Percentage | grant_program |
| `CX.APPRAISAL.WAIVER` | appraisal_waiver | Appraisal Waiver | collateral |
| `CX.ATTACHMENT.TYPE` | attachment_type | Attachment Type (Attached/Detached) | property |
| `CX.CONDO.PROJECT.ID` | condo_project_id | Condo Project ID | property |
| `CX.CONDO.PROJECT.NAME` | condo_project_name | Condo Project Name | property |
| `CX.DOCUMENTATIONTYPE` | doc_type_submission | Documentation Type (NON-QM Submission) | processor_workflow |
| `CX.FNMA.ADDITIONAL.DATA` | fnma_additional_data | Fannie Mae Additional Data - AMI | grant_program |
| `CX.KM.CL.ADDITIONAL.NOTES` | cover_letter_additional_notes | Cover Letter - Additional Notes (pre-populated) | cover_letter |
| `CX.KM.CL.APPRAISAL` | cover_letter_appraisal | Cover Letter - Appraisal (pre-populated) | cover_letter |
| `CX.KM.CL.TITLE.COMPANY` | cover_letter_title_company | Cover Letter - Title Company (pre-populated) | cover_letter |
| `CX.KM.SUBMISSION.NOTES` | submission_notes | Submission Notes (Cover Letter) | cover_letter |
| `CX.NBSFLAG` | nbs_flag | Non-Borrowing Spouse Flag | borrower_info |
| `CX.NBSINFO` | nbs_info | Non-Borrowing Spouse Name | borrower_info |
| `CX.NONDEL.INV.APPROVAL` | non_del_inv_approval | Non-Del Inv. Approval | processor_workflow |
| `CX.PRODUCTTYPE` | product_type | Product Type | processor_workflow |
| `CX.WIREDATELO` | wire_requested_date | Wire Requested Date | closing |
| `CoBorr.OccupancyIntent` | coborrower_occupancy_intent | Co-Borrower Occupancy Intent | borrower_info |
| `FE0112` | borr_1c_total_gross_income | Borrower 1c — Total Gross Income | income |
| `FE0119` | borr_base_monthly_income | Borrower — Base Monthly Income (Section 1b) | income |
| `FE0156` | borr_1c_monthly_income | Borrower 1c — Monthly Income (or Loss) | income |
| `FE0212` | coborr_1c_total_gross_income | Co-Borrower 1c — Total Gross Income | income |
| `FE0219` | coborr_base_monthly_income | Co-Borrower — Base Monthly Income (Section 1b) | income |
| `FE0256` | coborr_1c_monthly_income | Co-Borrower 1c — Monthly Income (or Loss) | income |
| `FE0302` | borr_1c_employer_name | Borrower 1c — Employer or Business Name | income |
| `FE0312` | borr_1d_total_gross_income | Borrower 1d — Total Gross Income | income |
| `FE0316` | borr_1c_years_in_line | Borrower 1c — Years in Line of Work | income |
| `FE0351` | borr_1c_start_date | Borrower 1c — Start Date | income |
| `FE0352` | borr_1c_months_in_line | Borrower 1c — Months in Line of Work | income |
| `FE0356` | borr_1d_monthly_income | Borrower 1d — Monthly Income (or Loss) | income |
| `FE0402` | coborr_1c_employer_name | Co-Borrower 1c — Employer or Business Name | income |
| `FE0412` | coborr_1d_total_gross_income | Co-Borrower 1d — Total Gross Income | income |
| `FE0416` | coborr_1c_years_in_line | Co-Borrower 1c — Years in Line of Work | income |
| `FE0451` | coborr_1c_start_date | Co-Borrower 1c — Start Date | income |
| `FE0452` | coborr_1c_months_in_line | Co-Borrower 1c — Months in Line of Work | income |
| `FE0456` | coborr_1d_monthly_income | Co-Borrower 1d — Monthly Income (or Loss) | income |
| `FE0502` | borr_1d_employer_name | Borrower 1d — Employer or Business Name | income |
| `FE0514` | borr_1d_end_date | Borrower 1d — End Date | income |
| `FE0551` | borr_1d_start_date | Borrower 1d — Start Date | income |
| `FE0602` | coborr_1d_employer_name | Co-Borrower 1d — Employer or Business Name | income |
| `FE0614` | coborr_1d_end_date | Co-Borrower 1d — End Date | income |
| `FE0651` | coborr_1d_start_date | Co-Borrower 1d — Start Date | income |
| `FR0106` | borr_present_city | Borrower Present City | borrower_info |
| `FR0107` | borr_present_state | Borrower Present State | borrower_info |
| `FR0108` | borr_present_zip | Borrower Present ZIP | borrower_info |
| `FR0112` | borr_present_yrs | Borrower Years at Current Address | borrower_info |
| `FR0115` | borr_housing_type | Borrower Current Housing Type | borrower_info |
| `FR0116` | borr_housing_amount | Borrower Current Housing Expense Amount | borrower_info |
| `FR0124` | borr_present_mos | Borrower Months at Current Address | borrower_info |
| `FR0126` | borr_present_addr | Borrower Present Street Address | borrower_info |
| `FR0212` | coborr_present_yrs | Co-Borrower Years at Current Address | borrower_info |
| `FR0224` | coborr_present_mos | Co-Borrower Months at Current Address | borrower_info |
| `FR0306` | borr_former_city | Borrower Former City | borrower_info |
| `FR0307` | borr_former_state | Borrower Former State | borrower_info |
| `FR0308` | borr_former_zip | Borrower Former Zip | borrower_info |
| `FR0315` | borr_former_housing_type | Borrower Former Housing Type | borrower_info |
| `FR0316` | borr_former_housing_amount | Borrower Former Housing Expense Amount | borrower_info |
| `FR0326` | borr_former_addr | Borrower Former Street Address | borrower_info |
| `FR0406` | coborr_former_city | Co-Borrower Former City | borrower_info |
| `FR0407` | coborr_former_state | Co-Borrower Former State | borrower_info |
| `FR0408` | coborr_former_zip | Co-Borrower Former Zip | borrower_info |
| `FR0415` | coborr_housing_type | Co-Borrower Current Housing Type | borrower_info |
| `FR0416` | coborr_housing_amount | Co-Borrower Current Housing Expense Amount | borrower_info |
| `FR0426` | coborr_former_addr | Co-Borrower Former Street Address | borrower_info |
| `TSUM.PropertyFormType` | property_form_type | Property Form Type (Transmittal Summary) | property |
| `URLA.X1` | borrower_citizenship | Borrower Citizenship | borrower_info |
| `URLA.X123` | borr_military_active_duty | Borrower Currently Serving on Active Duty | borrower_info |
| `URLA.X124` | borr_military_retired | Borrower Retired/Discharged/Separated | borrower_info |
| `URLA.X125` | borr_military_reserve | Borrower Non-Activated Reserve/National Guard | borrower_info |
| `URLA.X126` | coborr_military_active_duty | Co-Borrower Currently Serving on Active Duty | borrower_info |
| `URLA.X127` | coborr_military_retired | Co-Borrower Retired/Discharged/Separated | borrower_info |
| `URLA.X128` | coborr_military_reserve | Co-Borrower Non-Activated Reserve/National Guard | borrower_info |
| `URLA.X13` | borr_military_service | Borrower Military Service Indicator | borrower_info |
| `URLA.X14` | coborr_military_service | Co-Borrower Military Service Indicator | borrower_info |
| `URLA.X19` | borr_military_surviving_spouse | Borrower Surviving Spouse | borrower_info |
| `URLA.X199` | borr_1b_dna | Borrower — Section 1b Does Not Apply | income |
| `URLA.X2` | coborrower_citizenship | Co-Borrower Citizenship | borrower_info |
| `URLA.X20` | coborr_military_surviving_spouse | Co-Borrower Surviving Spouse | borrower_info |
| `URLA.X200` | coborr_1b_dna | Co-Borrower — Section 1b Does Not Apply | income |
| `URLA.X201` | borr_1c_dna | Borrower — Section 1c Does Not Apply | income |
| `URLA.X202` | coborr_1c_dna | Co-Borrower — Section 1c Does Not Apply | income |
| `URLA.X203` | borr_1d_dna | Borrower — Section 1d Does Not Apply | income |
| `URLA.X204` | coborr_1d_dna | Co-Borrower — Section 1d Does Not Apply | income |
| `URLA.X21` | borr_language_preference | Borrower Language Preference | borrower_info |
| `URLA.X22` | coborr_language_preference | Co-Borrower Language Preference | borrower_info |
| `URLA.X265` | borr_former_addr_does_not_apply | Borrower Former Address Does Not Apply | borrower_info |
| `URLA.X266` | coborr_former_addr_does_not_apply | Co-Borrower Former Address Does Not Apply | borrower_info |
| `URLA.X40` | borr_other_income_dna | Borrower — Income from Other Sources Does Not Apply (1e) | income |
| `URLA.X41` | coborr_other_income_dna | Co-Borrower — Income from Other Sources Does Not Apply (1e) | income |
| `URLA.X73` | property_address_urla | Property Street Address (URLA Lender — editable) | property |
| `VASUMM.X23` | credit_score_decision | Credit Score for Decision Making | credit |

## Document Types

- 1003 URLA
- Assets
- Bank Statement
- Borrower's Certification & Authorization
- Credit Report
- Driver's License
- Estimated Settlement Statement
- Evidence of Hazard Insurance
- Flood Certificate
- General Letter of Explanation
- Gift Letter
- HOA Statement
- LDP
- MD DUAL CAPACITY IN REAL ESTATE
- MD Important Notice Regarding Counseling
- MD Notice Regarding Right for Assumption
- MD Notice of Right to Rescind
- MD Right to Choose Insurance Provider
- MD Settlement Services/Right to Choose
- Mortgage Statement
- Paystubs
- Property Tax Bill
- Purchase Agreement
- Title Report
- Underwriting (DU / LP)
- VOD
- VOE
- VOE - non service provider
- VOL

---

## Step Completion

When ALL substeps above are completed (0.1 through 0.6):
1. Call `save_step_report(step_name="STEP_00", status="completed", ...)`
2. Call `write_todo(step_id="STEP_00", status="completed")` to advance to the next step
3. Call `write_todo(step_id="STEP_01", status="in_progress")` to start STEP_01 (Pre-Checks)

⚠️ You MUST call write_todo to advance — do NOT produce a text-only response.
