## Purpose

Run Fannie Mae DU and Freddie Mac LP (fresh AUS). Upload findings to eFolder UW bucket. Depends on Step 11 (eFolder cleaned — old AUS deleted first).


**NOTE**: Each substep has its own dedicated tool. State (`los_fields`, `doc_fields`, `loan_summary`) is automatically injected.

## Available Tools

| Tool | Purpose |
|------|---------|
| `run_fannie_aus` | Run Fannie Mae DU |
| `run_freddie_aus` | Run Freddie Mac LP |

## Overview

| Substep | Description | Tool |
|---------|-------------|------|
| 14.1 | Run Fannie Mae DU | `run_fannie_aus` |
| 14.2 | Run Freddie Mac LP | `run_freddie_aus` |

## Tool Calls

```python
# Substep 14.1 - Run Fannie Mae DU
run_fannie_aus(loan_guid=loan_id)
# Substep 14.2 - Run Freddie Mac LP
run_freddie_aus(loan_guid=loan_id)
```

---

## Substeps

### Substep 14.1 - Run Fannie Mae DU
**Tool**: `run_fannie_aus`

Submit loan to Fannie Mae Desktop Underwriter (DU). Record the decision (Approve/Eligible, Refer, Refer with Caution, etc.) and upload findings PDF to the Underwriting eFolder bucket.


**LOS Fields (read from state):**

| Encompass Field | Field ID | Key | Purpose |
|-----------------|----------|-----|---------|
| Mortgage Type | `1172` | `loan_type` | Fannie Mae DU applies to Conventional (and FHA with override) |

**Business Rules:**
- **Fannie DU Submission** (custom): Submit to Fannie Mae DU via Encompass. Record decision and upload findings. If not Approve/Eligible, flag the decision for processor review.


**Flags — raise when conditions are met:**
- WARNING: "Fannie DU Not Approve/Eligible"
  - Condition: DU decision is not Approve/Eligible
  - Remedy: Review findings with LO and underwriter before proceeding
- WARNING: "Fannie DU Findings Not Uploaded"
  - Condition: Fannie DU findings PDF not found in UW bucket
  - Remedy: Upload Fannie DU findings PDF to eFolder

After completing this substep, call:
```
write_todo(step_id="STEP_14", substep_id="14.1", status="completed", notes="<detailed report with every check result, field IDs/values, and flags>")
```

---

### Substep 14.2 - Run Freddie Mac LP
**Tool**: `run_freddie_aus`

Submit loan to Freddie Mac Loan Product Advisor (LP). Record decision (Accept, Caution, Ineligible, etc.) and upload findings PDF to the Underwriting eFolder bucket.


**LOS Fields (read from state):**

| Encompass Field | Field ID | Key | Purpose |
|-----------------|----------|-----|---------|
| Mortgage Type | `1172` | `loan_type` | Freddie LP applies to Conventional |

**Business Rules:**
- **Freddie LP Submission** (custom): Submit to Freddie Mac LP via Encompass. Record decision and upload findings. If not Accept, flag the decision for processor review.


**Flags — raise when conditions are met:**
- WARNING: "Freddie LP Not Accept"
  - Condition: LP decision is not Accept
  - Remedy: Review LP findings with LO and underwriter before proceeding
- WARNING: "Freddie LP Findings Not Uploaded"
  - Condition: Freddie LP findings PDF not found in UW bucket
  - Remedy: Upload Freddie LP findings PDF to eFolder

After completing this substep, call:
```
write_todo(step_id="STEP_14", substep_id="14.2", status="completed", notes="<detailed report with every check result, field IDs/values, and flags>")
```

---

## Step Completion

When ALL substeps above are completed:
1. Call `save_step_report(step_name="STEP_14", status="completed", ...)`
2. Call `write_todo(step_id="STEP_14", status="completed")` to advance to the next step
3. Call `write_todo(step_id="STEP_15", status="in_progress")` to start STEP_15 (Processor Workflow and Closing)

⚠️ You MUST call write_todo to advance — do NOT produce a text-only response.
