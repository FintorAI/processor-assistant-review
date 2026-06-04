## Purpose

Verify AND populate every field in the Borrower Summary / Origination screen. Covers ID expiration, property address verification against listing, credit score, subject property details, loan amount, and AMI / Affordable Loan Eligibility.


**⚠️ This step may WRITE to Encompass.** Substeps that write are marked below.

**NOTE**: Each substep has its own dedicated tool. State (`los_fields`, `doc_fields`, `loan_summary`) is automatically injected.

## Available Tools

| Tool | Purpose |
|------|---------|
| `review_borrower_summary` | Review Borrower Summary - Origination |

## Overview

| Substep | Description | Tool |
|---------|-------------|------|
| 2.1 | Review Borrower Summary - Origination | `review_borrower_summary` |

## Tool Calls

```python
# Substep 2.1 - Review Borrower Summary - Origination
# ⚠️ This substep WRITES to Encompass
review_borrower_summary(loan_guid=loan_id)
```

---

## Substeps

### Substep 2.1 - Review Borrower Summary - Origination
**Tool**: `review_borrower_summary`

Walk through the Borrower Summary – Origination screen. Check every field for completeness (cell phone = home phone if empty). Verify ID is not expired. Cross-check subject property address against listing (Google). Confirm credit score, loan amount, and AMI / Affordable Loan Eligibility fields.


**LOS Fields (read from state):**

| Encompass Field | Field ID | Key | Purpose |
|-----------------|----------|-----|---------|
| Borrower First Name | `4000` | `borrower_first_name` | Completeness check |
| Borrower Middle Name | `4001` | `borrower_middle_name` | Completeness check |
| Borrower Last Name | `4002` | `borrower_last_name` | Completeness check |
| Borrower Name Suffix | `4003` | `borrower_name_suffix` | Completeness check |
| Borrower SSN | `65` | `borrower_ssn` | Completeness check |
| Borrower Date of Birth | `1402` | `borrower_dob` | Used with ID expiry cross-check |
| Borrower Home Phone | `66` | `borrower_home_phone` | Fallback for cell phone if cell is empty |
| Borrower Business/Work Phone | `1715` | `borrower_work_phone` | Completeness check — needs live field ID validation |
| Borrower Cell Phone | `1490` | `borrower_cell_phone` | If empty, copy from home phone (field 66) |
| Borrower Accept Text/SMS | `4920` | `borrower_accept_sms` | Auto-check if unchecked — required for text communications |
| Borrower Email | `1240` | `borrower_email` | Completeness check |
| Borrower Marital Status | `52` | `borrower_marital_status` | Completeness check; also drives vesting at Step 8 |
| Borrower Dependents Count | `53` | `borrower_dependents_count` | Completeness check |
| Borrower Dependent Ages | `54` | `borrower_dependent_ages` | Completeness check |
| Co-Borrower First Name | `4004` | `coborrower_first_name` | Completeness check if coborrower present |
| Co-Borrower Last Name | `4006` | `coborrower_last_name` | Completeness check if coborrower present |
| Co-Borrower SSN | `97` | `coborrower_ssn` | Completeness check if coborrower present |
| Co-Borrower Date of Birth | `1403` | `coborrower_dob` | ID expiry cross-check for coborrower |
| Co-Borrower Home Phone | `98` | `coborrower_home_phone` | Completeness check if coborrower present |
| Co-Borrower Business/Work Phone | `1716` | `coborrower_work_phone` | Completeness check if coborrower present — needs live field ID validation |
| Co-Borrower Cell Phone | `1480` | `coborrower_cell_phone` | Completeness check if coborrower present |
| Co-Borrower Accept Text/SMS | `4935` | `coborrower_accept_sms` | Auto-check if unchecked and coborrower is present |
| Co-Borrower Email | `1179` | `coborrower_email` | Completeness check if coborrower present |
| Co-Borrower Marital Status | `84` | `coborrower_marital_status` | Completeness check if coborrower present |
| Borrower Experian/FICO Score | `67` | `experian_score` | Flag if blank |
| Borrower TransUnion/Empirica Score | `1450` | `transunion_score` | Flag if blank |
| Borrower Equifax/Beacon Score | `1414` | `equifax_score` | Flag if blank |
| Co-Borrower Experian/FICO Score | `60` | `coborrower_experian_score` | Flag if blank when co-borrower is present |
| Co-Borrower TransUnion/Empirica Score | `1452` | `coborrower_transunion_score` | Flag if blank when co-borrower is present |
| Co-Borrower Equifax/Beacon Score | `1415` | `coborrower_equifax_score` | Flag if blank when co-borrower is present |
| Credit Score for Decision Making | `VASUMM.X23` | `credit_score_decision` | Flag if blank — used by both borrower and co-borrower |
| Credit Reference Number | `300` | `credit_reference_number` | Flag if blank |
| Property Street Address | `11` | `property_address` | Verified against online listing and Purchase Contract |
| Property Street Address (URLA Lender — editable) | `URLA.X73` | `property_address_urla` | Editable version of property street address on 1003 URLA Lender form. Used as primary source when field 11 is read-only. Used for cash-out refi current address comparison.
 |
