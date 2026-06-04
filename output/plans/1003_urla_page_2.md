## Purpose

Review and populate 1003 URLA Part 2: employment (Section 2b VOE cross-check), employment history with gap rules, and other income (Section 2e).


**⚠️ This step may WRITE to Encompass.** Substeps that write are marked below.

**NOTE**: Each substep has its own dedicated tool. State (`los_fields`, `doc_fields`, `loan_summary`) is automatically injected.

## Available Tools

| Tool | Purpose |
|------|---------|
| `review_urla_employment` | Employment Verification (2b VOE) |
| `review_urla_other_income` | Other Income (2e) |

## Overview

| Substep | Description | Tool |
|---------|-------------|------|
| 5.1 | Employment Verification (2b VOE) | `review_urla_employment` |
| 5.2 | Other Income (2e) | `review_urla_other_income` |

## Tool Calls

```python
# Substep 5.1 - Employment Verification (2b VOE)
# ⚠️ This substep WRITES to Encompass
review_urla_employment(loan_guid=loan_id)
# Substep 5.2 - Other Income (2e)
review_urla_other_income(loan_guid=loan_id)
```

---

## Substeps

### Substep 5.1 - Employment Verification (2b VOE)
**Tool**: `review_urla_employment`

Cross-check employment fields against VOE: employer name, original hire date, base pay (Gross Monthly Income → Base / Month). Cross-reference employer address via Google. Flag discrepancies. Check employment history — if current employment < 2 years, review prior history for gaps and explanations.


**LOS Fields (read from state):**

