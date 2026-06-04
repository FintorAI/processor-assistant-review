## Purpose

Review and populate the Transmittal Summary (1008). Run Freddie Mac Condo Project Advisor lookup if property is a Condo. Confirm project type, project ID/name, note rate vs qualifying rate.


**NOTE**: Each substep has its own dedicated tool. State (`los_fields`, `doc_fields`, `loan_summary`) is automatically injected.

## Available Tools

| Tool | Purpose |
|------|---------|
| `update_transmittal_summary` | Update Transmittal Summary |

## Overview

| Substep | Description | Tool |
|---------|-------------|------|
| 10.1 | Update Transmittal Summary | `update_transmittal_summary` |

## Tool Calls

```python
# Substep 10.1 - Update Transmittal Summary
update_transmittal_summary(loan_guid=loan_id)
```

---

## Substeps

### Substep 10.1 - Update Transmittal Summary
**Tool**: `update_transmittal_summary`

Review the 1008 Transmittal Summary. Checks this agent can do now:
  1. Rate check — compare Note Rate (field 3) vs Qualifying Rate (field 1014).
     Flag warning if they differ (fixed-rate loans: they should match).
  2. Project Type info — read field 1553 and flag info showing current value.
Condo-specific fields (Project Name, CPM Project ID#) are populated by the computer-use agent after Freddie Mac Condo Project Advisor browser lookup. See ARCHITECTURE.md "Transmittal Summary — Condo Split" for details.


**LOS Fields (read from state):**

| Encompass Field | Field ID | Key | Purpose |
|-----------------|----------|-----|---------|
| Note Rate | `3` | `note_rate` | Must equal qualifying rate for fixed-rate loans |
| Qualifying Rate (Transmittal Summary) | `1014` | `qualifying_rate` | Confirmed from Encompass Transmittal Summary screen (field 1014) |
| Project Type dropdown (Transmittal Summary) | `1012` | `project_type_1012` | Written when property is NOT a condo/PUD: "Other: G/Not in a Project or Development". Values include E/Detached, F/Attached, G/Not in a Project or Development, P/PUD, T/2-4 Unit.
 |
| Level of Property Review (Exterior/Interior) | `1541` | `property_review_type` | Read to surface current review type (Exterior / Interior / Full) |
| Appraisal Form Number | `1542` | `appraisal_form_number` | Written based on property type: 1004 (single-family/1-unit), 1073 (condo), 1025 (2-4 unit). Standard residential = 1004. NOTE: Field ID 1542 unverified against live Encompass — confirm before relying on writes.
 |
| Property Form Type (Transmittal Summary) | `TSUM.PropertyFormType` | `property_form_type` | Written alongside field 1542 — standard value is "Uniform Residential Appraisal Report" for 1-unit non-condo.
 |
| Project Type (Transmittal Summary) | `1553` | `transmittal_project_type` | Read and surface for info — e.g. Established Project, New Project |
| Property Type | `1041` | `property_type` | If Condo/PUD, raise pending flag for CUA condo advisor substep |
| Condo Project Name | `CX.CONDO.PROJECT.NAME` | `condo_project_name` | Written by CUA after Freddie Mac CPA lookup — read to check if already populated |
| Condo Project ID | `CX.CONDO.PROJECT.ID` | `condo_project_id` | Written by CUA after Freddie Mac CPA lookup — read to check if already populated |

**Business Rules:**
- **Note Rate Equals Qualifying Rate** (field_comparison): Compare field 3 (Note Rate) vs field 1014 (Qualifying Rate on Transmittal Summary). For fixed-rate loans they must match. Flag warning if they differ.

- **Condo Project Fields Pending CUA** (custom): If property_type contains Condo or PUD and condo_project_name/id are blank, raise an info flag indicating condo fields await the computer-use agent (Freddie Mac Condo Project Advisor browser lookup).


**Flags — raise when conditions are met:**
- WARNING: "Note Rate vs Qualifying Rate Mismatch"
  - Condition: Note rate (field 3) doesn't match qualifying rate (field 1014)
  - Remedy: Reconcile rates — qualifying rate should equal note rate for fixed-rate loans
- INFO: "Project Type"
  - Condition: Always raised — shows current value of field 1553 for processor awareness
  - Remedy: Verify project type is correct for this property
- INFO: "Condo Project Fields Pending — CUA Required"
  - Condition: Condo/PUD property and project name/ID not yet populated
  - Remedy: Computer-use agent will run Freddie Mac Condo Project Advisor to populate project name and ID

After completing this substep, call:
```
write_todo(step_id="STEP_10", substep_id="10.1", status="completed", notes="<detailed report with every check result, field IDs/values, and flags>")
```

---

## Step Completion

When ALL substeps above are completed:
1. Call `save_step_report(step_name="STEP_10", status="completed", ...)`
2. Call `write_todo(step_id="STEP_10", status="completed")` to advance to the next step
3. Call `write_todo(step_id="STEP_11", status="in_progress")` to start STEP_11 (Processor Workflow and Closing)

⚠️ You MUST call write_todo to advance — do NOT produce a text-only response.
