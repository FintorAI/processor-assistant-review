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
| 11.1 | Update Transmittal Summary | `update_transmittal_summary` |

## Tool Calls

```python
# Substep 11.1 - Update Transmittal Summary
update_transmittal_summary(loan_guid=loan_id)
```

---

## Substeps

### Substep 11.1 - Update Transmittal Summary
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
| Proposed Homeowner Assoc. Dues (Monthly) | `233` | `hoa_dues_monthly` | PUD signal — HOA dues present on a non-condo property suggests the subject may sit in a Planned Unit Development. Used by the PUD-detection rule.
 |
| Attachment Type (Attached/Detached) | `CX.ATTACHMENT.TYPE` | `attachment_type` | PUD signal — an Attached property combined with HOA dues is a common PUD indicator. Used by the PUD-detection rule.
 |
| Property Street Address | `11` | `property_address` | Build the Zillow verification deep-link for the PUD-detection flag |
| Property City | `12` | `property_city` | Build the Zillow verification deep-link for the PUD-detection flag |
| Property State | `14` | `property_state` | Build the Zillow verification deep-link for the PUD-detection flag |
| Property ZIP | `15` | `property_zip` | Build the Zillow verification deep-link for the PUD-detection flag |
| Condo Project Name (Transmittal Summary) | `1298` | `condo_project_name` | Written write-only-if-blank from the Zillow subdivision when PUD indicators are present |
| CPM Project ID | `3050` | `condo_project_id` | Written by CUA after Freddie Mac CPA lookup — read to check if already populated |
| Subject Property Number of Units | `16` | `property_units` | Written write-only-if-blank to "1" when the property is confirmed single family (no HOA, not a PUD, not 2-4 unit).
 |
| Community Lending / Affordable Housing Initiative | `1551` | `community_lending_ahi` | Community lending CHECKBOX on the Transmittal Summary (YN — fieldWriter accepts "True"/"False", reads back Y/N; verified 2026-07-23). There is no separate "08 Home Ready" dropdown. NOT auto-written — the applies-when rule is unconfirmed; surfaced as a manual-entry row instead.
 |
| Home Buyers Education Certification | `1552` | `homebuyer_education_cert` | Dropdown on the Transmittal Summary — options (from prod field schema): "Homeowner education completed", "1x1 counseling completed", "Both completed", "Yes", "No". Left blank by design (write verified 2026-07-23) — surfaced as a manual-entry row for the dashboard.
 |

**Business Rules:**
- **Note Rate Equals Qualifying Rate** (field_comparison): Compare field 3 (Note Rate) vs field 1014 (Qualifying Rate on Transmittal Summary). For fixed-rate loans they must match. Flag warning if they differ.

- **Condo Project Fields Pending CUA** (custom): If property_type contains Condo or PUD and condo_project_name/id are blank, raise an info flag indicating condo fields await the computer-use agent (Freddie Mac Condo Project Advisor browser lookup).

- **PUD Detection** (custom): Consumes precomputed PUD signals from STEP_01 substep 1.3 (state['property_verification']['pud'], produced by review_property_listing via appraisal + HOA/Attached heuristic + Zillow/HasData). When strong signals are present, do NOT auto-set field 1012 to "Not in a Project"; instead skip the write (the Possible PUD flag was already raised at 1.3). When PUD indicators are present and the Zillow lookup returned a community/subdivision name, Project Name (field 1298) is written write-only-if-blank from that subdivision. Falls back to a live lookup only if 1.3 did not run (e.g. dev-mode skip).

- **Number of Units Defaults to 1 for Confirmed Single Family** (custom): When the property is confirmed non-PUD, non-condo, not 2-4 unit, and has no HOA dues (field 233 blank/zero), write "1" to Number of Units (field 16) if it is blank. Flag a warning if 16 already holds a value inconsistent with the unit count implied by property type.

- **Community Lending / Homebuyer Education — Manual Entry** (custom): Read fields 1551 (Community Lending/AHI checkbox) and 1552 (Home Buyers Education Certification dropdown). Never auto-write either — register both as manual-entry rows in state['manual_fields'] (with current values) so the processor can set them in the dashboard Field Writes tab.


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
- WARNING: "Possible PUD — Verify Property / Project Type"
  - Condition: Non-condo property with PUD indicators (appraisal Project Type = PUD, or HOA dues present + Attached dwelling)

  - Remedy: Verify on Zillow whether the subject is in a Planned Unit Development; if so set Property Type (1041) = PUD and Project Type (1012) = "Other: P/PUD"

- INFO: "Number of Units Set to 1"
  - Condition: Field 16 was blank and the property was confirmed single family (no HOA, not a PUD, not 2-4 unit)

  - Remedy: No action needed — Number of Units (16) set to 1
- WARNING: "Number of Units — Unexpected Value"
  - Condition: Field 16 holds a value inconsistent with the unit count implied by property type
  - Remedy: Verify and correct Number of Units (field 16)

After completing this substep, call:
```
write_todo(step_id="STEP_11", substep_id="11.1", status="completed", notes="<detailed report with every check result, field IDs/values, and flags>")
```

---

## Step Completion

When ALL substeps above are completed:
1. Call `save_step_report(step_name="STEP_11", status="completed", ...)`
2. Call `write_todo(step_id="STEP_11", status="completed")` to advance to the next step
3. Call `write_todo(step_id="STEP_12", status="in_progress")` to start STEP_12 (FHA-Specific Forms)

⚠️ You MUST call write_todo to advance — do NOT produce a text-only response.