| Encompass Field | Field ID | Key | Purpose |
|-----------------|----------|-----|---------|
| Employment 1 — Type (Current / Prior) | `BE0109` | `be01_employment_type` | Determines if entry 1 is current or prior employer; drives cross-check logic |
| Employment 1 — VOE Is For (Borrower / Co-Borrower) | `BE0108` | `be01_voe_is_for` | Identifies which borrower this employment entry belongs to |
| Employment 1 — Foreign Address Checkbox | `BE0180` | `be01_foreign_address` | Flags if employer is at a foreign address |
| Employment 1 — Employer Name | `BE0102` | `be01_employer_name` | Cross-check against VOE current_employer_name |
| Employment 1 — Employer Phone | `BE0117` | `be01_employer_phone` | Presence check; cross-check against VOE current_employer_phone |
| Employment 1 — Employer Street Address | `BE0160` | `be01_employer_street` | Cross-reference employer address |
| Employment 1 — Unit Type (Suite / Apt / etc.) | `BE0158` | `be01_employer_unit_type` | Address unit type |
| Employment 1 — Unit Number | `BE0159` | `be01_employer_unit_number` | Address unit number |
| Employment 1 — Employer City | `BE0105` | `be01_employer_city` | Cross-reference employer city |
| Employment 1 — Employer State | `BE0106` | `be01_employer_state` | Cross-reference employer state |
| Employment 1 — Employer Zip | `BE0107` | `be01_employer_zip` | Cross-reference employer zip |
| Employment 1 — Date Hired | `BE0151` | `be01_date_hired` | Cross-check against VOE current_original_hire_date |
| Employment 1 — Date Terminated | `BE0114` | `be01_date_terminated` | Null if current employer; populated for prior employment |
| Employment 1 — Years in This Job | `BE0113` | `be01_years_in_job` | Cross-check against VOE current_years_in_job |
| Employment 1 — Months in This Job | `BE0133` | `be01_months_in_job` | Combined with years to determine < 2 year rule |
| Employment 1 — Years in Line of Work | `BE0116` | `be01_years_in_line_of_work` | Total years in profession; cross-check against VOE |
| Employment 1 — Months in Line of Work | `BE0152` | `be01_months_in_line_of_work` | Combined with years in line of work |
| Employment 1 — Monthly Base Pay | `BE0119` | `be01_monthly_base_pay` | Cross-check against VOE current_monthly_base_pay |
| Employment 1 — Position / Title / Type of Business | `BE0110` | `be01_position_title` | Cross-check against VOE current_position_title |
| Employment 1 — Print Authorization on Signature Line | `BE0236` | `be01_authorization_printed` | Must be checked; prints borrower authorization reference on signature line |
| Employment 2 — Type (Current / Prior) | `BE0209` | `be02_employment_type` | Determines if entry 2 is current or prior employer |
| Employment 2 — VOE Is For (Borrower / Co-Borrower) | `BE0208` | `be02_voe_is_for` | Identifies which borrower this employment entry belongs to |
| Employment 2 — Foreign Address Checkbox | `BE0280` | `be02_foreign_address` | Flags if employer is at a foreign address |
| Employment 2 — Employer Name | `BE0202` | `be02_employer_name` | Cross-check against VOE previous_employer_name if type = Prior |
| Employment 2 — Employer Phone | `BE0217` | `be02_employer_phone` | Presence check |
| Employment 2 — Employer Street Address | `BE0260` | `be02_employer_street` | Cross-reference employer address |
| Employment 2 — Unit Type | `BE0258` | `be02_employer_unit_type` | Address unit type |
| Employment 2 — Unit Number | `BE0259` | `be02_employer_unit_number` | Address unit number |
| Employment 2 — Employer City | `BE0205` | `be02_employer_city` | Cross-reference employer city |
| Employment 2 — Employer State | `BE0206` | `be02_employer_state` | Cross-reference employer state |
| Employment 2 — Employer Zip | `BE0207` | `be02_employer_zip` | Cross-reference employer zip |
| Employment 2 — Date Hired | `BE0251` | `be02_date_hired` | Cross-check against VOE previous_original_hire_date if prior |
| Employment 2 — Date Terminated | `BE0214` | `be02_date_terminated` | Populated if prior employer; gap between terminated and be01_date_hired is the gap |
| Employment 2 — Years in This Job | `BE0213` | `be02_years_in_job` | Duration at prior employer |
| Employment 2 — Months in This Job | `BE0233` | `be02_months_in_job` | Duration at prior employer |
| Employment 2 — Years in Line of Work | `BE0216` | `be02_years_in_line_of_work` | Total years in profession |
| Employment 2 — Months in Line of Work | `BE0252` | `be02_months_in_line_of_work` | Total months in profession |
| Employment 2 — Position / Title / Type of Business | `BE0210` | `be02_position_title` | Position or title at second employer |
| Employment 2 — Monthly Base Pay | `BE0219` | `be02_monthly_base_pay` | Cross-check against VOE previous_monthly_base_pay if prior |
| Employment 3 — Type (Current / Prior) | `BE0309` | `be03_employment_type` | Determines if entry 3 is current or prior employer |
| Employment 3 — VOE Is For (Borrower / Co-Borrower) | `BE0308` | `be03_voe_is_for` | Identifies which borrower this employment entry belongs to |
| Employment 3 — Employer Name | `BE0302` | `be03_employer_name` | Third employment entry employer name |
| Employment 3 — Date Hired | `BE0351` | `be03_date_hired` | Third employment entry hire date |
| Employment 3 — Date Terminated | `BE0314` | `be03_date_terminated` | Third employment entry termination date (gap calculation) |
| Employment 3 — Years in This Job | `BE0313` | `be03_years_in_job` | Duration at third employer |
| Employment 3 — Months in This Job | `BE0333` | `be03_months_in_job` | Duration at third employer |
| Employment 3 — Position / Title / Type of Business | `BE0310` | `be03_position_title` | Position or title at third employer |
| Employment 3 — Monthly Base Pay | `BE0319` | `be03_monthly_base_pay` | Income at third employer |
| Borrower — Base Monthly Income (Section 1b) | `FE0119` | `borr_base_monthly_income` | Total base monthly income for borrower per Section 1b. Must be populated unless URLA.X199 (1b does not apply) is checked.
 |
| Co-Borrower — Base Monthly Income (Section 1b) | `FE0219` | `coborr_base_monthly_income` | Total base monthly income for co-borrower per Section 1b. Must be populated unless URLA.X200 (1b does not apply) is checked.
 |
