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
| 3.1 | Review 1003 URLA Page 1 | `review_urla_page1` |

## Tool Calls

```python
# Substep 3.1 - Review 1003 URLA Page 1
review_urla_page1(loan_guid=loan_id)
```

---

## Substeps

### Substep 3.1 - Review 1003 URLA Page 1
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
write_todo(step_id="STEP_03", substep_id="3.1", status="completed", notes="<detailed report with every check result, field IDs/values, and flags>")
```

---

## Step Completion

When ALL substeps above are completed:
1. Call `save_step_report(step_name="STEP_03", status="completed", ...)`
2. Call `write_todo(step_id="STEP_03", status="completed")` to advance to the next step
3. Call `write_todo(step_id="STEP_04", status="in_progress")` to start STEP_04 (1003 URLA Page 2)

⚠️ You MUST call write_todo to advance — do NOT produce a text-only response.