| Borrower Present Street Address | `FR0126` | `borr_present_addr` | Cash-out refi check — compare to subject property address |
| Borrower Present City | `FR0106` | `borr_present_city` | Cash-out refi occupancy check |
| Borrower Present State | `FR0107` | `borr_present_state` | Cash-out refi occupancy check |
| Borrower Present ZIP | `FR0108` | `borr_present_zip` | Cash-out refi occupancy check |
| Property City | `12` | `property_city` | Address verification |
| Property County | `13` | `property_county` | Full address verification |
| Property State | `14` | `property_state` | Address verification |
| Property ZIP | `15` | `property_zip` | Address verification |
| Loan Purpose | `19` | `loan_purpose` | Purchase confirmation |
| Loan Amount | `1109` | `loan_amount` | Confirm loan amount matches Purchase Contract |
| Appraised Value | `356` | `appraised_value` | If empty, copy estimated_value into this field (pre-appraisal fill) |
| Estimated Value | `1821` | `estimated_value` | Compare against purchase price from Purchase Contract; copy to 356 if 356 is empty |
| Purchase Price (LOS) | `136` | `los_purchase_price` | Cross-check LOS purchase price against purchase price extracted from Purchase Contract |
| Lender | `1264` | `lender` | Must be "All Western Mortgage Inc." |
| Loan Program | `1401` | `loan_program` | Presence check; info flag |
| Closing Cost Program | `1785` | `closing_cost_program` | Cross-check against loan purpose (19) for purchase/refi mismatch |
| Loan Number | `364` | `loan_number` | Presence check; warning if empty |
| MERS MIN | `1051` | `mers_min` | Info flag |
| Property Will Be (Occupancy) | `1811` | `occupancy` | Presence check; info flag |
| Loan Type | `1172` | `loan_type` | Presence check; info flag |
| Lien Position | `420` | `lien_position` | Presence check; info flag |
| Amortization Type | `608` | `amort_type` | Presence check; info flag |
| Loan Term (Months) | `4` | `loan_term_months` | Presence check; info flag |
| Term Due In (Months) | `325` | `term_due_in_months` | Presence check; info flag |
| Note Rate | `3` | `note_rate` | Presence check; warning if empty |
| Qualifying Rate | `1014` | `qualifying_rate` | Presence check; warning if empty |
| Undiscounted Rate | `3293` | `undiscounted_rate` | Presence check; info flag |
| Monthly Payment (P&I) | `5` | `monthly_payment` | Presence check; warning if empty |
| Total Monthly Payment | `912` | `total_monthly_payment` | Presence check; warning if empty |
| Monthly Income | `736` | `monthly_income` | Presence check; warning if empty |
| Down Payment % | `1771` | `down_payment_pct` | Cross-check down_payment_pct × purchase_price vs field 1335 (down payment amount) |
| Down Payment Amount | `1335` | `down_payment_amount` | Verify matches down_payment_pct × purchase_price |
| Rate Is Locked (Y/N) | `2400` | `rate_is_locked` | Authoritative lock indicator; drives date validation logic |
| Lock Date | `761` | `lock_date` | Required if locked; must match last rate set date |
| Lock Period (# of Days) | `432` | `lock_days` | Presence check if locked |
| Lock Expiration Date | `762` | `lock_expires` | Presence check if locked |
| Last Rate Set Date | `3253` | `last_rate_set_date` | Must be today if unlocked; must match lock date if locked |
| Rate Lock Disclosure Date | `3259` | `rate_lock_disclosure_date` | Must be today if locked; can be blank if unlocked |
| Est Closing Date | `763` | `est_closing_date` | Presence check; warning if empty |
| Borrower Est Closing Date | `4114` | `borrower_est_closing_date` | Presence check; warning if empty |
| Secondary Registration | `3941` | `secondary_registration` | Presence check; info flag |
| AMI / Affordable Loan Eligibility | `CX.AMI.ELIGIBILITY` | `ami_eligibility` | Check AMI eligibility flag for grant programs |

**Document Types:**
- **Driver's License**:
  - `dl_expiry`
  - `dl_name`
- **Purchase Agreement**:
  - `purchase_price`
  - `purchase_property_address`

**Business Rules:**
- **All Required Fields Populated** (existence_check): Every mandatory field on the Borrower Summary screen must be populated. If cell phone is empty, copy value from home phone.

- **ID Not Expired** (value_check): Government ID expiration must be >= today.
- **Property Address Consistent** (field_comparison): Property address in Encompass should match the listing and Purchase Contract. Flag any mismatch.

- **Loan Amount Positive** (value_check): Loan amount must be greater than zero.

**Flags — raise when conditions are met:**
- WARNING: "Required Field Empty"
  - Condition: A mandatory Borrower Summary field is blank
  - Remedy: Populate the missing field before proceeding
- WARNING: "Borrower ID Expired"
  - Condition: Government ID expiration date is before today
  - Remedy: Request a valid government-issued ID from the borrower
- WARNING: "Co-Borrower ID Expired"
  - Condition: Co-borrower government ID expiration date is before today
  - Remedy: Request a valid government-issued ID from the co-borrower
- WARNING: "Property Address Mismatch"
  - Condition: Encompass address doesn't match listing or Purchase Contract
  - Remedy: Correct the property address and flag for Lock Desk if needed
- CRITICAL: "Loan Amount Missing"
  - Condition: Loan amount is zero or blank
  - Remedy: Enter the correct loan amount before proceeding
- CRITICAL: "Credit Score Missing"
  - Condition: Middle credit score is blank
  - Remedy: Ensure credit has been pulled and scores are populated in Encompass
- WARNING: "Email Missing"
  - Condition: Borrower email is blank
  - Remedy: Obtain and enter borrower email address
- WARNING: "Co-Borrower Email Missing"
  - Condition: Co-borrower email is blank when co-borrower is present
  - Remedy: Obtain and enter co-borrower email address

**⚠️ Field Updates (writes to Encompass):**
- Field `1490` = `{borrower_home_phone}` (when: borrower_cell_phone is empty)
- Field `4920` = `true` (when: borrower_accept_sms is unchecked)
- Field `4935` = `true` (when: coborrower_accept_sms is unchecked and coborrower is present)

After completing this substep, call:
```
write_todo(step_id="STEP_02", substep_id="2.1", status="completed", notes="<detailed report with every check result, field IDs/values, and flags>")
```

---

## Step Completion

When ALL substeps above are completed:
1. Call `save_step_report(step_name="STEP_02", status="completed", ...)`
2. Call `write_todo(step_id="STEP_02", status="completed")` to advance to the next step
3. Call `write_todo(step_id="STEP_03", status="in_progress")` to start STEP_03 (1003 URLA Lender)

⚠️ You MUST call write_todo to advance — do NOT produce a text-only response.
