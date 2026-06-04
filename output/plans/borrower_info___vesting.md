## Purpose

Review and populate the Borrower Information - Vesting screen. Confirm occupancy intent and vesting name/type, then build the Final Vesting string. Manner Held (field 33) is read-only here — it is set by the 1003 URLA Lender step (STEP_03). For single/unmarried borrowers without co-borrower: "WILL OCCUPY / A SINGLE WOMAN OR UNMARRIED WOMAN". Click Build Final Vesting when complete. NBS (non-borrowing spouse) vesting handled separately.


**⚠️ This step may WRITE to Encompass.** Substeps that write are marked below.

**NOTE**: Each substep has its own dedicated tool. State (`los_fields`, `doc_fields`, `loan_summary`) is automatically injected.

## Available Tools

| Tool | Purpose |
|------|---------|
| `update_borrower_vesting` | Update Borrower Vesting |

## Overview

| Substep | Description | Tool |
|---------|-------------|------|
| 9.1 | Update Borrower Vesting | `update_borrower_vesting` |

## Tool Calls

```python
# Substep 9.1 - Update Borrower Vesting
# ⚠️ This substep WRITES to Encompass
update_borrower_vesting(loan_guid=loan_id)
```

---

## Substeps

### Substep 9.1 - Update Borrower Vesting
**Tool**: `update_borrower_vesting`

Verify and write occupancy intent (Borr/CoBorr.OccupancyIntent) and borrower vesting name/type (1868/1871, 1873/1876). Read Manner Held (field 33) — set by the 1003 URLA Lender step (STEP_03), not computed here — and read final vesting (field 1867). Final vesting is NEVER overwritten except for single/unmarried borrowers missing the required suffix (e.g. "AN UNMARRIED WOMAN"). Click Build Final Vesting in Encompass to set 1867.


**LOS Fields (read from state):**

| Encompass Field | Field ID | Key | Purpose |
|-----------------|----------|-----|---------|
| Property State | `14` | `property_state` | State-specific vesting rules (community property, joint tenants, etc.) |
| Loan Purpose | `19` | `loan_purpose` | Purchase → Will Occupy; Refinance → Currently Occupy |
| Occupancy Status | `1811` | `occupancy` | Primary / Investment / Second Home → drives occupancy intent |
| Borrower First Name | `4000` | `borrower_first_name` | Borrower vesting name |
| Borrower Middle Name | `4001` | `borrower_middle_name` | Borrower vesting name (middle) |
| Borrower Last Name | `4002` | `borrower_last_name` | Borrower vesting name |
| Borrower SSN | `65` | `borrower_ssn` | Write to vesting entity |
| Borrower Date of Birth | `1402` | `borrower_dob` | Write to vesting entity |
| Borrower Sex | `471` | `borrower_sex` | Determines Man/Woman in unmarried vesting suffix |
| Co-Borrower Sex | `478` | `coborrower_sex` | Used with borrower_sex to determine wife-first vesting order |
| Borrower Marital Status | `479` | `marital_status` | Married / Unmarried / Separated — drives manner held and vesting suffix |
| Borrower Occupancy Intent | `Borr.OccupancyIntent` | `borrower_occupancy_intent` | Will Occupy / Currently Occupy / Will Not Occupy |
| Borrower Vesting Name | `1868` | `borrower_vesting_name` | Vesting name written from borrower full name |
| Borrower Vesting Type | `1871` | `borrower_vesting_type` | Individual (standard borrower) |
| Borrower Vesting Description | `1872` | `borrower_vesting_desc` | The vesting dropdown for the borrower entity (e.g. "A SINGLE WOMAN", "AN UNMARRIED MAN", "HUSBAND AND WIFE"). Written from marital status + co-borrower presence. Build Final Vesting combines 1868 + 1872 + field 33 → 1867. |
| Co-Borrower First Name | `4004` | `coborrower_first_name` | Presence determines joint vesting; name written to 1873 |
| Co-Borrower Middle Name | `4005` | `coborrower_middle_name` | Co-borrower vesting name (middle) |
| Co-Borrower Last Name | `4006` | `coborrower_last_name` | Co-borrower vesting name |
| Co-Borrower SSN | `97` | `coborrower_ssn` | Write to co-borrower vesting entity |
| Co-Borrower Date of Birth | `1403` | `coborrower_dob` | Write to co-borrower vesting entity |
| Co-Borrower Occupancy Intent | `CoBorr.OccupancyIntent` | `coborrower_occupancy_intent` | Will Occupy / Currently Occupy / Will Not Occupy (same as borrower) |
| Co-Borrower Vesting Name | `1873` | `coborrower_vesting_name` | Vesting name written from co-borrower full name |
| Co-Borrower Vesting Type | `1876` | `coborrower_vesting_type` | Individual (standard co-borrower) |
| Co-Borrower Vesting Description | `1877` | `coborrower_vesting_desc` | The vesting dropdown for the co-borrower entity (e.g. "HUSBAND AND WIFE", "AN UNMARRIED MAN"). Written from marital status + presence of co-borrower. |
| Non-Borrowing Spouse Flag | `CX.NBSFLAG` | `nbs_flag` | YES if NBS exists on title without being a co-borrower |
| Non-Borrowing Spouse Name | `CX.NBSINFO` | `nbs_info` | NBS name → written to TR0101 (vesting entity) |
| Manner in Which Title Will Be Held | `33` | `manner_of_title` | READ-ONLY here. Manner Held (field 33) + URLA.X138 + Estate Will Be Held In (field 1066) are computed and written by the 1003 URLA Lender step (STEP_03, substep 3.1). This step reads field 33 only to confirm Build Final Vesting has its required input. |
| Final Vesting | `1867` | `final_vesting` | Read-only; populated by clicking Build Final Vesting in Encompass. Auto-corrected ONLY for single/unmarried borrowers missing required suffix. |

