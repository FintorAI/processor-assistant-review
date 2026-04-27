## Purpose

Print (generate PDF) of the 1008 Transmittal Summary, 2020 URLA (1003), and Cover Letter into the correct eFolder buckets. Note: Cover Letter is NOT manually placed here — it is auto-placed when Required Fields → Finished is clicked at Step 17.


**NOTE**: Each substep has its own dedicated tool. State (`los_fields`, `doc_fields`, `loan_summary`) is automatically injected.

## Available Tools

| Tool | Purpose |
|------|---------|
| `print_forms` | Print Forms to eFolder |

## Overview

| Substep | Description | Tool |
|---------|-------------|------|
| 13.1 | Print Forms to eFolder | `print_forms` |

## Tool Calls

```python
# Substep 13.1 - Print Forms to eFolder
print_forms(loan_guid=loan_id)
```

---

## Substeps

### Substep 13.1 - Print Forms to eFolder
**Tool**: `print_forms`

Print the following to eFolder: - 1008 Transmittal Summary → Transmittal Summary bucket - 2020 URLA → 1003 URLA bucket Do NOT upload Cover Letter here — it is auto-placed at Step 17 via Required Fields → Finished (notes.txt:219-220).


**LOS Fields (read from state):**

| Encompass Field | Field ID | Key | Purpose |
|-----------------|----------|-----|---------|
| Loan Number | `364` | `loan_number` | Used in printed document header/footer |

**Business Rules:**
- **Print Transmittal Summary** (custom): Generate PDF of 1008 Transmittal Summary and upload to Transmittal Summary eFolder bucket.

- **Print 2020 URLA** (custom): Generate PDF of 2020 URLA and upload to 1003 URLA eFolder bucket.


**Flags — raise when conditions are met:**
- WARNING: "Transmittal Summary Not Printed"
  - Condition: 1008 Transmittal Summary PDF not found in eFolder bucket
  - Remedy: Print and upload the Transmittal Summary
- WARNING: "2020 URLA Not Printed"
  - Condition: 2020 URLA PDF not found in eFolder bucket
  - Remedy: Print and upload the 2020 URLA

After completing this substep, call:
```
write_todo(step_id="STEP_13", substep_id="13.1", status="completed", notes="<detailed report with every check result, field IDs/values, and flags>")
```

---

## Step Completion

When ALL substeps above are completed:
1. Call `save_step_report(step_name="STEP_13", status="completed", ...)`
2. Call `write_todo(step_id="STEP_13", status="completed")` to advance to the next step
3. Call `write_todo(step_id="STEP_14", status="in_progress")` to start STEP_14 (Run Fresh AUS)

⚠️ You MUST call write_todo to advance — do NOT produce a text-only response.
