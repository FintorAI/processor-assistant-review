## Purpose

Review and populate 1003 URLA Page 1. Many fields overlap with the Origination screen (Step 2). Confirm borrower personal information, property type, loan purpose, and occupancy match across both screens.


**NOTE**: Each substep has its own dedicated tool. State (`los_fields`, `doc_fields`, `loan_summary`) is automatically injected.

## Available Tools

| Tool | Purpose |
|------|---------|
| `review_urla_page1` | Review 1003 URLA Page 1 |

## Overview

| Substep | Description | Tool |
|---------|-------------|------|
| 4.1 | Review 1003 URLA Page 1 | `review_urla_page1` |

## Tool Calls

```python
# Substep 4.1 - Review 1003 URLA Page 1
review_urla_page1(loan_guid=loan_id)
```

---

## Substeps

### Substep 4.1 - Review 1003 URLA Page 1
**Tool**: `review_urla_page1`

Check all fields on 1003 URLA Page 1 for completeness and consistency with the Borrower Summary - Origination screen. Confirm borrower name, address, SSN, loan purpose, property type, and occupancy. Flag any mismatches.


**LOS Fields (read from state):**

| Encompass Field | Field ID | Key | Purpose |
|-----------------|----------|-----|---------|
| Borrower First Name | `4000` | `borrower_first_name` | Confirm matches Origination screen |
| Borrower Middle Name | `4001` | `borrower_middle_name` | Completeness check |
| Borrower Last Name | `4002` | `borrower_last_name` | Confirm matches Origination screen |
| Borrower SSN | `65` | `borrower_ssn` | Verify SSN is populated |
| Borrower DOB | `1402` | `borrower_dob` | Completeness |
| Borrower Current Street Address | `35` | `borrower_current_address` | Current mailing address |
| Subject Property Address | `11` | `property_address` | Matches Origination screen |
| Loan Purpose | `19` | `loan_purpose` | Purchase / Refinance |
| Property Type | `1041` | `property_type` | SFR / Condo / etc. |
| Occupancy | `1811` | `occupancy` | Primary / Secondary / Investment |
| Borrower Citizenship | `URLA.X1` | `borrower_citizenship` | Must be USCitizen / PermanentResidentAlien / NonPermanentResidentAlien; never auto-populated |
| Co-Borrower Citizenship | `URLA.X2` | `coborrower_citizenship` | Same 3-value check as borrower; only checked if co-borrower present |
| Borrower Present Street Address | `FR0126` | `borr_present_addr` | Current address presence check |
| Borrower Present City | `FR0106` | `borr_present_city` | Current address presence check |
| Borrower Present State | `FR0107` | `borr_present_state` | Current address presence check |
| Borrower Present Zip | `FR0108` | `borr_present_zip` | Current address presence check |
| Borrower Years at Current Address | `FR0112` | `borr_present_yrs` | If < 2 years, former address is required |
| Borrower Months at Current Address | `FR0124` | `borr_present_mos` | Combined with years to determine former address requirement |
| Borrower Current Housing Type | `FR0115` | `borr_housing_type` | If Rent, housing amount must be populated |
| Borrower Current Housing Expense Amount | `FR0116` | `borr_housing_amount` | Required if housing type = Rent |
| Borrower Former Street Address | `FR0326` | `borr_former_addr` | Required if < 2 years at current address |
| Borrower Former City | `FR0306` | `borr_former_city` | Required if < 2 years at current address |
| Borrower Former State | `FR0307` | `borr_former_state` | Required if < 2 years at current address |
| Borrower Former Zip | `FR0308` | `borr_former_zip` | Required if < 2 years at current address |
| Borrower Former Housing Type | `FR0315` | `borr_former_housing_type` | If Rent, former housing amount should be populated |
| Borrower Former Housing Expense Amount | `FR0316` | `borr_former_housing_amount` | Required if former housing type = Rent |
| Borrower Former Address Does Not Apply | `URLA.X265` | `borr_former_addr_does_not_apply` | Auto-checked when borrower has >= 2 years at current address |
| Co-Borrower Years at Current Address | `FR0212` | `coborr_present_yrs` | If < 2 years, co-borrower former address is required |
| Co-Borrower Months at Current Address | `FR0224` | `coborr_present_mos` | Combined with years to determine former address requirement |
| Co-Borrower Current Housing Type | `FR0415` | `coborr_housing_type` | If Rent, housing amount must be populated |
| Co-Borrower Current Housing Expense Amount | `FR0416` | `coborr_housing_amount` | Required if housing type = Rent |
| Co-Borrower Former Street Address | `FR0426` | `coborr_former_addr` | Required if co-borrower < 2 years at current address |
| Co-Borrower Former City | `FR0406` | `coborr_former_city` | Required if co-borrower < 2 years at current address |
| Co-Borrower Former State | `FR0407` | `coborr_former_state` | Required if co-borrower < 2 years at current address |
| Co-Borrower Former Zip | `FR0408` | `coborr_former_zip` | Required if co-borrower < 2 years at current address |
| Co-Borrower Former Address Does Not Apply | `URLA.X266` | `coborr_former_addr_does_not_apply` | Auto-checked when co-borrower has >= 2 years at current address |
| Borrower Mailing Address Same as Present | `1819` | `borr_mailing_same_as_present` | Must be Y; auto-corrected if not set |
| Co-Borrower Mailing Address Same as Present | `1820` | `coborr_mailing_same_as_present` | Must be Y if co-borrower present; auto-corrected if not set |
| Borrower Military Service Indicator | `URLA.X13` | `borr_military_service` | Must be Yes for VA loans; sub-options required |
| Borrower Currently Serving on Active Duty | `URLA.X123` | `borr_military_active_duty` | VA sub-option |
| Borrower Retired/Discharged/Separated | `URLA.X124` | `borr_military_retired` | VA sub-option |
| Borrower Non-Activated Reserve/National Guard | `URLA.X125` | `borr_military_reserve` | VA sub-option |
| Borrower Surviving Spouse | `URLA.X19` | `borr_military_surviving_spouse` | VA sub-option |
| Co-Borrower Military Service Indicator | `URLA.X14` | `coborr_military_service` | Must be Yes for VA loans if co-borrower present |
| Co-Borrower Currently Serving on Active Duty | `URLA.X126` | `coborr_military_active_duty` | VA sub-option |
| Co-Borrower Retired/Discharged/Separated | `URLA.X127` | `coborr_military_retired` | VA sub-option |
| Co-Borrower Non-Activated Reserve/National Guard | `URLA.X128` | `coborr_military_reserve` | VA sub-option |
| Co-Borrower Surviving Spouse | `URLA.X20` | `coborr_military_surviving_spouse` | VA sub-option |
| Borrower Language Preference | `URLA.X21` | `borr_language_preference` | Default EnglishIndicator; flag if blank for processor verification |
| Co-Borrower Language Preference | `URLA.X22` | `coborr_language_preference` | Default EnglishIndicator if co-borrower present; flag if blank |
| Number of Dependents | `53` | `borrower_dependents_count` | Info flag |
| Dependents Ages | `54` | `borrower_dependent_ages` | Present if count > 0 |
| Co-Borrower Number of Dependents | `85` | `coborr_dependents_count` | Info flag |
| Co-Borrower Dependents Ages | `86` | `coborr_dependents_ages` | Present if count > 0 |
| Subject Property Number of Units | `16` | `property_units` | Presence check; warning if empty |

**Business Rules:**
- **Borrower Info Consistent with Origination** (field_comparison): First name, last name, property address, loan purpose, property type, and occupancy must match the Borrower Summary screen values from Step 2.

- **SSN Populated** (existence_check): Borrower SSN must not be blank.

**Flags — raise when conditions are met:**
- WARNING: "URLA Page 1 Field Mismatch with Origination"
  - Condition: Field value on URLA Page 1 differs from Borrower Summary screen
  - Remedy: Reconcile the field — update the incorrect screen
- CRITICAL: "Borrower SSN Missing"
  - Condition: Borrower SSN is blank on Page 1
  - Remedy: Populate SSN before proceeding

After completing this substep, call:
```
write_todo(step_id="STEP_04", substep_id="4.1", status="completed", notes="<detailed report with every check result, field IDs/values, and flags>")
```

---

## Step Completion

When ALL substeps above are completed:
1. Call `save_step_report(step_name="STEP_04", status="completed", ...)`
2. Call `write_todo(step_id="STEP_04", status="completed")` to advance to the next step
3. Call `write_todo(step_id="STEP_05", status="in_progress")` to start STEP_05 (1003 URLA Page 2)

⚠️ You MUST call write_todo to advance — do NOT produce a text-only response.
