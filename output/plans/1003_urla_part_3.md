## Purpose

Review and populate 1003 URLA Part 3: Assets/VOD (2a), EMD (2b), Liabilities/VOL (2c), 2d, and REO Section 3. Cross-check against bank statements, VOD, and Purchase Contract.


**NOTE**: Each substep has its own dedicated tool. State (`los_fields`, `doc_fields`, `loan_summary`) is automatically injected.

## Available Tools

| Tool | Purpose |
|------|---------|
| `review_urla_assets` | Assets and VOD (2a) |
| `review_urla_emd` | EMD Check (2b) |
| `review_urla_liabilities` | Liabilities and VOL (2c) |
| `review_urla_reo` | Other Assets and REO (2d, Section 3) |

## Overview

| Substep | Description | Tool |
|---------|-------------|------|
| 5.1 | Assets and VOD (2a) | `review_urla_assets` |
| 5.2 | EMD Check (2b) | `review_urla_emd` |
| 5.3 | Liabilities and VOL (2c) | `review_urla_liabilities` |
| 5.4 | Other Assets and REO (2d, Section 3) | `review_urla_reo` |

## Tool Calls

```python
# Substep 5.1 - Assets and VOD (2a)
review_urla_assets(loan_guid=loan_id)
# Substep 5.2 - EMD Check (2b)
review_urla_emd(loan_guid=loan_id)
# Substep 5.3 - Liabilities and VOL (2c)
review_urla_liabilities(loan_guid=loan_id)
# Substep 5.4 - Other Assets and REO (2d, Section 3)
review_urla_reo(loan_guid=loan_id)
```

---

## Substeps

### Substep 5.1 - Assets and VOD (2a)
**Tool**: `review_urla_assets`

Review Section 2a (Assets/VOD). Check bank accounts, checking/savings. Cross-reference against bank statements. Verify account numbers, dates, and balances. Flag if total assets insufficient for closing. Rename asset documents per naming convention (e.g. "Edward Jones x1234 - 04/14/2026").


**LOS Fields (read from state):**

| Encompass Field | Field ID | Key | Purpose |
|-----------------|----------|-----|---------|
| Total Assets | `732` | `total_assets` | Total asset amount — sufficient for closing? |
| Checking Account Balance | `733` | `checking_balance` | Verify against bank statement |
| Savings Account Balance | `734` | `savings_balance` | Verify against bank statement |

**Document Types:**
- **Bank Statement** (ALL COPIES):
  - `bank_account_number`
  - `bank_balance`
  - `bank_statement_date`
  - `bank_zel_deposits`
  - `bank_large_deposits`
- **Assets**:
  - `asset_account_number`
  - `asset_balance`
  - `asset_account_date`
- **VOD**:
  - `vod_account_balances`
  - `vod_account_numbers`

**Business Rules:**
- **Bank Statement Recency** (value_check): Bank statements must be within required recency window (Conv=60 days, FHA=30 days).
- **ZEL/Zelle Deposits Flagged** (custom): Any ZEL or Zelle deposits should be flagged for UW explanation letter. Klarna or Firm deposits should also be flagged.

- **Large Deposits Flagged** (custom): Green deposits (cash deposits, large non-payroll deposits) must be explained and sourced. Flag for borrower Letter of Explanation.


**Flags — raise when conditions are met:**
- WARNING: "ZEL/Zelle Deposit Requires Explanation"
  - Condition: Zelle or ZEL deposit found in bank statement
  - Remedy: Request borrower LOE explaining the ZEL/Zelle transactions
- WARNING: "Large Deposit Requires Sourcing"
  - Condition: Large non-payroll deposit (green deposit) found in bank statement
  - Remedy: Request documentation sourcing the large deposit
- WARNING: "Bank Statement Stale"
  - Condition: Bank statement older than required recency window for loan type
  - Remedy: Request updated bank statements from borrower
- INFO: "Asset Document Needs Renaming"
  - Condition: Asset document not named per convention (e.g. "Edward Jones x1234 - 04/14/2026")
  - Remedy: Rename document in eFolder per naming convention

**Rule Modifiers (conditional behavior based on loan profile):**
- **When `loan_type` = `FHA`** → ADD: FHA requires only 1 month of bank statements (vs 2 for Conventional).
  - Rule: FHA Bank Statement 1 Month — FHA loan — only 1 month of bank statements required.
  - Source: notes.txt:64

After completing this substep, call:
```
write_todo(step_id="STEP_05", substep_id="5.1", status="completed", notes="<detailed report with every check result, field IDs/values, and flags>")
```

---

