## Purpose

Review and populate the Borrower Information - Vesting screen. Confirm occupancy intent, vesting (marital status from URLA Part 1), and click Build Final Vesting.


**⚠️ This step may WRITE to Encompass.** Substeps that write are marked below.

**NOTE**: Each substep has its own dedicated tool. State (`los_fields`, `doc_fields`, `loan_summary`) is automatically injected.

## Available Tools

| Tool | Purpose |
|------|---------|
| `update_borrower_vesting` | Update Borrower Vesting |

## Overview

| Substep | Description | Tool |
|---------|-------------|------|
| 8.1 | Update Borrower Vesting | `update_borrower_vesting` |

## Tool Calls

```python
# Substep 8.1 - Update Borrower Vesting
# ⚠️ This substep WRITES to Encompass
update_borrower_vesting(loan_guid=loan_id)
```

---

## Substeps

### Substep 8.1 - Update Borrower Vesting
**Tool**: `update_borrower_vesting`

Verify occupancy intent, populate vesting description from URLA Part 1 (married/unmarried/single). Build and confirm the Final Vesting string (e.g. "WILL OCCUPY / A SINGLE WOMAN OR UNMARRIED WOMAN"). Click Build Final Vesting when complete.


**LOS Fields (read from state):**

| Encompass Field | Field ID | Key | Purpose |
|-----------------|----------|-----|---------|
| Occupancy / Occupancy Intent | `1811` | `occupancy` | Will Occupy / Will Not Occupy |
| Borrower Marital Status | `1065` | `marital_status` | Married / Unmarried / Separated |
| Vesting Description | `CX.VESTING.DESCRIPTION` | `vesting_description` | Current vesting text |
| Final Vesting | `CX.FINAL.VESTING` | `final_vesting` | Built vesting string — confirm after Build Final Vesting click |
| Co-Borrower First Name | `4004` | `coborrower_first_name` | Determine if Joint vesting applies |

**Business Rules:**
- **Vesting Consistent with Marital Status** (custom): If single/unmarried and no co-borrower: Sole Ownership, unmarried/single vesting language. If married: appropriate joint vesting or sole ownership with spousal signature.

- **Final Vesting Built** (existence_check): Final Vesting field must be populated after the Build Final Vesting action.

**Flags — raise when conditions are met:**
- WARNING: "Vesting Description Not Set"
  - Condition: Vesting description field is empty
  - Remedy: Populate vesting description from marital status before building
- WARNING: "Final Vesting Not Built"
  - Condition: Final Vesting field is empty after update
  - Remedy: Click Build Final Vesting in Encompass

**⚠️ Field Updates (writes to Encompass):**
- Field `CX.VESTING.DESCRIPTION` = `{computed_vesting_description}` (when: always)

After completing this substep, call:
```
write_todo(step_id="STEP_08", substep_id="8.1", status="completed", notes="<detailed report with every check result, field IDs/values, and flags>")
```

---

## Step Completion

When ALL substeps above are completed:
1. Call `save_step_report(step_name="STEP_08", status="completed", ...)`
2. Call `write_todo(step_id="STEP_08", status="completed")` to advance to the next step
3. Call `write_todo(step_id="STEP_09", status="in_progress")` to start STEP_09 (Transmittal Summary)

⚠️ You MUST call write_todo to advance — do NOT produce a text-only response.
