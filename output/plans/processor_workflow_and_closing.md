## Purpose

Update the Processor Workflow screen and Processor Closing screen. Covers: Conforming/Non-Del Inv. Approval, doc type, signing date, and wire requested date.


**⚠️ This step may WRITE to Encompass.** Substeps that write are marked below.

**NOTE**: Each substep has its own dedicated tool. State (`los_fields`, `doc_fields`, `loan_summary`) is automatically injected.

## Available Tools

| Tool | Purpose |
|------|---------|
| `update_processor_workflow` | Processor Workflow Update |
| `update_processor_closing` | Processor Closing Update |

## Overview

| Substep | Description | Tool |
|---------|-------------|------|
| 11.1 | Processor Workflow Update | `update_processor_workflow` |
| 11.2 | Processor Closing Update | `update_processor_closing` |

## Tool Calls

```python
# Substep 11.1 - Processor Workflow Update
# ⚠️ This substep WRITES to Encompass
update_processor_workflow(loan_guid=loan_id)
# Substep 11.2 - Processor Closing Update
# ⚠️ This substep WRITES to Encompass
update_processor_closing(loan_guid=loan_id)
```

---

## Substeps

### Substep 11.1 - Processor Workflow Update
**Tool**: `update_processor_workflow`

Fill out the Processor Workflow screen: set Product Type (derived from loan type), Non-Del Inv. Approval (usually No), and Documentation Type (Full Doc for conventional loans).


**LOS Fields (read from state):**

| Encompass Field | Field ID | Key | Purpose |
|-----------------|----------|-----|---------|
| Mortgage Type | `1172` | `loan_type` | Drives product type mapping (Conventional → Conforming, FHA → FHA, etc.) |
| Product Type | `CX.PRODUCTTYPE` | `product_type` | Current value — verify and set if blank |
| Documentation Type (NON-QM Submission) | `CX.DOCUMENTATIONTYPE` | `doc_type_submission` | Set to Full Doc for conventional loans |
| Non-Del Inv. Approval | `CX.NONDEL.INV.APPROVAL` | `non_del_inv_approval` | Usually No — verify field ID before going live |

**Business Rules:**
- **Product Type Derived from Loan Type** (custom): Map loan type → product type: Conventional → Conforming, FHA → FHA, VA → VA, USDA → USDA. Write CX.PRODUCTTYPE if blank or incorrect.

- **Documentation Type Set to Full Doc** (custom): For conventional (Full Doc) loans, set CX.DOCUMENTATIONTYPE = "Full Doc". Non-QM Submission doc type for conventional is Full Doc.

- **Non-Del Inv. Approval** (existence_check): Set Non-Del Inv. Approval to No (standard for conforming loans).

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

**⚠️ Field Updates (writes to Encompass):**
- Field `CX.PRODUCTTYPE` = `{derived_product_type}` (when: product type can be derived from loan type)
- Field `CX.DOCUMENTATIONTYPE` = `Full Doc` (when: loan type is conventional/conforming)
- Field `CX.NONDEL.INV.APPROVAL` = `No` (when: loan type is conforming)

After completing this substep, call:
```
write_todo(step_id="STEP_11", substep_id="11.1", status="completed", notes="<detailed report with every check result, field IDs/values, and flags>")
```

---

### Substep 11.2 - Processor Closing Update
**Tool**: `update_processor_closing`

Fill out the Processor Closing screen. For purchase loans, signing date, wire requested date, and closing date all match — write the closing date value to both CUST50FV (Signing Date) and CX.WIREDATELO (Wire Requested Date).


**LOS Fields (read from state):**

| Encompass Field | Field ID | Key | Purpose |
|-----------------|----------|-----|---------|
| Closing Date | `748` | `closing_date` | Source of truth for signing and wire dates on purchase loans |
| Signing Date | `CUST50FV` | `signing_date` | Set to closing date on purchase loans |
| Wire Requested Date | `CX.WIREDATELO` | `wire_requested_date` | Set to closing date on purchase loans |
| Loan Purpose | `19` | `loan_purpose` | Determines date logic (purchase = all three dates match) |

**Business Rules:**
- **Purchase Loan Date Alignment** (custom): For purchase loans: Signing Date = Wire Requested Date = Closing Date (field 748). Read closing_date from LOS and write to both CUST50FV and CX.WIREDATELO. Flag if closing_date is blank.


**Flags — raise when conditions are met:**
- WARNING: "Closing Date Not Set"
  - Condition: Closing date (field 748) is blank — cannot set signing or wire dates
  - Remedy: Set the closing date in Encompass before running this step
- WARNING: "Signing Date Not Set"
  - Condition: Signing date is still blank after write attempt
  - Remedy: Manually set signing date to match closing date on Processor Closing screen

**⚠️ Field Updates (writes to Encompass):**
- Field `CUST50FV` = `{closing_date}` (when: loan_purpose == Purchase)
- Field `CX.WIREDATELO` = `{closing_date}` (when: loan_purpose == Purchase)

After completing this substep, call:
```
write_todo(step_id="STEP_11", substep_id="11.2", status="completed", notes="<detailed report with every check result, field IDs/values, and flags>")
```

---

## Step Completion

When ALL substeps above are completed:
1. Call `save_step_report(step_name="STEP_11", status="completed", ...)`
2. Call `write_todo(step_id="STEP_11", status="completed")` to advance to the next step

⚠️ You MUST call write_todo to advance — do NOT produce a text-only response.
