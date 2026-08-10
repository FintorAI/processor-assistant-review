## Purpose

Update the Processor Workflow screen, Processor Closing screen (incl. Certifications), and the In Processing/Submitted milestone worksheet. Covers: Conforming/Non-Del Inv. Approval, doc type, signing/wire date alignment, certification checkboxes, and processor assignment.


**⚠️ This step may WRITE to Encompass.** Substeps that write are marked below.

**NOTE**: Each substep has its own dedicated tool. State (`los_fields`, `doc_fields`, `loan_summary`) is automatically injected.

## Available Tools

| Tool | Purpose |
|------|---------|
| `update_processor_workflow` | Processor Workflow Update |
| `update_processor_closing` | Processor Closing Update |
| `update_processing_submitted` | Processing/Submitted Milestone Update |
| `build_action_items` | Build Action Items |

## Overview

| Substep | Description | Tool |
|---------|-------------|------|
| 14.1 | Processor Workflow Update | `update_processor_workflow` |
| 14.2 | Processor Closing Update | `update_processor_closing` |
| 14.3 | Processing/Submitted Milestone Update | `update_processing_submitted` |
| 14.4 | Build Action Items | `build_action_items` |

## Tool Calls

```python
# Substep 14.1 - Processor Workflow Update
# ⚠️ This substep WRITES to Encompass
update_processor_workflow(loan_guid=loan_id)
# Substep 14.2 - Processor Closing Update
# ⚠️ This substep WRITES to Encompass
update_processor_closing(loan_guid=loan_id)
# Substep 14.3 - Processing/Submitted Milestone Update
update_processing_submitted(loan_guid=loan_id)
# Substep 14.4 - Build Action Items
build_action_items(loan_guid=loan_id)
```

---

## Substeps

### Substep 14.1 - Processor Workflow Update
**Tool**: `update_processor_workflow`

Fill out the Processor Workflow screen: set Product Type (derived from loan type), Non-Del Inv. Approval (usually NO), Documentation Type (Full Doc for conventional loans), and the AKA field (write-if-blank from Credit Report AKAs aggregated across all bureaus).


**LOS Fields (read from state):**

| Encompass Field | Field ID | Key | Purpose |
|-----------------|----------|-----|---------|
| Mortgage Type | `1172` | `loan_type` | Drives product type mapping (Conventional → Conforming, FHA → FHA, etc.) |
| Product Type | `CX.PRODUCTTYPE` | `product_type` | Current value — verify and set if blank. Dropdown options (from prod field schema): Conforming, FHA, VA, USDA, DPA/Bond, Jumbo, NonQM, Reverse, 2nd/HELOC, Private, Construction, Bridge.
 |
| Documentation Type (NON-QM Submission) | `CX.DOCUMENTATIONTYPE` | `doc_type_submission` | Set to Full Doc for conventional loans. Dropdown options: Full Doc, Bank Statement(s), Verification of Employment, Asset Qualification, Debt Service Coverage Ratio, 1099, CPA P&L 12 mos, CPA P&L 24 mos.
 |
| Non-Del Inv. Approval (Prior Approval) | `CUST42FV` | `non_del_inv_approval` | Usually NO (set NO by default — YES only if the underwriter already approved). Field ID CUST42FV verified in EC UI + live round-trip write (2026-07-23); dropdown options are uppercase YES / NO. (CX.NONDEL.INV.APPROVAL does not exist in the prod instance.)
 |
| Processor Workflow AKAs | `CUST69FV` | `processor_workflow_aka` | AKA field on the Processor Workflow screen — write-if-blank with cleaned, deduped Credit Report AKAs from all bureaus (borrower + co-borrower). Field ID verified in Encompass UI 2026-08-10.
 |
| Borrower / Co-Borrower Names | `4000`/`4001`/`4002`, `4004`/`4005`/`4006` | `borrower_*_name`, `coborrower_*_name` | AKA cleaning — reversed-name reorder, name variations, cross-borrower filter on joint reports.
 |

