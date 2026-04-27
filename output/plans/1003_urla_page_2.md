## Purpose

Review and populate 1003 URLA Page 2: employment (Section 1b VOE cross-check), employment history with gap rules, and other income (Section 1e).


**NOTE**: Each substep has its own dedicated tool. State (`los_fields`, `doc_fields`, `loan_summary`) is automatically injected.

## Available Tools

| Tool | Purpose |
|------|---------|
| `review_urla_employment` | Employment Verification (1b VOE) |
| `review_urla_other_income` | Other Income (1e) |

## Overview

| Substep | Description | Tool |
|---------|-------------|------|
| 4.1 | Employment Verification (1b VOE) | `review_urla_employment` |
| 4.2 | Other Income (1e) | `review_urla_other_income` |

## Tool Calls

```python
# Substep 4.1 - Employment Verification (1b VOE)
review_urla_employment(loan_guid=loan_id)
# Substep 4.2 - Other Income (1e)
review_urla_other_income(loan_guid=loan_id)
```

---

## Substeps

### Substep 4.1 - Employment Verification (1b VOE)
**Tool**: `review_urla_employment`

Cross-check employment fields against VOE: employer name, original hire date, base pay (Gross Monthly Income → Base / Month). Cross-reference employer address via Google. Flag discrepancies. Check employment history — if current employment < 2 years, review prior history for gaps and explanations.


**LOS Fields (read from state):**

| Encompass Field | Field ID | Key | Purpose |
|-----------------|----------|-----|---------|
| Employer Name | `1169` | `employer_name` | Match against VOE |
| Employer Address | `1182` | `employer_address` | Cross-reference via Google |
| Employment Start Date (Hire Date) | `1068` | `employment_start_date` | Match against VOE original hire date |
| Base Monthly Income | `1072` | `base_monthly_income` | Match against VOE compensation base pay |
| Job Title | `1286` | `job_title` | Completeness (N/A is acceptable) |
| Years in Profession | `1073` | `years_in_profession` | Total years — if < 2, check history |

**Document Types:**
- **VOE**:
  - `voe_employer_name`
  - `voe_hire_date`
  - `voe_base_pay`
  - `voe_employer_address`

**Business Rules:**
- **Employer Name Matches VOE** (field_comparison): Employer name in Encompass must match the VOE employer name.
- **Hire Date Matches VOE** (field_comparison): Employment start date must match VOE original hire date.
- **Base Pay Matches VOE** (field_comparison): Gross Monthly Base / Month must match VOE compensation base pay.
- **Employment History Gap Check** (custom): If current employment start date shows < 2 years: review prior employment entries. If FHA and gap < 6 months, require written explanation. If gap > 6 months, document 2-year history before the gap and income continuity.


**Flags — raise when conditions are met:**
- WARNING: "Employer Name Mismatch (VOE vs 1003)"
  - Condition: Employer name in Encompass doesn't match VOE
  - Remedy: Correct employer name to match VOE
- WARNING: "Hire Date Mismatch (VOE vs 1003)"
  - Condition: Hire date in Encompass doesn't match VOE original hire date
  - Remedy: Correct hire date to match VOE
- WARNING: "Base Pay Mismatch (VOE vs 1003)"
  - Condition: Base monthly income doesn't match VOE base pay rate
  - Remedy: Reconcile income figure with VOE
- WARNING: "Employment Gap Requires Explanation"
  - Condition: Employment gap < 6 months with FHA loan, or gap > 6 months with any loan type

  - Remedy: Obtain written explanation letter from borrower for employment gap

**Rule Modifiers (conditional behavior based on loan profile):**
- **When `loan_type` = `FHA`** → ADD: FHA: if employment gap < 6 months, require written explanation from borrower. If gap > 6 months, require 2-year history before the gap.

  - Rule: FHA Employment Gap Rules — For FHA loans: gap < 6 months requires explanation letter; gap > 6 months requires documented 2-year history before gap.

  - Flag (warning): FHA Employment Gap - Explanation Required — Obtain borrower explanation letter for employment gap
  - Source: notes.txt:44-48

After completing this substep, call:
```
write_todo(step_id="STEP_04", substep_id="4.1", status="completed", notes="<detailed report with every check result, field IDs/values, and flags>")
```

---

### Substep 4.2 - Other Income (1e)
**Tool**: `review_urla_other_income`

Review Section 1e — Other Sources of Income (alimony, dividend stocks, Social Security, etc.). Verify amounts are populated and documented.


**LOS Fields (read from state):**

| Encompass Field | Field ID | Key | Purpose |
|-----------------|----------|-----|---------|
| Other Income Type | `172` | `other_income_type` | Type of other income (alimony, dividends, etc.) |
| Other Income Amount (Monthly) | `173` | `other_income_amount` | Monthly other income amount |

**Business Rules:**
- **Other Income Documented** (existence_check): If other income is present in Encompass, verify it is documented in the file. Alimony requires court order; dividends require brokerage statements.


**Flags — raise when conditions are met:**
- WARNING: "Other Income Not Documented"
  - Condition: Other income amount is populated but supporting documents are missing
  - Remedy: Obtain supporting documentation for other income type

After completing this substep, call:
```
write_todo(step_id="STEP_04", substep_id="4.2", status="completed", notes="<detailed report with every check result, field IDs/values, and flags>")
```

---

## Step Completion

When ALL substeps above are completed:
1. Call `save_step_report(step_name="STEP_04", status="completed", ...)`
2. Call `write_todo(step_id="STEP_04", status="completed")` to advance to the next step
3. Call `write_todo(step_id="STEP_05", status="in_progress")` to start STEP_05 (1003 URLA Part 3)

⚠️ You MUST call write_todo to advance — do NOT produce a text-only response.