| Borrower — Section 1b Does Not Apply | `URLA.X199` | `borr_1b_dna` | If checked, borrower has no employee/employer income — FE0119 blank is acceptable. |
| Co-Borrower — Section 1b Does Not Apply | `URLA.X200` | `coborr_1b_dna` | If checked, co-borrower has no employee/employer income — FE0219 blank is acceptable. |
| Borrower 1c — Total Gross Income | `FE0112` | `borr_1c_total_gross_income` | Surfaced as info flag when 1c is populated. |
| Borrower 1c — Monthly Income (or Loss) | `FE0156` | `borr_1c_monthly_income` | Surfaced as info flag alongside total gross income. |
| Co-Borrower 1c — Total Gross Income | `FE0212` | `coborr_1c_total_gross_income` | Surfaced as info flag when co-borrower 1c is populated. |
| Co-Borrower 1c — Monthly Income (or Loss) | `FE0256` | `coborr_1c_monthly_income` | Surfaced as info flag alongside total gross income. |
| Borrower 1c — Employer or Business Name | `FE0302` | `borr_1c_employer_name` | Presence indicates borrower has self/additional employment. Gate for DNA check. |
| Borrower 1c — Start Date | `FE0351` | `borr_1c_start_date` | Start date of additional/self employment |
| Borrower 1c — Years in Line of Work | `FE0316` | `borr_1c_years_in_line` | Duration check |
| Borrower 1c — Months in Line of Work | `FE0352` | `borr_1c_months_in_line` | Duration check |
| Co-Borrower 1c — Employer or Business Name | `FE0402` | `coborr_1c_employer_name` | Presence indicates co-borrower has self/additional employment. Gate for DNA check. |
| Co-Borrower 1c — Start Date | `FE0451` | `coborr_1c_start_date` | Start date of additional/self employment |
| Co-Borrower 1c — Years in Line of Work | `FE0416` | `coborr_1c_years_in_line` | Duration check |
| Co-Borrower 1c — Months in Line of Work | `FE0452` | `coborr_1c_months_in_line` | Duration check |
| Borrower — Section 1c Does Not Apply | `URLA.X201` | `borr_1c_dna` | If checked, borrower has no additional/self-employment income — 1c section blank is acceptable. |
| Co-Borrower — Section 1c Does Not Apply | `URLA.X202` | `coborr_1c_dna` | If checked, co-borrower has no additional/self-employment income. |
| Borrower 1d — Total Gross Income | `FE0312` | `borr_1d_total_gross_income` | Surfaced as info flag when 1d is populated. |
| Borrower 1d — Monthly Income (or Loss) | `FE0356` | `borr_1d_monthly_income` | Surfaced as info flag alongside total gross income. |
| Co-Borrower 1d — Total Gross Income | `FE0412` | `coborr_1d_total_gross_income` | Surfaced as info flag when co-borrower 1d is populated. |
| Co-Borrower 1d — Monthly Income (or Loss) | `FE0456` | `coborr_1d_monthly_income` | Surfaced as info flag alongside total gross income. |
| Borrower 1d — Employer or Business Name | `FE0502` | `borr_1d_employer_name` | Presence indicates prior employer. Gate for DNA check. |
| Borrower 1d — Start Date | `FE0551` | `borr_1d_start_date` | Prior employment start date |
| Borrower 1d — End Date | `FE0514` | `borr_1d_end_date` | Prior employment end date |
| Co-Borrower 1d — Employer or Business Name | `FE0602` | `coborr_1d_employer_name` | Presence indicates prior employer. Gate for DNA check. |
| Co-Borrower 1d — Start Date | `FE0651` | `coborr_1d_start_date` | Prior employment start date |
| Co-Borrower 1d — End Date | `FE0614` | `coborr_1d_end_date` | Prior employment end date |
| Borrower — Section 1d Does Not Apply | `URLA.X203` | `borr_1d_dna` | If checked, borrower has no previous employment — 1d section blank is acceptable. |
| Co-Borrower — Section 1d Does Not Apply | `URLA.X204` | `coborr_1d_dna` | If checked, co-borrower has no previous employment. |

**Document Types:**
- **VOE** (ALL COPIES):
  - `current_employer_name`
  - `current_position_title`
  - `current_employer_phone`
  - `current_employer_street`
  - `current_employer_city`
  - `current_employer_state`
  - `current_employer_zip`
  - `current_original_hire_date`
  - `current_date_terminated`
  - `current_years_in_job`
  - `current_months_in_job`
  - `current_years_in_line_of_work`
  - `current_months_in_line_of_work`
  - `current_monthly_base_pay`
  - `previous_employer_name`
  - `previous_position_title`
  - `previous_employer_phone`
  - `previous_employer_street`
  - `previous_employer_city`
  - `previous_employer_state`
  - `previous_employer_zip`
  - `previous_original_hire_date`
  - `previous_date_terminated`
  - `previous_years_in_job`
  - `previous_months_in_job`
  - `previous_monthly_base_pay`
  - `borrower_name`
  - `authorization_printed`
  - `verification_date`
