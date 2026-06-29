## Purpose

FHA-only forms that don't apply to conventional/VA/USDA loans. Covers FHA Management (CAIVRS authorization numbers + FHA Case Number/ADP code) and the HUD-92900-LT Transmittal. Every substep is gated on loan_type == FHA and is a no-op for non-FHA loans. Runs after the Transmittal Summary (STEP_10) and before Processor Workflow and Closing (STEP_12).


**NOTE**: Each substep has its own dedicated tool. State (`los_fields`, `doc_fields`, `loan_summary`) is automatically injected.

## Available Tools

| Tool | Purpose |
|------|---------|
| `update_fha_management` | FHA Management |
| `update_hud_transmittal` | HUD Transmittal |

## Overview

| Substep | Description | Tool |
|---------|-------------|------|
| 11.1 | FHA Management | `update_fha_management` |
| 11.2 | HUD Transmittal | `update_hud_transmittal` |

## Tool Calls

```python
# Substep 11.1 - FHA Management
update_fha_management(loan_guid=loan_id)
# Substep 11.2 - HUD Transmittal
update_hud_transmittal(loan_guid=loan_id)
```

---

## Substeps

### Substep 11.1 - FHA Management
**Tool**: `update_fha_management`

Populate the FHA Management screen (Tracking tab). Two parts:
  1. CAIVRS — write the per-applicant CAIVRS Authorization Number extracted
     from the CAIVRS document (borrower + co-borrowers) into the Encompass
     CAIVRS fields. Write-only-if-blank; emits an info flag listing what was
     written and a warning if the document has a number Encompass is missing.
  2. FHA Case Number — confirm the FHA Case Number (field 1040) and ADP code
     (703 for a standard 1-unit property) are present. Flag if missing; the
     case number itself is assigned via FHA Connection, not written here.
FHA-only: no-op when loan_type != FHA.


**Condition:** Only runs when {"equals": "FHA", "field": "loan_type"}

**LOS Fields (read from state):**

| Encompass Field | Field ID | Key | Purpose |
|-----------------|----------|-----|---------|
| Mortgage Type | `1172` | `loan_type` | Gate — substep only runs for FHA loans |
| FHA/VA Agency Case Number | `1040` | `fha_case_number` | Confirm FHA Case Number is assigned (FHA Government Documents) |

**Document Types:**
- **CAIVRS**:
  - `caivrs_status`
  - `borrower_authorization_number`
  - `coborrower_authorization_number`
  - `coborrower2_authorization_number`
  - `coborrower3_authorization_number`
  - `authorization_date`

**Business Rules:**
- **Write CAIVRS Authorization Numbers** (custom): For each applicant, if the CAIVRS document has an Authorization Number and the corresponding Encompass CAIVRS field is blank, write it. Never overwrite a populated CAIVRS field. The Encompass CAIVRS field IDs are not yet verified — the tool keeps them in a single CAIVRS_FIELDS map and only writes when that map is confirmed (otherwise it flags the numbers for manual entry).

- **FHA Case Number Present** (existence_check): Flag a warning if the FHA Case Number (field 1040) is blank — it must be assigned via FHA Connection before submission. ADP code is 703 for a standard 1-unit property.


**Flags — raise when conditions are met:**
- INFO: "CAIVRS Numbers Written"
  - Condition: One or more CAIVRS Authorization Numbers were written to Encompass
  - Remedy: Verify the CAIVRS numbers on the FHA Management screen against the document
- WARNING: "CAIVRS Numbers Pending Manual Entry"
  - Condition: CAIVRS document has Authorization Numbers but the Encompass CAIVRS field IDs are unverified, so they were not written automatically

  - Remedy: Enter the CAIVRS Authorization Number(s) on the FHA Management Tracking tab
- WARNING: "CAIVRS Document Missing"
  - Condition: FHA loan with no CAIVRS document / no Authorization Numbers extracted
  - Remedy: Run CAIVRS Authorization in FHA Connection and upload the result to the eFolder
- WARNING: "FHA Case Number Missing"
  - Condition: FHA loan with blank FHA Case Number (field 1040)
  - Remedy: Assign the FHA Case Number via FHA Connection (ADP code 703 for standard 1-unit)

After completing this substep, call:
```
write_todo(step_id="STEP_11", substep_id="11.1", status="completed", notes="<detailed report with every check result, field IDs/values, and flags>")
```

---

### Substep 11.2 - HUD Transmittal
**Tool**: `update_hud_transmittal`

Review the HUD-92900-LT (FHA Loan Transmittal). This form is normally completed by the underwriter, so the agent verifies/flags rather than writes: Source/EIN should be MMP / 52 (Government), and the FHA Case Number + ADP code must be present. FHA-only: no-op when loan_type != FHA.


**Condition:** Only runs when {"equals": "FHA", "field": "loan_type"}

**LOS Fields (read from state):**

| Encompass Field | Field ID | Key | Purpose |
|-----------------|----------|-----|---------|
| Mortgage Type | `1172` | `loan_type` | Gate — substep only runs for FHA loans |
| FHA/VA Agency Case Number | `1040` | `fha_case_number` | HUD-92900-LT requires the case number |

**Business Rules:**
- **HUD-92900-LT Review** (existence_check): Surface the HUD-92900-LT for processor/underwriter review. Confirm Source/EIN = MMP/52 (Government) and that the FHA Case Number + ADP code are present. Flag-only — the underwriter completes this form.


**Flags — raise when conditions are met:**
- INFO: "HUD-92900-LT Review Required"
  - Condition: Always raised for FHA loans — underwriter completes the HUD-92900-LT
  - Remedy: Verify Source/EIN = MMP/52 (Government) and FHA Case Number + ADP code on the HUD Transmittal
- WARNING: "HUD-92900-LT Case Number Missing"
  - Condition: FHA loan with blank FHA Case Number (field 1040) — HUD Transmittal incomplete
  - Remedy: Assign the FHA Case Number before the underwriter completes the HUD-92900-LT

After completing this substep, call:
```
write_todo(step_id="STEP_11", substep_id="11.2", status="completed", notes="<detailed report with every check result, field IDs/values, and flags>")
```

---

## Step Completion

When ALL substeps above are completed:
1. Call `save_step_report(step_name="STEP_11", status="completed", ...)`
2. Call `write_todo(step_id="STEP_11", status="completed")` to advance to the next step
3. Call `write_todo(step_id="STEP_12", status="in_progress")` to start STEP_12 (Processor Workflow and Closing)

⚠️ You MUST call write_todo to advance — do NOT produce a text-only response.