**Business Rules:**
- **Occupancy Intent — Borrower** (custom): Borr.OccupancyIntent must be set. Investment/Second Home → Will Not Occupy. Refinance + Primary → Currently Occupy. Purchase + Primary → Will Occupy. Write if empty or incorrect.

- **Borrower Vesting Name and Type** (existence_check): Field 1868 (Borrower Vesting Name) must match full borrower name from 4000+4001+4002. Field 1871 (Borrower Vesting Type) must be Individual. Write if empty.

- **Manner Held Present (read-only)** (existence_check): Field 33 (Manner Held) is owned by the 1003 URLA Lender step (3.1). This step only reads it; if empty, flag that the 1003 URLA Lender step should be run first so Build Final Vesting has its required input.

- **Final Vesting Built** (existence_check): Field 1867 (Final Vesting) must be populated. Read-only — populated via Build Final Vesting button in Encompass. For single/unmarried borrowers, verify suffix contains AN UNMARRIED WOMAN/MAN or A SINGLE WOMAN/MAN.

- **Single/Unmarried Vesting Suffix** (custom): If borrower is single/unmarried and no co-borrower: Final Vesting must end with "AN UNMARRIED WOMAN", "A SINGLE WOMAN", "AN UNMARRIED MAN", or "A SINGLE MAN". Auto-correct field 1867 if suffix is missing.


**Flags — raise when conditions are met:**
- WARNING: "Occupancy Intent Not Set"
  - Condition: Borr.OccupancyIntent is empty or incorrect
  - Remedy: Set occupancy intent based on loan purpose and occupancy type
- WARNING: "Borrower Vesting Name Missing (1868)"
  - Condition: Field 1868 is empty or does not match borrower name
  - Remedy: Write borrower full name to field 1868
- WARNING: "Borrower Vesting Description Missing (1872)"
  - Condition: Field 1872 is empty — Build Final Vesting will produce incomplete output
  - Remedy: Set vesting description (1872) before clicking Build Final Vesting
- WARNING: "Manner Held Empty"
  - Condition: Field 33 is empty (should be set by the 1003 URLA Lender step 3.1)
  - Remedy: Run/verify the 1003 URLA Lender step (3.1) to set Manner Held before Build Final Vesting
- WARNING: "Final Vesting Empty"
  - Condition: Field 1867 is empty
  - Remedy: Click Build Final Vesting in Encompass to populate field 1867
- WARNING: "Vesting Suffix Missing (Single/Unmarried)"
  - Condition: Borrower is single/unmarried, no co-borrower, and final vesting does not contain the required unmarried/single suffix

  - Remedy: Update Final Vesting (1867) to include AN UNMARRIED WOMAN/MAN or A SINGLE WOMAN/MAN as appropriate


**⚠️ Field Updates (writes to Encompass):**
- Field `1868` = `{borrower_full_name}` (when: empty)
- Field `1871` = `Individual` (when: always)
- Field `1872` = `{computed_vesting_desc}` (when: empty)
- Field `Borr.OccupancyIntent` = `{computed_occupancy_intent}` (when: always)

After completing this substep, call:
```
write_todo(step_id="STEP_09", substep_id="9.1", status="completed", notes="<detailed report with every check result, field IDs/values, and flags>")
```

---

## Step Completion

When ALL substeps above are completed:
1. Call `save_step_report(step_name="STEP_09", status="completed", ...)`
2. Call `write_todo(step_id="STEP_09", status="completed")` to advance to the next step
3. Call `write_todo(step_id="STEP_10", status="in_progress")` to start STEP_10 (Transmittal Summary)

⚠️ You MUST call write_todo to advance — do NOT produce a text-only response.