- **Paystubs** (ALL COPIES):
  - `paystub_employer_name`
  - `paystub_gross_pay`

**Business Rules:**
- **BE0109 Employment Type Populated** (existence_check): For each BE01/BE02/BE03 entry — skip if all fields empty (no entry). If any employer field is populated, BE0109 (employment type) must be either "Current" or "Prior". Entries with BE0109 = "Current" are cross-checked against VOE current_ fields; entries with BE0109 = "Prior" against previous_ fields.

- **VOE Is For — Borrower Identified** (existence_check): BE0108 (VOE is for) must be Borrower or Co-Borrower. Verify each employment entry is attributed to the correct borrower.

- **Authorization Checkbox Checked** (existence_check): BE0236 must be checked for the current employment entry. This prints "see attached borrower's authorization" on the signature line.

- **Employer Name Matches VOE** (field_comparison): For each populated employment entry: LOS employer name (be0x_employer_name) must match VOE current_employer_name (if Current) or previous_employer_name (if Prior).

- **Hire Date Matches VOE** (field_comparison): LOS date hired (be0x_date_hired) must match VOE current_original_hire_date (if Current) or previous_original_hire_date (if Prior).

- **Monthly Base Pay Matches VOE** (field_comparison): LOS monthly base pay (be0x_monthly_base_pay) must match VOE current_monthly_base_pay (if Current) or previous_monthly_base_pay (if Prior).

- **Date Terminated — Current vs Prior** (custom): For entries with BE0109 = "Current": date terminated (BE0114) must be null/empty. For entries with BE0109 = "Prior": date terminated must be populated. If Prior and date terminated is empty, flag as warning.

- **Base Monthly Income Populated (Section 1b)** (existence_check): FE0119 (borrower base monthly income) must be populated unless URLA.X201 is checked. FE0219 (co-borrower) must be populated unless URLA.X202 is checked or no co-borrower exists.

- **Employment History Gap Check** (custom): Calculate total time at current employer using be0x_years_in_job + be0x_months_in_job. If current employment < 2 years total: review prior employment entries for continuity and gaps. FHA: gap < 6 months requires explanation; gap > 6 months requires 2-year documented history before the gap.


**Flags — raise when conditions are met:**
- BLOCKING: "Employment Type Not Set (BE0109)"
  - Condition: Employment entry has populated employer name but BE0109 is empty
  - Remedy: Set employment type to Current or Prior in Encompass
- WARNING: "Employer Name Mismatch (VOE vs 1003)"
  - Condition: Employer name in Encompass doesn't match VOE employer name for the same entry type
  - Remedy: Correct employer name to match VOE
- WARNING: "Hire Date Mismatch (VOE vs 1003)"
  - Condition: Date hired in Encompass doesn't match VOE original hire date
  - Remedy: Correct hire date to match VOE
- WARNING: "Monthly Base Pay Mismatch (VOE vs 1003)"
  - Condition: Monthly base pay in Encompass doesn't match VOE
  - Remedy: Reconcile income figure with VOE
- WARNING: "Date Terminated Missing for Prior Employer"
  - Condition: Employment entry is marked Prior but date terminated is empty
  - Remedy: Enter termination date in Encompass for prior employer
- WARNING: "Date Terminated Populated for Current Employer"
  - Condition: Employment entry is marked Current but date terminated is populated
  - Remedy: Clear termination date for current employer
- WARNING: "Authorization Checkbox Not Checked (BE0236)"
  - Condition: BE0236 is unchecked for current employment entry
  - Remedy: Check the "Print see attached borrower's authorization" box in Encompass
- WARNING: "Employment Gap Requires Explanation"
  - Condition: Current employment < 2 years and gap between prior termination and current hire > 30 days

  - Remedy: Obtain written explanation letter from borrower for employment gap
- WARNING: "Borrower Base Monthly Income Missing (FE0119)"
  - Condition: FE0119 (borrower base monthly income) is empty and URLA.X201 (does not apply) is not checked

  - Remedy: Enter the borrower's base monthly income in Section 1b or check the Does Not Apply box
