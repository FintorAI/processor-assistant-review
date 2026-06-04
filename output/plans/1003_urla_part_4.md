## Purpose

Review and populate 1003 URLA Part 4: downpayment and grant sources (4b/c/d), Declarations (Section 5), ethnicity cross-check against Driver's License, attachment/property type vs listing, estate held, and manner of title.


**NOTE**: Each substep has its own dedicated tool. State (`los_fields`, `doc_fields`, `loan_summary`) is automatically injected.

## Available Tools

| Tool | Purpose |
|------|---------|
| `review_urla_downpayment` | Downpayment Sources (4b, 4c, 4d) |
| `review_urla_declarations` | Declarations (Section 5) |
| `review_urla_ethnicity` | Ethnicity and ID Cross-Check |

## Overview

| Substep | Description | Tool |
|---------|-------------|------|
| 7.1 | Downpayment Sources (4b, 4c, 4d) | `review_urla_downpayment` |
| 7.2 | Declarations (Section 5) | `review_urla_declarations` |
| 7.3 | Ethnicity and ID Cross-Check | `review_urla_ethnicity` |

## Tool Calls

```python
# Substep 7.1 - Downpayment Sources (4b, 4c, 4d)
review_urla_downpayment(loan_guid=loan_id)
# Substep 7.2 - Declarations (Section 5)
review_urla_declarations(loan_guid=loan_id)
# Substep 7.3 - Ethnicity and ID Cross-Check
review_urla_ethnicity(loan_guid=loan_id)
```

---

## Substeps

### Substep 7.1 - Downpayment Sources (4b, 4c, 4d)
**Tool**: `review_urla_downpayment`

Review Sections 4b (downpayment assistance / HELOC — usually N/A for purchase), 4c (rental income — check if applicable), and 4d (grant programs). Check Fannie Mae Additional Data and AMI eligibility for grant programs (e.g. Freddie Mac $2500 grant for borrowers <= 50% AMI).


**LOS Fields (read from state):**

| Encompass Field | Field ID | Key | Purpose |
|-----------------|----------|-----|---------|
| Gift Amount | `231` | `gift_amount` | Gift of cash — verify gift letter and donor details |
| AMI Percentage | `CX.AMI.PERCENTAGE` | `ami_percentage` | If <= 50%, qualifies for Freddie Mac $2500 grant |
| Fannie Mae Additional Data - AMI | `CX.FNMA.ADDITIONAL.DATA` | `fnma_additional_data` | Verify affordable loan eligibility flag |
| Rental Income | `218` | `rental_income` | Check if 4c is applicable |

**Business Rules:**
- **Grant Program Eligibility** (custom): If AMI <= 50%, borrower qualifies for $2500 Freddie Mac grant automatically. Verify Affordable Loan Eligibility = AM 100% flag.

- **Gift Letter Required** (existence_check): If gift amount is populated, verify gift letter and donor details are in file. Send gift letter to borrower if missing.


**Flags — raise when conditions are met:**
- WARNING: "Gift Letter Missing"
  - Condition: Gift amount is populated but gift letter is absent
  - Remedy: Send gift letter to borrower for signature and return
- INFO: "AMI Eligibility Not Set"
  - Condition: AMI percentage is <= 50% but Affordable Loan Eligibility not confirmed
  - Remedy: Confirm AMI = AM 100% in Fannie Mae Additional Data screen

After completing this substep, call:
```
write_todo(step_id="STEP_07", substep_id="7.1", status="completed", notes="<detailed report with every check result, field IDs/values, and flags>")
```

---

### Substep 7.2 - Declarations (Section 5)
**Tool**: `review_urla_declarations`

Review Section 5 — Declarations. Flag any answers that appear incorrect based on known loan file facts. (Example: Borrower rented for 10 months but owned a property 3 years ago — second checkbox should be Yes, not No.)


**LOS Fields (read from state):**

