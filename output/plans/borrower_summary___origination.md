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
| Borrower Last Name | `4002` | `borrower_last_name` | Completeness check |
| Borrower Cell Phone | `1480` | `borrower_cell_phone` | If empty, copy from home phone |
| Borrower Home Phone | `66` | `borrower_home_phone` | Fallback for cell phone |
| Borrower Date of Birth | `1402` | `borrower_dob` | Used with ID expiry cross-check |
| Property Street Address | `11` | `property_address` | Verified against online listing and Purchase Contract |
| Property City | `12` | `property_city` | Address verification |
| Property State | `14` | `property_state` | Address verification |
| Property ZIP | `15` | `property_zip` | Address verification |
| Credit Score (Middle) | `1168` | `credit_score` | Confirm credit score is populated |
| Loan Amount | `1109` | `loan_amount` | Confirm loan amount matches Purchase Contract |
| Appraised / Estimated Value | `356` | `appraised_value` | Est -> Appraised value field check |
| Loan Purpose | `19` | `loan_purpose` | Purchase confirmation |
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
- WARNING: "Property Address Mismatch"
  - Condition: Encompass address doesn't match listing or Purchase Contract
  - Remedy: Correct the property address and flag for Lock Desk if needed
- CRITICAL: "Loan Amount Missing"
  - Condition: Loan amount is zero or blank
  - Remedy: Enter the correct loan amount before proceeding

**⚠️ Field Updates (writes to Encompass):**
- Field `1480` = `{borrower_home_phone}` (when: borrower_cell_phone is empty)

After completing this substep, call:
```
write_todo(step_id="STEP_02", substep_id="2.1", status="completed", notes="<detailed report with every check result, field IDs/values, and flags>")
```

---

## Step Completion

When ALL substeps above are completed:
1. Call `save_step_report(step_name="STEP_02", status="completed", ...)`
2. Call `write_todo(step_id="STEP_02", status="completed")` to advance to the next step
3. Call `write_todo(step_id="STEP_03", status="in_progress")` to start STEP_03 (1003 URLA Page 1)

⚠️ You MUST call write_todo to advance — do NOT produce a text-only response.
