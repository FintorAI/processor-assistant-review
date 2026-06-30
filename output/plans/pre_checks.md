## Purpose

Verify presence (and signing where applicable) of all docs that downstream steps depend on. Fails fast with a flag if any are missing. Also reads the existing AUS (Underwriting bucket) for collateral relief and income raw relief — AUS is required and flagged if absent.


**NOTE**: Each substep has its own dedicated tool. State (`los_fields`, `doc_fields`, `loan_summary`) is automatically injected.

## Available Tools

| Tool | Purpose |
|------|---------|
| `run_pre_checks` | Document Presence Check |
| `review_file_contacts` | File Contacts Check |

## Overview

| Substep | Description | Tool |
|---------|-------------|------|
| 1.1 | Document Presence Check | `run_pre_checks` |
| 1.2 | File Contacts Check | `review_file_contacts` |

## Tool Calls

```python
# Substep 1.1 - Document Presence Check
run_pre_checks(loan_guid=loan_id)
# Substep 1.2 - File Contacts Check
review_file_contacts(loan_guid=loan_id)
```

---

## Substeps

### Substep 1.1 - Document Presence Check
**Tool**: `run_pre_checks`

Check eFolder for presence (and signing) of: 1003, Borrower's Cert/Auth, State disclosures (e.g. MD Right of Assumption), VOE, Assets, Bank Statement, VOD, Driver's License (or other ID), ESS, and existing AUS (Underwriting bucket). Flag any missing docs as warnings/critical before proceeding.


**LOS Fields (read from state):**

| Encompass Field | Field ID | Key | Purpose |
|-----------------|----------|-----|---------|
| Property State | `14` | `property_state` | Determines which state-specific disclosures are required |
| Mortgage Type | `1172` | `loan_type` | Determines bank statement month requirement (Conv=2mo, FHA=1mo) |
| Appraisal Waiver | `CX.APPRAISAL.WAIVER` | `appraisal_waiver` | Flags collateral relief from AUS |

**Document Types:**
- **1003 URLA**:
  - `urla_signed`
- **Borrower's Certification & Authorization**:
  - `bca_present`
- **Underwriting (DU / LP)**:
  - `aus_collateral_relief`
  - `aus_income_raw_relief`
  - `aus_run_date`
- **VOE - non service provider**:
  - `voe_present`
- **Paystubs**:
  - `paystubs_present`
- **Assets**:
  - `assets_present`
- **Bank Statement** (ALL COPIES):
  - `bank_statement_months`
  - `bank_statement_dates`
- **VOD**:
  - `vod_present`
- **Driver's License**:
  - `dl_present`
  - `dl_expiry`
- **Estimated Settlement Statement**:
  - `ess_present`
- **Credit Report**:
  - `credit_report_present`
  - `credit_report_date`
  - `fact_act_present`
- **MD Notice of Right to Rescind**:
  - `md_rescind_signed`
- **MD DUAL CAPACITY IN REAL ESTATE**:
  - `md_dual_cap_signed`
- **MD Important Notice Regarding Counseling**:
  - `md_counseling_signed`
- **MD Notice Regarding Right for Assumption**:
  - `md_assumption_signed`
- **MD Right to Choose Insurance Provider**:
  - `md_ins_provider_signed`
- **MD Settlement Services/Right to Choose**:
  - `md_settlement_signed`
- **Flood Certificate**:
  - `flood_cert_present`
  - `flood_zone`
- **Evidence of Hazard Insurance**:
  - `hazard_insurance_present`
  - `hazard_insurance_expiry`
- **Title Report**:
  - `title_report_present`
  - `title_legal_description`
- **LDP**:
  - `ldp_present`
- **General Letter of Explanation**:
  - `loe_present`
  - `loe_topics`

**Business Rules:**
- **Required Docs Present** (existence_check): All listed docs must exist in eFolder. Flag each missing one as warning or critical based on downstream dependency severity.

- **AUS Required** (existence_check): Existing AUS (Underwriting bucket) must be present. If missing, flag as critical — orders at Step 10 depend on AUS findings.

- **Bank Statement Recency** (value_check): If Conventional, require 2 months of bank statements. If FHA, require 1 month. Flag if fewer than required months are present.

