## Purpose

Final submission steps after flag review passes. Mark docs Ready-for-UW in eFolder, click Required Fields → Finished (auto-places Cover Letter), change milestone to Submitted, set processor name, check tasks, and do final eFolder cleanup (Unassigned → Recycle).


**⚠️ This step may WRITE to Encompass.** Substeps that write are marked below.

**NOTE**: Each substep has its own dedicated tool. State (`los_fields`, `doc_fields`, `loan_summary`) is automatically injected.

## Available Tools

| Tool | Purpose |
|------|---------|
| `mark_docs_ready_for_uw` | Mark Docs Ready-for-UW |
| `complete_required_fields` | Required Fields - Click Finished |
| `update_milestone` | Milestone Change and Processor Name |
| `final_efolder_cleanup` | Final eFolder Cleanup |

## Overview

| Substep | Description | Tool |
|---------|-------------|------|
| 17.1 | Mark Docs Ready-for-UW | `mark_docs_ready_for_uw` |
| 17.2 | Required Fields - Click Finished | `complete_required_fields` |
| 17.3 | Milestone Change and Processor Name | `update_milestone` |
| 17.4 | Final eFolder Cleanup | `final_efolder_cleanup` |

## Tool Calls

```python
# Substep 17.1 - Mark Docs Ready-for-UW
mark_docs_ready_for_uw(loan_guid=loan_id)
# Substep 17.2 - Required Fields - Click Finished
complete_required_fields(loan_guid=loan_id)
# Substep 17.3 - Milestone Change and Processor Name
# ⚠️ This substep WRITES to Encompass
update_milestone(loan_guid=loan_id)
# Substep 17.4 - Final eFolder Cleanup
final_efolder_cleanup(loan_guid=loan_id)
```

---

## Substeps

### Substep 17.1 - Mark Docs Ready-for-UW
**Tool**: `mark_docs_ready_for_uw`

In eFolder, select and mark the following documents as Ready-for-UW per notes.txt:215. These are handed to UW with the next milestone change. Documents: 1003, 1008, Assets, Bank Statements, Condo Project, Credit Report, Estimated Settlement Statement, Flood Certificate, Fraud, General Letter of Explanation, ID Customer, Income Calc, Internal Submission, LDP, Loan Estimate, Lock Confirmation, Paystubs, Purchase Agreement, Tax Summary, UW (x2), VOE.


**Document Types:**
- **1003 URLA**:
  - `urla_doc_id`
- **Transmittal Summary**:
  - `transmittal_doc_id`
- **Assets** (ALL COPIES):
  - `assets_doc_ids`
- **Bank Statement** (ALL COPIES):
  - `bank_statement_doc_ids`
- **Underwriting (DU / LP)** (ALL COPIES):
  - `uw_doc_ids`
- **Income Calc**:
  - `income_calc_doc_id`

**Business Rules:**
- **All Specified Docs Marked Ready-for-UW** (custom): Select each document in the ready-for-UW list and change its status to Ready-for-UW in Encompass eFolder.


**Flags — raise when conditions are met:**
- WARNING: "Doc Not Found for Ready-for-UW"
  - Condition: A required Ready-for-UW document is missing from eFolder
  - Remedy: Verify document is in eFolder before marking Ready-for-UW

After completing this substep, call:
```
write_todo(step_id="STEP_17", substep_id="17.1", status="completed", notes="<detailed report with every check result, field IDs/values, and flags>")
```

---

### Substep 17.2 - Required Fields - Click Finished
**Tool**: `complete_required_fields`

Navigate to Encompass Required Fields screen. Confirm all required fields are complete. Click Finished. This auto-places the Cover Letter in the correct eFolder bucket as a side effect (notes.txt:219-220).


**LOS Fields (read from state):**

| Encompass Field | Field ID | Key | Purpose |
|-----------------|----------|-----|---------|
| Required Fields Status | `CX.REQUIRED.FIELDS.STATUS` | `required_fields_status` | Verify required fields are complete before clicking Finished |

**Business Rules:**
- **Required Fields Complete** (custom): All Encompass-required fields must be complete. Click Finished to submit. Cover Letter auto-places in eFolder bucket as side effect.


**Flags — raise when conditions are met:**
- CRITICAL: "Required Fields Incomplete"
  - Condition: Encompass required fields screen shows incomplete fields
  - Remedy: Return to Flag Review — incomplete required fields must be resolved

After completing this substep, call:
```
write_todo(step_id="STEP_17", substep_id="17.2", status="completed", notes="<detailed report with every check result, field IDs/values, and flags>")
```

---

### Substep 17.3 - Milestone Change and Processor Name
**Tool**: `update_milestone`

Change loan milestone from "In Processing" to "Submitted to UW". Set the processor name field to state["processor_name"]. Check and close all outstanding tasks.


**LOS Fields (read from state):**

| Encompass Field | Field ID | Key | Purpose |
|-----------------|----------|-----|---------|
| Current Milestone | `CX.MILESTONE.CURRENT` | `current_milestone` | Confirm current milestone is In Processing |
| Processor Name | `CX.PROCESSOR.NAME` | `processor_name` | Set to state["processor_name"] on submission |

**Business Rules:**
- **Milestone Changed to Submitted** (custom): Change milestone from In Processing → Submitted to UW. Set processor name. Verify all tasks are closed or note any outstanding.


**Flags — raise when conditions are met:**
- CRITICAL: "Milestone Change Failed"
  - Condition: Milestone could not be changed to Submitted
  - Remedy: Manually change milestone in Encompass
- WARNING: "Processor Name Not Set"
  - Condition: Processor name field is empty after milestone change
  - Remedy: Set processor name in the loan record

**⚠️ Field Updates (writes to Encompass):**
- Field `CX.PROCESSOR.NAME` = `{processor_name}` (when: always)

After completing this substep, call:
```
write_todo(step_id="STEP_17", substep_id="17.3", status="completed", notes="<detailed report with every check result, field IDs/values, and flags>")
```

---

### Substep 17.4 - Final eFolder Cleanup
**Tool**: `final_efolder_cleanup`

Move all Unassigned eFolder documents to the Recycle bucket. Clean up any residual junk files after submission.


**Document Types:**
- **Unassigned** (ALL COPIES):
  - `unassigned_doc_ids`

**Business Rules:**
- **Move Unassigned to Recycle** (custom): Find all documents in the Unassigned bucket and move them to Recycle.


**Flags — raise when conditions are met:**
- INFO: "Unassigned Documents Remain"
  - Condition: Unassigned documents could not be moved to Recycle
  - Remedy: Manually move Unassigned documents to Recycle in eFolder

After completing this substep, call:
```
write_todo(step_id="STEP_17", substep_id="17.4", status="completed", notes="<detailed report with every check result, field IDs/values, and flags>")
```

---

## Step Completion

When ALL substeps above are completed:
1. Call `save_step_report(step_name="STEP_17", status="completed", ...)`
2. Call `write_todo(step_id="STEP_17", status="completed")` to advance to the next step
3. Call `write_todo(step_id="STEP_18", status="in_progress")` to start STEP_18 (Notifications)

⚠️ You MUST call write_todo to advance — do NOT produce a text-only response.
