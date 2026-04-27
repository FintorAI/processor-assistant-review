## Purpose

Singular human gate before any final writes. Calls interrupt() with all accumulated flags from Steps 1-15 (missing docs, field mismatches, Cover Letter draft, etc.) plus any Required-Fields-screen items pulled from Encompass. Processor reviews, resolves, edits, or accepts each item. Resume value flows back into state and unlocks Step 17.


**NOTE**: Each substep has its own dedicated tool. State (`los_fields`, `doc_fields`, `loan_summary`) is automatically injected.

## Available Tools

| Tool | Purpose |
|------|---------|
| `review_flags` | Review All Flags (HITL) |

## Overview

| Substep | Description | Tool |
|---------|-------------|------|
| 16.1 | Review All Flags (HITL) | `review_flags` |

## Tool Calls

```python
# Substep 16.1 - Review All Flags (HITL)
review_flags(loan_guid=loan_id)
```

---

## Substeps

### Substep 16.1 - Review All Flags (HITL)
**Tool**: `review_flags`

Gather all accumulated flags from Steps 1-15 from state["flags"]. Also pull the Required Fields screen from Encompass — surface any fields that Encompass marks as required. Call interrupt() with the full flag list + required fields list. Wait for processor to review and respond. On resume: record which flags were resolved and carry forward any processor edits to state.


**LOS Fields (read from state):**

| Encompass Field | Field ID | Key | Purpose |
|-----------------|----------|-----|---------|
| Encompass Required Fields Status | `CX.REQUIRED.FIELDS.STATUS` | `required_fields_status` | List of fields Encompass marks as required before submission |

**Business Rules:**
- **All Critical Flags Must Be Resolved** (custom): Processor must acknowledge or resolve all critical flags before Step 17 can proceed. Warning flags may be acknowledged without resolution.


**Flags — raise when conditions are met:**
- CRITICAL: "Critical Flag Not Resolved"
  - Condition: Processor confirmed HITL without resolving a critical flag
  - Remedy: Resolve all critical flags before proceeding to submission

After completing this substep, call:
```
write_todo(step_id="STEP_16", substep_id="16.1", status="completed", notes="<detailed report with every check result, field IDs/values, and flags>")
```

---

## Step Completion

When ALL substeps above are completed:
1. Call `save_step_report(step_name="STEP_16", status="completed", ...)`
2. Call `write_todo(step_id="STEP_16", status="completed")` to advance to the next step
3. Call `write_todo(step_id="STEP_17", status="in_progress")` to start STEP_17 (Submission)

⚠️ You MUST call write_todo to advance — do NOT produce a text-only response.