| Encompass Field | Field ID | Key | Purpose |
|-----------------|----------|-----|---------|
| Declaration - Primary Residence Intent (5a-A) | `418` | `declaration_primary_residence` | Verify occupancy intent is correct (Y/N) |
| Declaration 5a(A) — Ownership Interest Past 3 Years | `403` | `declaration_ownership_3yr` | Must reflect borrower's actual history (field 1491 is invalid in batch API) |
| Declaration 5a(A)(1) — Type of Prior Property | `981` | `prior_property_type` | PR/SR/SH/IP — required when 403=Yes; cross-check with loan occupancy |
| Declaration 5a(A)(2) — How Title Was Held (Prior Property) | `1069` | `prior_title_held` | Sole/JointWithSpouse/JointWithOtherThanSpouse — flag joint if no co-borrower |
| Declaration 5a(A) — Co-Borrower Ownership Interest Past 3 Years | `1108` | `coborr_ownership_3yr` | Check co-borrower ownership if co-borrower is present |

**Business Rules:**
- **Declarations Consistent with File** (custom): Cross-check each declaration against known loan file facts (borrower notes, credit report, prior address history). Flag any that appear incorrect.


**Flags — raise when conditions are met:**
- WARNING: "Declaration Appears Incorrect"
  - Condition: A declaration answer is inconsistent with known borrower history
  - Remedy: Review with processor and correct if needed

After completing this substep, call:
```
write_todo(step_id="STEP_07", substep_id="7.2", status="completed", notes="<detailed report with every check result, field IDs/values, and flags>")
```

---

### Substep 7.3 - Ethnicity and ID Cross-Check
**Tool**: `review_urla_ethnicity`

Validate borrower ethnicity selection against Driver's License. Verify attachment type (attached/detached) and property type match the Google listing. Confirm estate held = Fee Simple and manner of title = Sole Ownership (or appropriate for married/co-borrower).


**LOS Fields (read from state):**

| Encompass Field | Field ID | Key | Purpose |
|-----------------|----------|-----|---------|
| Borrower Ethnicity | `1544` | `borrower_ethnicity` | Cross-check against Driver's License |
| Attachment Type (Attached/Detached) | `CX.ATTACHMENT.TYPE` | `attachment_type` | Verify against property listing |
| Property Type | `1041` | `property_type` | Verify against property listing |
| Estate Will Be Held In | `1066` | `estate_held` | Should be Fee Simple for standard purchase. Dropdown field on 1003 URLA Lender form. Values: FeeSimple, Leasehold. Field 1066 = Estate Will Be Held In.
 |
| Manner in Which Title Will Be Held | `33` | `manner_of_title` | Sole Ownership / Joint Tenancy / Tenants in Common / etc. Field 33 = Manner of Title (Borrower Vesting form). URLA.X138 = same data in the 1003 URLA Lender form — always write both together.
 |

**Document Types:**
- **Driver's License**:
  - `dl_ethnicity_indicator`
  - `dl_borrower_name`

**Business Rules:**
- **Attachment Type Consistent with Listing** (custom): Cross-reference attachment type (Attached vs Detached) against listing on Google. Flag mismatch.

- **Manner of Title Appropriate** (custom): If no co-borrower and not married: Sole Ownership. If co-borrower: Joint Tenants / Tenants in Common as appropriate.


**Flags — raise when conditions are met:**
- WARNING: "Ethnicity Mismatch with ID"
  - Condition: Ethnicity on 1003 doesn't match ID documentation
  - Remedy: Verify borrower's self-identified ethnicity and update if needed
- WARNING: "Attachment Type Mismatch with Listing"
  - Condition: Attachment type in Encompass doesn't match property listing
  - Remedy: Correct attachment type based on property listing
- WARNING: "Estate Held Not Fee Simple"
  - Condition: Estate held is not Fee Simple for standard purchase
  - Remedy: Verify estate type is correct for this loan

After completing this substep, call:
```
write_todo(step_id="STEP_07", substep_id="7.3", status="completed", notes="<detailed report with every check result, field IDs/values, and flags>")
```

---

## Step Completion

When ALL substeps above are completed:
1. Call `save_step_report(step_name="STEP_07", status="completed", ...)`
2. Call `write_todo(step_id="STEP_07", status="completed")` to advance to the next step
3. Call `write_todo(step_id="STEP_08", status="in_progress")` to start STEP_08 (Cover Letter)

⚠️ You MUST call write_todo to advance — do NOT produce a text-only response.
