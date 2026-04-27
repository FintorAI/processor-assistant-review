## Purpose

Update the Processor Workflow screen and Processor Closing screen. Covers: Conforming/Non-Del Inv. Approval, doc type, signing date, and wire requested date.


**NOTE**: Each substep has its own dedicated tool. State (`los_fields`, `doc_fields`, `loan_summary`) is automatically injected.

## Available Tools

| Tool | Purpose |
|------|---------|
| `update_processor_workflow` | Processor Workflow Update |
| `update_processor_closing` | Processor Closing Update |

## Overview

| Substep | Description | Tool |
|---------|-------------|------|
| 15.1 | Processor Workflow Update | `update_processor_workflow` |
| 15.2 | Processor Closing Update | `update_processor_closing` |

## Tool Calls

```python
# Substep 15.1 - Processor Workflow Update
update_processor_workflow(loan_guid=loan_id)
# Substep 15.2 - Processor Closing Update
update_processor_closing(loan_guid=loan_id)
```

---

## Substeps

### Substep 15.1 - Processor Workflow Update
**Tool**: `update_processor_workflow`

Fill out the Processor Workflow screen: select Conforming or Non-Deliverable Investor Approval, set doc type (wet-signed / e-sign / hybrid), and confirm the loan has been submitted to UW milestone.


**LOS Fields (read from state):**

| Encompass Field | Field ID | Key | Purpose |
|-----------------|----------|-----|---------|
| Investor Type (Conforming / Non-Del) | `CX.INVESTOR.TYPE` | `investor_type` | Determines investor approval pathway |
| Doc Type (Wet / E-sign / Hybrid) | `CX.DOC.TYPE` | `doc_type` | Closing doc signing method |

**Business Rules:**
- **Investor Type Set** (existence_check): Conforming or Non-Deliverable investor type must be selected.
- **Doc Type Set** (existence_check): Signing doc type must be selected.

**Flags — raise when conditions are met:**
- WARNING: "Investor Type Not Set"
  - Condition: Investor type not selected in Processor Workflow
  - Remedy: Select Conforming or Non-Deliverable Investor Approval
- WARNING: "Doc Type Not Set"
  - Condition: Doc type not selected in Processor Workflow
  - Remedy: Select Wet-Signed / E-Sign / Hybrid for this loan

After completing this substep, call:
```
write_todo(step_id="STEP_15", substep_id="15.1", status="completed", notes="<detailed report with every check result, field IDs/values, and flags>")
```

---

### Substep 15.2 - Processor Closing Update
**Tool**: `update_processor_closing`

Fill out the Processor Closing screen: set signing date and wire requested date.


**LOS Fields (read from state):**

| Encompass Field | Field ID | Key | Purpose |
|-----------------|----------|-----|---------|
| Signing Date | `CX.SIGNING.DATE` | `signing_date` | Scheduled signing date |
| Wire Requested Date | `CX.WIRE.REQUESTED.DATE` | `wire_requested_date` | Date wire was requested |

**Business Rules:**
- **Signing Date Set** (existence_check): Signing date must be populated on the Processor Closing screen.

**Flags — raise when conditions are met:**
- WARNING: "Signing Date Not Set"
  - Condition: Signing date is blank on Processor Closing screen
  - Remedy: Set the signing date before proceeding to submission

After completing this substep, call:
```
write_todo(step_id="STEP_15", substep_id="15.2", status="completed", notes="<detailed report with every check result, field IDs/values, and flags>")
```

---

## Step Completion

When ALL substeps above are completed:
1. Call `save_step_report(step_name="STEP_15", status="completed", ...)`
2. Call `write_todo(step_id="STEP_15", status="completed")` to advance to the next step
3. Call `write_todo(step_id="STEP_16", status="in_progress")` to start STEP_16 (Flag Review (HITL))

⚠️ You MUST call write_todo to advance — do NOT produce a text-only response.