**Docs (read from state):**
- Credit Report: `borrower_aka`, `coborrower_aka` (extractor's flat cross-bureau merge) and `credit_score_factors` (per-bureau entries, each with its own `AKA` list in original bureau format — comma format `LAST,FIRST,MIDDLE` preserved here).

**Business Rules:**
- **AKAs from All Bureaus (Write-if-Blank)** (computed): Union the flat borrower_aka/coborrower_aka lists with each bureau's AKA list from credit_score_factors[i].AKA, clean (bureau LAST,FIRST,MIDDLE → First Middle Last, reversed-name reorder, drop single-token shreds, cross-borrower filter), dedupe, and write the "; "-joined result to CUST69FV only when it is blank. Never overwrite a populated value. If no AKA was extracted, flag as warning instead. Bureau file IDs ending in B2 (e.g. EQX-B2) are attributed to the co-borrower.

- **Product Type Derived from Loan Type** (custom): Map loan type → product type: Conventional → Conforming, FHA → FHA, VA → VA, USDA → USDA. Write CX.PRODUCTTYPE if blank or incorrect.

- **Documentation Type Set to Full Doc** (custom): For conventional (Full Doc) loans, set CX.DOCUMENTATIONTYPE = "Full Doc". Non-QM Submission doc type for conventional is Full Doc.

- **Non-Del Inv. Approval** (existence_check): Set Non-Del Inv. Approval (CUST42FV) to "NO" (uppercase — dropdown enum). Standard for conforming loans; YES only when the underwriter already approved the loan before (rare).


**Flags — raise when conditions are met:**
- WARNING: "Product Type Not Set"
  - Condition: CX.PRODUCTTYPE is blank after attempted write
  - Remedy: Manually set Product Type on the Processor Workflow screen
- WARNING: "Documentation Type Not Set"
  - Condition: CX.DOCUMENTATIONTYPE is blank after attempted write
  - Remedy: Set Documentation Type to Full Doc on Processor Workflow screen
- WARNING: "Unknown Loan Type — Product Type Not Mapped"
  - Condition: Loan type does not match any known product type mapping
  - Remedy: Manually set Product Type on the Processor Workflow screen
- INFO-OVERWRITE: "Auto-corrected: Processor Workflow AKAs"
  - Condition: CUST69FV was blank and credit-report AKAs were written to it (emitted by `_write_fields`)
  - Remedy: Review the AKA names written to the Processor Workflow screen
- WARNING: "AKA Not Verified — No AKA Extracted from Credit Report"
  - Condition: No AKA names could be aggregated from the Credit Report (doc missing from eFolder, or extraction returned no usable AKA entries)
  - Remedy: Review the credit report's per-bureau AKA sections manually and fill the Processor Workflow AKA field

**⚠️ Field Updates (writes to Encompass):**
- Field `CX.PRODUCTTYPE` = `{derived_product_type}` (when: always)
- Field `CX.DOCUMENTATIONTYPE` = `Full Doc` (when: always)
- Field `CUST42FV` = `NO` (when: always)
- Field `CUST69FV` = `{computed_aka_list}` (when: CUST69FV blank and credit-report AKAs extracted)

After completing this substep, call:
```
write_todo(step_id="STEP_14", substep_id="14.1", status="completed", notes="<detailed report with every check result, field IDs/values, and flags>")
```

---

### Substep 14.2 - Processor Closing Update
**Tool**: `update_processor_closing`

Fill out the Processor Closing screen. Date alignment: Est Closing Date (763) is the source of truth — if Signing Date (CUST50FV) and Wire Requested Date (CX.WIREDATELO) already match it, write nothing; otherwise write the mismatched/blank date(s) FROM 763. Never write 763 itself. Also fills the Certifications section: Vesting Verified checkboxes always checked; Wire Instructions / Escrow E&O / CPL checkboxes checked from eFolder presence (live bucket listing); HOI Effective and Taxes left blank (manual-entry rows for the dashboard Field Writes tab).


**LOS Fields (read from state):**

| Encompass Field | Field ID | Key | Purpose |
|-----------------|----------|-----|---------|
| Est Closing Date | `763` | `closing_date` | SOURCE OF TRUTH for signing and wire dates on purchase loans. Read-only — never written by this substep.
 |
| Signing Date | `CUST50FV` | `signing_date` | Written from 763 only when blank or mismatched |
| Wire Requested Date | `CX.WIREDATELO` | `wire_requested_date` | Written from 763 only when blank or mismatched (except Michigan) |
| Loan Purpose | `19` | `loan_purpose` | Determines date logic (purchase = all three dates match) |
| Property State | `14` | `property_state` | MD purchase loans: Signing/Wire/Closing dates all match (confirmed). Michigan is called out separately by the processor — do not blindly equate Wire Requested Date to Closing Date there; flag for manual confirmation.
 |
| Vesting Verified - Title | `CX.VESTINGVERIFTITLE` | `cert_vesting_verif_title` | Certification checkbox (format X) — always checked ("X") |
| Vesting Verified - Borrower | `CX.VESTINGVERIFBOR` | `cert_vesting_verif_borrower` | Certification checkbox (format X) — always checked ("X") |
| Escrow wire instructions in file | `CX.WIREINSTINFILE` | `cert_wire_instructions_in_file` | Checked ("X") when a Wire Instructions document exists in the eFolder |
| Escrow E&O insurance in file | `CX.ESCROWEOINFILE` | `cert_escrow_eo_in_file` | Checked ("X") when an Escrow E&O insurance document exists in the eFolder |
| CPL in file with correct names, loan number, addressed to AWM | `CX.CPLINFILE` | `cert_cpl_in_file` | Checked ("X") when a Closing Protection Letter exists in the eFolder |
| HOI is effective on or before Note Date (Wet) / Funding Date (Dry) | `CX.HOIEFFECTIVE` | `cert_hoi_effective` | Left blank by design — manual-entry row (judgment call) |
| Taxes | `CX.TAXES` | `taxes_dropdown` | Left blank by design — manual-entry row. Dropdown options (from prod field schema): Unimproved, Improved.
 |

**Business Rules:**
- **Purchase Loan Date Alignment (763 is Source of Truth)** (custom): For purchase loans: Signing Date (CUST50FV) and Wire Requested Date (CX.WIREDATELO) must equal Est Closing Date (763). If all three already match, write NOTHING. Otherwise write only the blank/mismatched date(s) with the value FROM field 763. Field 763 itself is never written. Flag if 763 is blank.

- **Michigan Wire Date Exception** (custom): For Michigan (property_state == MI) purchase loans, still align Signing Date (CUST50FV) with 763, but do NOT auto-set Wire Requested Date (CX.WIREDATELO) — the processor flagged Michigan's wire timing as different from the MD same-day pattern. Raise an info flag + manual-entry row asking the processor to confirm the correct wire date instead.

- **Certifications — Vesting Verified** (custom): Always check (write "X") CX.VESTINGVERIFTITLE and CX.VESTINGVERIFBOR. Both are custom checkbox fields (format X) — verified by live round-trip write 2026-07-23.

- **Certifications — eFolder Presence Checkboxes** (custom): Check (write "X") CX.WIREINSTINFILE / CX.ESCROWEOINFILE / CX.CPLINFILE when a matching document bucket with attachments exists in the live eFolder listing (GET /v3/loans/{id}/documents — fuzzy title match on "wire instruction", "e&o" / "errors & omissions", "cpl" / "closing protection"). When absent, leave unchecked and raise an info flag so the processor knows why.

- **Certifications — Manual-Entry Fields** (custom): CX.HOIEFFECTIVE (HOI effective date certification) and CX.TAXES (Unimproved/Improved) are never auto-written — register both as manual-entry rows in state['manual_fields'] for the dashboard.


**Flags — raise when conditions are met:**
- WARNING: "Closing Date Not Set"
  - Condition: Est Closing Date (field 763) is blank — cannot align signing or wire dates
  - Remedy: Set the closing date in Encompass before running this step
- WARNING: "Signing Date Not Set"
  - Condition: Signing date is still blank after write attempt
  - Remedy: Manually set signing date to match closing date on Processor Closing screen
- INFO: "Michigan Wire Date Needs Manual Confirmation"
  - Condition: Michigan purchase loan — Wire Requested Date was not auto-set to match Closing Date
  - Remedy: Confirm the correct Wire Requested Date for this Michigan closing with the closing team
- INFO: "Closing Dates Already Aligned"
  - Condition: Signing, Wire Requested, and Est Closing dates all match — no writes needed
  - Remedy: No action needed
- INFO: "Certification Document Not Found in eFolder"
  - Condition: Wire Instructions / Escrow E&O / CPL bucket absent or empty in the live eFolder listing — corresponding certification checkbox left unchecked

  - Remedy: Upload the document to the eFolder, then check the certification box

**⚠️ Field Updates (writes to Encompass):**
- Field `CUST50FV` = `{closing_date}` (when: loan_purpose == Purchase and CUST50FV != 763)
- Field `CX.WIREDATELO` = `{closing_date}` (when: loan_purpose == Purchase and CX.WIREDATELO != 763 and property_state != MI)
- Field `CX.VESTINGVERIFTITLE` = `X` (when: always)
- Field `CX.VESTINGVERIFBOR` = `X` (when: always)
- Field `CX.WIREINSTINFILE` = `X` (when: wire instructions document present in eFolder)
- Field `CX.ESCROWEOINFILE` = `X` (when: escrow E&O document present in eFolder)
- Field `CX.CPLINFILE` = `X` (when: CPL document present in eFolder)

After completing this substep, call:
```
write_todo(step_id="STEP_14", substep_id="14.2", status="completed", notes="<detailed report with every check result, field IDs/values, and flags>")
```

---

### Substep 14.3 - Processing/Submitted Milestone Update
**Tool**: `update_processing_submitted`

Update the "In Processing/Submitted" milestone worksheet via the v1 milestones API (GET/PATCH /encompass/v1/loans/{id}/milestones): ensure the Loan Processor associate is assigned (Ash Desai, user id "adesai"), and collect the milestone's MISSING REQUIRED FIELDS (the EC UI "Go To Fields" dialog list) as manual-entry rows for the dashboard. The "Finished" checkbox is the milestone's doneIndicator via ?action=finish (NOT field 1057 — schema says 1057 is "Borr Declarations E"); auto-finishing is HELD pending processor confirmation. PATCH bodies must include startDate; a loan open in the EC UI is locked (409 EBS-4360) and writes are skipped with a warning.


**Business Rules:**
- **Assign Loan Processor** (custom): Read the loan's milestones; find "In Processing/Submitted". If its loanAssociate is not a Loan Processor user, PATCH the milestone with {"loanAssociate": {"loanAssociateType": "User", "id": "adesai"}}. Honors DEV_MODE.dry_run (no PATCH; info flag instead).

- **Missing Required Fields Probe** (custom): Encompass enforces the admin's per-milestone required fields server-side when a milestone is finished (the settings APIs that expose the config return 403 for our API user). The tool attempts PATCH ?action=finish to trigger that validation: on rejection it parses the unmet-field list into a warning flag + state['manual_fields'] rows (field_id + description) so the dashboard shows them for manual entry; if the attempt unexpectedly succeeds it immediately PATCHes ?action=unfinish (auto-finish is held). Skipped when the milestone is already finished, in dry_run, or the loan is locked.

- **Milestone Finished Checkbox (HELD)** (custom): Never leave doneIndicator set — the mechanism is confirmed (milestone doneIndicator via ?action=finish, verified on prod loan 2604964148) but auto-finishing is held pending processor confirmation. Raise an info flag showing the current doneIndicator value.


**Flags — raise when conditions are met:**
- INFO: "Loan Processor Assigned"
  - Condition: Milestone loanAssociate was blank/non-processor and was PATCHed to Ash Desai (tool emits this at runtime as info-overwrite — the audit severity)

  - Remedy: Confirm the processor assignment on the In Processing/Submitted worksheet
- INFO: "Loan Processor Already Assigned"
  - Condition: Milestone already has a Loan Processor associate
  - Remedy: No action needed
- WARNING: "Milestone Required Fields Missing"
  - Condition: The finish-attempt probe was rejected because admin-required fields are blank — the unmet fields are listed in the flag and registered as manual-entry rows

  - Remedy: Fill the listed fields (dashboard Field Writes tab or Encompass), then finish the milestone
- INFO: "Milestone Required Fields All Satisfied"
  - Condition: The finish-attempt probe passed validation (milestone was immediately re-opened)
  - Remedy: Check the Finished box on the worksheet when ready
- WARNING: "Loan Locked — Milestone Writes Skipped"
  - Condition: The loan is open/locked in the Encompass UI (409 EBS-4360)
  - Remedy: Close the loan in Encompass, then re-run this substep
- INFO: "Milestone Finish Pending Manual Confirmation"
  - Condition: Always raised — doneIndicator (Finished checkbox) is not left set by the agent
  - Remedy: Check the Finished box on the In Processing/Submitted worksheet after verifying required fields (Encompass enforces them on save)

- WARNING: "Milestone Not Found"
  - Condition: No "In Processing/Submitted" milestone exists on the loan
  - Remedy: Verify the loan's milestone template in Encompass

After completing this substep, call:
```
write_todo(step_id="STEP_14", substep_id="14.3", status="completed", notes="<detailed report with every check result, field IDs/values, and flags>")
```

---

### Substep 14.4 - Build Action Items
**Tool**: `build_action_items`

Final substep — run last, after all reviews and form updates are complete. Derives component-agnostic communications action items from the review state (unresolved flags + loan facts) and writes them to state['comms_actions'] for the dashboard to trigger downstream agents (email / Blend follow-up). Each rule emits at most one item, carrying a component + trigger block so new components can be added without changing the schema. Items are merged/deduped by id across re-runs (runtime status preserved). No Encompass writes.


**Document Types:**
- **Title Report**:
  - `title_company`
- **HOA Statement**:
  - `hoa_dues`

**Business Rules:**
- **Derive Communications Action Items** (custom): Inspect the eFolder + unresolved review flags and emit at most one action item per rule: (1) Order Title Report when no Title Report is present; (2) Lock-Desk Address Correction when a LOCKED loan has an unresolved address discrepancy; (3) EMD Check Request to the buyer's agent when an EMD flag is unresolved; (4) no-HOA LOE (Blend) when HOA status is unconfirmed on a condo and no HOA Statement is present. Each item is component-agnostic with a trigger block (agent / graph_id / resume_contract / payload) and is deduped by id. Test runs redirect email to test inboxes.


After completing this substep, call:
```
write_todo(step_id="STEP_14", substep_id="14.4", status="completed", notes="<detailed report with every check result, field IDs/values, and flags>")
```

---

## Step Completion

When ALL substeps above are completed:
1. Call `save_step_report(step_name="STEP_14", status="completed", ...)`
2. Call `write_todo(step_id="STEP_14", status="completed")` to advance to the next step

⚠️ You MUST call write_todo to advance — do NOT produce a text-only response.