- **DL Not Expired** (value_check): Driver's License expiry must be >= today.

**Flags — raise when conditions are met:**
- CRITICAL: "Missing Required Document"
  - Condition: Any of the required documents is absent from the eFolder
  - Remedy: Locate or request the missing document before proceeding
- CRITICAL: "AUS Missing"
  - Condition: No Underwriting (DU/LP) document found in eFolder
  - Remedy: Run AUS or retrieve existing results before ordering
- WARNING: "Insufficient Bank Statements"
  - Condition: Fewer bank statement months than required for loan type
  - Remedy: Request additional statements from borrower
- WARNING: "ID Expired"
  - Condition: Driver's License expiration date is before today
  - Remedy: Request a valid, non-expired government ID from borrower
- WARNING: "State Disclosure Missing"
  - Condition: State-specific disclosure document not found in eFolder
  - Remedy: Locate and add the required state disclosure
- CRITICAL: "Credit Report Missing"
  - Condition: Credit report not found in eFolder
  - Remedy: Obtain and upload credit report before proceeding
- WARNING: "FACT ACT Disclosure Missing"
  - Condition: FACT ACT disclosure form not found within credit report
  - Remedy: Confirm FACT ACT form is included in the credit report package
- WARNING: "Flood Certificate Missing"
  - Condition: Flood certificate not found in eFolder
  - Remedy: Order or obtain flood certificate for the subject property
- WARNING: "Hazard Insurance Missing"
  - Condition: Evidence of hazard insurance not found in eFolder
  - Remedy: Request homeowners insurance deck page from borrower or agent
- WARNING: "Title Report Missing"
  - Condition: Title report not found in eFolder
  - Remedy: Request title report from title company
- WARNING: "LDP Missing"
  - Condition: Loan Defect Prevention document not found in eFolder
  - Remedy: Obtain LDP before submission

**Rule Modifiers (conditional behavior based on loan profile):**
- **When `state` = `MD`** → ADD: Maryland requires the "Notice Regarding Right for Assumption Under Certain Circumstances" disclosure. Flag if missing.

  - Flag (warning): MD Right of Assumption Disclosure Missing — Add MD disclosure before submission
  - Source: notes.txt:14

After completing this substep, call:
```
write_todo(step_id="STEP_01", substep_id="1.1", status="completed", notes="<detailed report with every check result, field IDs/values, and flags>")
```

---

### Substep 1.2 - File Contacts Check
**Tool**: `review_file_contacts`

Verify that the four key file contacts are assigned in Encompass: Buyer's Agent, Seller's Agent, Seller 1, and Escrow Company. Flag missing contacts as warnings so they can be linked before orders are placed.


**LOS Fields (read from state):**

| Encompass Field | Field ID | Key | Purpose |
|-----------------|----------|-----|---------|
| Seller 1 Name | `638` | `seller_1_name` | Seller 1 full name — cross-check against SELLER contact type |
| Escrow Case # | `186` | `escrow_case_number` | Escrow Company Escrow Case # — current value before writing the settlement File # |

**Business Rules:**
- **Required Contacts Present** (existence_check): Buyer's Agent, Seller's Agent, Seller 1 (SELLER contact type), and Escrow Company must all be assigned to the loan in Encompass. Flag each missing contact as a warning.


**Flags — raise when conditions are met:**
- WARNING: "Missing File Contact"
  - Condition: One or more required contacts not linked to the loan
  - Remedy: Go to File Contacts in Encompass and add the missing contact

After completing this substep, call:
```
write_todo(step_id="STEP_01", substep_id="1.2", status="completed", notes="<detailed report with every check result, field IDs/values, and flags>")
```

---

## Step Completion

When ALL substeps above are completed:
1. Call `save_step_report(step_name="STEP_01", status="completed", ...)`
2. Call `write_todo(step_id="STEP_01", status="completed")` to advance to the next step
3. Call `write_todo(step_id="STEP_02", status="in_progress")` to start STEP_02 (Borrower Summary - Origination)

⚠️ You MUST call write_todo to advance — do NOT produce a text-only response.