### Substep 5.2 - EMD Check (2b)
**Tool**: `review_urla_emd`

Review Section 2b — Earnest Money Deposit. Verify EMD amount matches Purchase Contract. Flag if EMD check copy is missing (trigger email to Realtor via Step 18).


**LOS Fields (read from state):**

| Encompass Field | Field ID | Key | Purpose |
|-----------------|----------|-----|---------|
| EMD Amount | `186` | `emd_amount` | Match against Purchase Contract EMD row |

**Document Types:**
- **Purchase Agreement**:
  - `emd_amount_pa`
  - `payment_terms`
  - `emd_payable_to`

**Business Rules:**
- **EMD Amount Matches Purchase Contract** (field_comparison): EMD amount in Section 2b must match Purchase Contract EMD row. Flag mismatch and missing check copy for Realtor email at Step 18.


**Flags — raise when conditions are met:**
- WARNING: "EMD Amount Mismatch"
  - Condition: EMD in Encompass doesn't match Purchase Contract
  - Remedy: Correct EMD amount to match Purchase Contract
- WARNING: "EMD Check Copy Missing"
  - Condition: Copy of EMD check not in eFolder
  - Remedy: Email Realtor to request copy of EMD check (auto-sends at Step 18)

After completing this substep, call:
```
write_todo(step_id="STEP_05", substep_id="5.2", status="completed", notes="<detailed report with every check result, field IDs/values, and flags>")
```

---

### Substep 5.3 - Liabilities and VOL (2c)
**Tool**: `review_urla_liabilities`

Review Section 2c — Liabilities (VOL). Check Excluded Monthly Payment and To Be Paid Off columns. Flag excluded debts for explanation. Flag any debts to be paid off that require most recent statement.


**LOS Fields (read from state):**

| Encompass Field | Field ID | Key | Purpose |
|-----------------|----------|-----|---------|
| Total Monthly Liabilities | `350` | `total_monthly_payments` | Check for excluded or paid-off items |

**Document Types:**
- **VOL**:
  - `excluded_debts`
  - `debts_to_be_paid_off`

**Business Rules:**
- **Excluded Debt Explained** (custom): Column 1 (Excluded Monthly Payment = Y): verify why excluded. Was it already paid off?

- **Paid-Off Debt Statement Required** (custom): Column 2 (To Be Paid Off = Y): request most recent credit card/loan statement (e.g. JPMCB card). Usually for cash-out refis.


**Flags — raise when conditions are met:**
- WARNING: "Excluded Debt Needs Explanation"
  - Condition: Debt excluded from monthly payment without documented reason
  - Remedy: Document why debt is excluded (paid off, not obligated, etc.)
- WARNING: "Payoff Statement Required"
  - Condition: Debt marked To Be Paid Off but payoff statement missing
  - Remedy: Request most recent statement for debt to be paid off

After completing this substep, call:
```
write_todo(step_id="STEP_05", substep_id="5.3", status="completed", notes="<detailed report with every check result, field IDs/values, and flags>")
```

---

### Substep 5.4 - Other Assets and REO (2d, Section 3)
**Tool**: `review_urla_reo`

Review 2d (Other Assets — usually marked No) and Section 3 (REO). If borrower owns other real property, verify mortgage statement, insurance deck page, HOA statement, and tax bill are present.


**LOS Fields (read from state):**

| Encompass Field | Field ID | Key | Purpose |
|-----------------|----------|-----|---------|
| Number of Owned Properties (REO) | `558` | `owned_properties_count` | Determine if REO documentation is needed |

**Document Types:**
- **Mortgage Statement**:
  - `mortgage_statement_present`

**Business Rules:**
- **REO Documentation** (custom): If borrower owns other property (REO count > 0), verify: mortgage statement, insurance deck page, HOA statement, tax bill. If count = 0, skip.


**Flags — raise when conditions are met:**
- WARNING: "REO Documents Missing"
  - Condition: Borrower has REO properties but supporting docs are absent
  - Remedy: Obtain mortgage statement, insurance, HOA, and tax bill for each REO

After completing this substep, call:
```
write_todo(step_id="STEP_05", substep_id="5.4", status="completed", notes="<detailed report with every check result, field IDs/values, and flags>")
```

---

## Step Completion

When ALL substeps above are completed:
1. Call `save_step_report(step_name="STEP_05", status="completed", ...)`
2. Call `write_todo(step_id="STEP_05", status="completed")` to advance to the next step
3. Call `write_todo(step_id="STEP_06", status="in_progress")` to start STEP_06 (1003 URLA Part 4)

⚠️ You MUST call write_todo to advance — do NOT produce a text-only response.