- WARNING: "Co-Borrower Base Monthly Income Missing (FE0219)"
  - Condition: FE0219 (co-borrower base monthly income) is empty and URLA.X202 (does not apply) is not checked and a co-borrower is present on the loan

  - Remedy: Enter the co-borrower's base monthly income in Section 1b or check the Does Not Apply box

**⚠️ Field Updates (writes to Encompass):**
- Field `URLA.X201` = `true` (when: Section 2c (borrower additional/self employment, FE0302) is empty and URLA.X201 is not already checked
)
- Field `URLA.X202` = `true` (when: Section 2c (co-borrower additional/self employment, FE0402) is empty and URLA.X202 is not already checked and a co-borrower is present
)
- Field `URLA.X203` = `true` (when: Section 2d (borrower previous employment, FE0502) is empty and URLA.X203 is not already checked
)
- Field `URLA.X204` = `true` (when: Section 2d (co-borrower previous employment, FE0602) is empty and URLA.X204 is not already checked and a co-borrower is present
)

**Rule Modifiers (conditional behavior based on loan profile):**
- **When `loan_type` = `FHA`** → ADD: FHA: if employment gap < 6 months, require written explanation from borrower. If gap > 6 months, require 2-year history before the gap.

  - Rule: FHA Employment Gap Rules — For FHA loans: gap < 6 months requires explanation letter; gap > 6 months requires documented 2-year history before gap.

  - Flag (warning): FHA Employment Gap - Explanation Required — Obtain borrower explanation letter for employment gap
  - Source: notes.txt:44-48

After completing this substep, call:
```
write_todo(step_id="STEP_05", substep_id="5.1", status="completed", notes="<detailed report with every check result, field IDs/values, and flags>")
```

---

### Substep 5.2 - Other Income (2e)
**Tool**: `review_urla_other_income`

Review Section 2e — Other Sources of Income on 1003 URLA Part 2 (alimony, dividend stocks, Social Security, etc.). Verify amounts are populated and documented.


**LOS Fields (read from state):**

| Encompass Field | Field ID | Key | Purpose |
|-----------------|----------|-----|---------|
| Other Income Type | `172` | `other_income_type` | Type of other income (alimony, dividends, etc.) |
| Other Income Amount (Monthly) | `173` | `other_income_amount` | Monthly other income amount |
| Borrower — Income from Other Sources Does Not Apply (1e) | `URLA.X40` | `borr_other_income_dna` | If checked, borrower has no other income sources. Suppresses requirement for other_income_type/amount for the borrower.
 |
| Co-Borrower — Income from Other Sources Does Not Apply (1e) | `URLA.X41` | `coborr_other_income_dna` | If checked, co-borrower has no other income sources. Suppresses requirement for other_income_type/amount for the co-borrower.
 |

**Business Rules:**
- **Other Income Documented** (existence_check): If other income is present in Encompass, verify it is documented in the file. Alimony requires court order; dividends require brokerage statements.

- **Other Income Does Not Apply Checkbox (1e)** (existence_check): URLA.X40 (borrower) and URLA.X41 (co-borrower) flag that no other income applies. If neither checkbox is checked and other_income_type/amount are empty, flag as advisory. If a checkbox IS checked, no other income fields are required for that borrower.


**Flags — raise when conditions are met:**
- WARNING: "Other Income Not Documented"
  - Condition: Other income amount is populated but supporting documents are missing
  - Remedy: Obtain supporting documentation for other income type
- INFO: "Other Income Section Incomplete (1e)"
  - Condition: Other income type and amount are both empty and neither URLA.X40 nor URLA.X41 (Does Not Apply) is checked

  - Remedy: Confirm with borrower whether other income exists. If not, check URLA.X40 / URLA.X41 as applicable to explicitly indicate none.


After completing this substep, call:
```
write_todo(step_id="STEP_05", substep_id="5.2", status="completed", notes="<detailed report with every check result, field IDs/values, and flags>")
```

---

## Step Completion

When ALL substeps above are completed:
1. Call `save_step_report(step_name="STEP_05", status="completed", ...)`
2. Call `write_todo(step_id="STEP_05", status="completed")` to advance to the next step
3. Call `write_todo(step_id="STEP_06", status="in_progress")` to start STEP_06 (1003 URLA Part 3)

⚠️ You MUST call write_todo to advance — do NOT produce a text-only response.
