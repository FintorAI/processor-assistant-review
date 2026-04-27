## Purpose

Place all required orders: (a) Appraisal — skip if collateral relief or AUS waiver; (b) Condo Questionnaire — skip if non-condo; (c) File Contacts + Title Order Email. AUS results from Step 1 inform skip conditions.


**NOTE**: Each substep has its own dedicated tool. State (`los_fields`, `doc_fields`, `loan_summary`) is automatically injected.

## Available Tools

| Tool | Purpose |
|------|---------|
| `order_appraisal` | Order Appraisal |
| `order_condo_questionnaire` | Order Condo Questionnaire |
| `send_title_order_email` | File Contacts and Title Order Email |

## Overview

| Substep | Description | Tool |
|---------|-------------|------|
| 10.1 | Order Appraisal | `order_appraisal` |
| 10.2 | Order Condo Questionnaire | `order_condo_questionnaire` |
| 10.3 | File Contacts and Title Order Email | `send_title_order_email` |

## Tool Calls

```python
# Substep 10.1 - Order Appraisal
order_appraisal(loan_guid=loan_id)
# Substep 10.2 - Order Condo Questionnaire
order_condo_questionnaire(loan_guid=loan_id)
# Substep 10.3 - File Contacts and Title Order Email
send_title_order_email(loan_guid=loan_id)
```

---

## Substeps

### Substep 10.1 - Order Appraisal
**Tool**: `order_appraisal`

Order the appraisal via Encompass unless AUS collateral relief or appraisal waiver is active. If VA loan, appraisal cannot be waived. Reference existing AUS findings (read at Step 1) for waiver status.


**LOS Fields (read from state):**

| Encompass Field | Field ID | Key | Purpose |
|-----------------|----------|-----|---------|
| Mortgage Type | `1172` | `loan_type` | VA loans cannot waive appraisal |
| Appraisal Waiver Flag | `CX.APPRAISAL.WAIVER` | `appraisal_waiver` | If True, skip appraisal order |
| AUS Collateral Relief | `CX.AUS.COLLATERAL.RELIEF` | `aus_collateral_relief` | If True, skip appraisal order |

**Business Rules:**
- **Skip If Waived** (custom): If appraisal_waiver == True OR aus_collateral_relief == True AND loan_type != VA: skip appraisal order. Otherwise, order via Encompass.


**Flags — raise when conditions are met:**
- WARNING: "Appraisal Not Ordered"
  - Condition: Appraisal required but not yet ordered
  - Remedy: Order appraisal via Encompass appraisal ordering workflow
- CRITICAL: "VA Appraisal Cannot Be Waived"
  - Condition: VA loan with appraisal waiver flag active
  - Remedy: Remove waiver flag — VA loans require a full appraisal

**Rule Modifiers (conditional behavior based on loan profile):**
- **When `loan_type` = `VA`** → ADD: VA loans always require a full appraisal — waiver is not allowed.
  - Flag (critical): VA Loan - Appraisal Required (No Waiver) — Remove appraisal waiver and order full VA appraisal
  - Source: notes.txt

After completing this substep, call:
```
write_todo(step_id="STEP_10", substep_id="10.1", status="completed", notes="<detailed report with every check result, field IDs/values, and flags>")
```

---

### Substep 10.2 - Order Condo Questionnaire
**Tool**: `order_condo_questionnaire`

Order the condo questionnaire only if the property type is Condo. Uses the project type from Step 9 (Transmittal Summary). Skip for non-condo properties.


**LOS Fields (read from state):**

| Encompass Field | Field ID | Key | Purpose |
|-----------------|----------|-----|---------|
| Property Type | `1041` | `property_type` | Only order if Condo |
| Condo Project Type | `CX.CONDO.PROJECT.TYPE` | `condo_project_type` | Determines questionnaire type |

**Business Rules:**
- **Skip If Non-Condo** (custom): If property_type != Condo, mark this substep as dynamically skipped. Otherwise, order the Condo Questionnaire via the ordering service.


**Flags — raise when conditions are met:**
- WARNING: "Condo Questionnaire Not Ordered"
  - Condition: Condo property but questionnaire not ordered
  - Remedy: Order condo questionnaire for this property

After completing this substep, call:
```
write_todo(step_id="STEP_10", substep_id="10.2", status="completed", notes="<detailed report with every check result, field IDs/values, and flags>")
```

---

### Substep 10.3 - File Contacts and Title Order Email
**Tool**: `send_title_order_email`

Update File Contacts in Encompass (Title Company, Appraiser, etc.) and send the title order email to the title company. Use Ash's email template. Recipients: Joe Salem (cc Almas Younis, Ash Desai) per recipients.json.


**LOS Fields (read from state):**

| Encompass Field | Field ID | Key | Purpose |
|-----------------|----------|-----|---------|
| Title Company Name | `CX.TITLE.COMPANY.NAME` | `title_company_name` | Title company for order email |
| Title Company Email | `CX.TITLE.COMPANY.EMAIL` | `title_company_email` | Email recipient for title order |
| Property Address | `11` | `property_address` | Included in title order email |
| Borrower Last Name | `4002` | `borrower_last_name` | Included in title order email subject line |

**Business Rules:**
- **File Contacts Updated** (existence_check): Title company name and email must be populated before sending order email.

**Flags — raise when conditions are met:**
- WARNING: "Title Company Contact Missing"
  - Condition: Title company name or email not populated in File Contacts
  - Remedy: Populate title company contact information before sending email
- WARNING: "Title Order Email Not Sent"
  - Condition: Title order email was not sent
  - Remedy: Send title order email to the title company

After completing this substep, call:
```
write_todo(step_id="STEP_10", substep_id="10.3", status="completed", notes="<detailed report with every check result, field IDs/values, and flags>")
```

---

## Step Completion

When ALL substeps above are completed:
1. Call `save_step_report(step_name="STEP_10", status="completed", ...)`
2. Call `write_todo(step_id="STEP_10", status="completed")` to advance to the next step
3. Call `write_todo(step_id="STEP_11", status="in_progress")` to start STEP_11 (Prep eFolder)

⚠️ You MUST call write_todo to advance — do NOT produce a text-only response.
