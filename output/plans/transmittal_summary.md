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
| 9.1 | Update Transmittal Summary | `update_transmittal_summary` |

## Tool Calls

```python
# Substep 9.1 - Update Transmittal Summary
update_transmittal_summary(loan_guid=loan_id)
```

---

## Substeps

### Substep 9.1 - Update Transmittal Summary
**Tool**: `update_transmittal_summary`

Populate the 1008 Transmittal Summary: project type, project ID, project name, qualifying rate. If condo, run Freddie Mac Condo Project Advisor lookup and populate project type (Warrantable / Non-Warrantable / Established). Verify note rate == qualifying rate.


**LOS Fields (read from state):**

| Encompass Field | Field ID | Key | Purpose |
|-----------------|----------|-----|---------|
| Property Type | `1041` | `property_type` | Condo triggers Condo Project Advisor lookup |
| Condo Project Type | `1065` | `condo_project_type` | Warrantable / Non-Warrantable / Established |
| Condo Project Name | `CX.CONDO.PROJECT.NAME` | `condo_project_name` | From Condo Project Advisor |
| Condo Project ID | `CX.CONDO.PROJECT.ID` | `condo_project_id` | From Condo Project Advisor |
| Note Rate | `3` | `note_rate` | Must equal qualifying rate |
| Qualifying Rate | `799` | `qualifying_rate` | Must equal note rate (unless ARM) |

**Business Rules:**
- **Note Rate Equals Qualifying Rate** (field_comparison): Note rate must match the qualifying rate on the Transmittal Summary.

**Flags — raise when conditions are met:**
- WARNING: "Note Rate vs Qualifying Rate Mismatch"
  - Condition: Note rate doesn't match qualifying rate on Transmittal Summary
  - Remedy: Reconcile rates — qualifying rate should equal note rate for fixed-rate loans
- WARNING: "Condo Project Fields Not Populated"
  - Condition: Condo property but project type/name/ID not populated
  - Remedy: Run Freddie Mac Condo Project Advisor to populate project details

**Rule Modifiers (conditional behavior based on loan profile):**
- **When `loan_type` = `Conventional`** → ADD: For Conventional/Condo: run Freddie Mac Condo Project Advisor lookup to determine Warrantable / Non-Warrantable / Established / etc.

  - Rule: Condo Project Advisor Lookup — Run Freddie Mac Condo Project Advisor (FMCPA) for this property. Return project type, project name, and project ID.

  - Flag (warning): Condo Project Advisor Not Run — Run Freddie Mac Condo Project Advisor lookup
  - Source: notes.txt

After completing this substep, call:
```
write_todo(step_id="STEP_09", substep_id="9.1", status="completed", notes="<detailed report with every check result, field IDs/values, and flags>")
```

---

## Step Completion

When ALL substeps above are completed:
1. Call `save_step_report(step_name="STEP_09", status="completed", ...)`
2. Call `write_todo(step_id="STEP_09", status="completed")` to advance to the next step
3. Call `write_todo(step_id="STEP_10", status="in_progress")` to start STEP_10 (Orders)

⚠️ You MUST call write_todo to advance — do NOT produce a text-only response.
