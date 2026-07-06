## Purpose

Review the Flood Certificate and insurance documents against Encompass. Confirms the flood cert applicant/property match the loan (checklist 12 #2), and confirms the flood zone designation — classifying SFHA (A/V) vs non-hazard (X), checking that a flood policy is on file when in an SFHA, and reconciling the extracted zone against the Encompass Flood Zone on the Flood Information form (field 541) with auto-correct on mismatch/blank (12 #4). Hazard-insurance verification (checklist section 13) is scaffolded here for future build-out. Read-only against borrower data; the only write is the flood zone (541) when the cert maps to a recognized FEMA designation.


**⚠️ This step may WRITE to Encompass.** Substeps that write are marked below.

**NOTE**: Each substep has its own dedicated tool. State (`los_fields`, `doc_fields`, `loan_summary`) is automatically injected.

## Available Tools

| Tool | Purpose |
|------|---------|
| `review_flood_hazard_insurance` | Review Flood & Hazard Insurance |

## Overview

| Substep | Description | Tool |
|---------|-------------|------|
| 8.1 | Review Flood & Hazard Insurance | `review_flood_hazard_insurance` |

## Tool Calls

```python
# Substep 8.1 - Review Flood & Hazard Insurance
# ⚠️ This substep WRITES to Encompass
review_flood_hazard_insurance(loan_guid=loan_id)
```

---

## Substeps

### Substep 8.1 - Review Flood & Hazard Insurance
**Tool**: `review_flood_hazard_insurance`

Cross-reference the Flood Certificate against Encompass: applicant name and property address vs the subject (12 #2), and the flood zone designation vs Encompass field 541 with SFHA insurance-required checks (12 #4). Auto-correct field 541 from the cert only when the extracted zone maps to a value the field-541 dropdown accepts; otherwise warn and leave for manual entry.


**LOS Fields (read from state):**

| Encompass Field | Field ID | Key | Purpose |
|-----------------|----------|-----|---------|
| Property Street Address | `11` | `property_address` | Subject address for flood cert property match (12 |
| Property Street Address (URLA Lender — editable) | `URLA.X73` | `property_address_urla` | Editable subject street address fallback |
| Property City | `12` | `property_city` | Subject address component |
| Property State | `14` | `property_state` | Subject address component |
| Property ZIP | `15` | `property_zip` | Subject address component |
| Borrower Last Name | `4002` | `borrower_last_name` | Match against flood cert borrower name (12 |
| Co-Borrower Last Name | `4006` | `coborrower_last_name` | Match against flood cert borrower name (12 |
| Flood Zone (Flood Information form) | `541` | `los_flood_zone` | Checklist 12 #4 — compare/auto-correct against the flood certificate's extracted zone; only written when the zone maps to a field-541 dropdown value.
 |
| Flood Certification Number (Cert | `2363` | `los_flood_cert_number` | Flood Information form Cert |

**Document Types:**
- **Flood Certificate**:
  - `property_address`
  - `borrower_name`
  - `flood_zone`
  - `in_sfha`
- **Flood Insurance**:
  - `flood_policy_number`
  - `flood_annual_premium`
- **Evidence of Insurance**:
  - `insured_location`

**Business Rules:**
- **Flood Cert Applicant/Property Match** (field_comparison): The flood certificate borrower name must overlap the Encompass applicant surname(s), and the flood cert property address must match the USPS-validated subject address. Warn on mismatch; never auto-correct.

- **Flood Zone Designation** (field_comparison): Classify the flood cert zone (SFHA A/V vs non-hazard X). When in an SFHA, a flood policy must be on file. Reconcile the extracted zone against Encompass field 541 — auto-correct on mismatch/blank only when the zone maps to a recognized FEMA designation.


**Flags — raise when conditions are met:**
- WARNING: "Flood Cert Applicant Mismatch"
  - Condition: Flood cert borrower name does not match the Encompass applicant(s)
  - Remedy: Verify the flood certificate was ordered for the correct borrower / loan
- WARNING: "Flood Zone Mismatch"
  - Condition: Flood cert zone does not match Encompass field 541 and is not a recognized designation
  - Remedy: Verify the correct flood zone and update Encompass field 541 manually
- WARNING: "Flood Insurance Required (SFHA)"
  - Condition: Property is in a Special Flood Hazard Area but no flood policy is on file
  - Remedy: Obtain a flood insurance policy meeting NFIP coverage requirements

**⚠️ Field Updates (writes to Encompass):**
- Field `541` = `{flood_zone}` (when: Flood cert zone maps to a recognized FEMA designation and 541 is blank or mismatched)

After completing this substep, call:
```
write_todo(step_id="STEP_08", substep_id="8.1", status="completed", notes="<detailed report with every check result, field IDs/values, and flags>")
```

---

## Step Completion

When ALL substeps above are completed:
1. Call `save_step_report(step_name="STEP_08", status="completed", ...)`
2. Call `write_todo(step_id="STEP_08", status="completed")` to advance to the next step
3. Call `write_todo(step_id="STEP_09", status="in_progress")` to start STEP_09 (Cover Letter)

⚠️ You MUST call write_todo to advance — do NOT produce a text-only response.
