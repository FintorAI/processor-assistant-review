## Purpose

Send conditional notification emails. Lock Desk email if locked + address change discovered at Step 2. Request Locked LE if locked + no locked LE. EMD check email to Realtor if EMD missing or amount mismatch flagged at Step 5.


**NOTE**: Each substep has its own dedicated tool. State (`los_fields`, `doc_fields`, `loan_summary`) is automatically injected.

## Available Tools

| Tool | Purpose |
|------|---------|
| `send_lock_desk_email` | Lock Desk Email (if locked + address change) |
| `request_locked_le` | Request Locked LE (if locked + no Locked LE) |
| `send_emd_email` | EMD Email to Realtor (if EMD missing or mismatch) |

## Overview

| Substep | Description | Tool |
|---------|-------------|------|
| 18.1 | Lock Desk Email (if locked + address change) | `send_lock_desk_email` |
| 18.2 | Request Locked LE (if locked + no Locked LE) | `request_locked_le` |
| 18.3 | EMD Email to Realtor (if EMD missing or mismatch) | `send_emd_email` |

## Tool Calls

```python
# Substep 18.1 - Lock Desk Email (if locked + address change)
send_lock_desk_email(loan_guid=loan_id)
# Substep 18.2 - Request Locked LE (if locked + no Locked LE)
request_locked_le(loan_guid=loan_id)
# Substep 18.3 - EMD Email to Realtor (if EMD missing or mismatch)
send_emd_email(loan_guid=loan_id)
```

---

## Substeps

### Substep 18.1 - Lock Desk Email (if locked + address change)
**Tool**: `send_lock_desk_email`

Send email to Lock Desk if the loan is locked AND an address change was discovered at Step 2 (property address mismatch flag). Uses email template from recipients.json (Almas Younis, Ash Desai).


**LOS Fields (read from state):**

| Encompass Field | Field ID | Key | Purpose |
|-----------------|----------|-----|---------|
| Loan Locked Status | `CX.LOAN.LOCKED` | `loan_locked` | Only send email if loan is locked |
| Lock Expiration Date | `762` | `lock_expiration` | Include in email body |

**Business Rules:**
- **Send Only If Locked and Address Changed** (custom): If loan_locked == True AND address_change_flag raised at Step 2: send Lock Desk notification email. Otherwise, dynamically skip.


**Flags — raise when conditions are met:**
- INFO: "Lock Desk Email Not Sent"
  - Condition: Locked loan with address change but Lock Desk email not sent
  - Remedy: Manually send Lock Desk email with updated property address

After completing this substep, call:
```
write_todo(step_id="STEP_18", substep_id="18.1", status="completed", notes="<detailed report with every check result, field IDs/values, and flags>")
```

---

### Substep 18.2 - Request Locked LE (if locked + no Locked LE)
**Tool**: `request_locked_le`

Send email requesting a Locked Loan Estimate if the loan is locked but no Locked LE is present in eFolder. Uses recipients.json contacts.


**LOS Fields (read from state):**

| Encompass Field | Field ID | Key | Purpose |
|-----------------|----------|-----|---------|
| Loan Locked Status | `CX.LOAN.LOCKED` | `loan_locked` | Only send if locked |
| Locked LE Present | `CX.LOCKED.LE.PRESENT` | `locked_le_present` | Skip if Locked LE already in file |

**Document Types:**
- **Lock Confirmation**:
  - `lock_confirmation_present`

**Business Rules:**
- **Send Only If Locked and No Locked LE** (custom): If loan_locked == True AND locked_le_present == False: send Locked LE request email. Otherwise, dynamically skip.


**Flags — raise when conditions are met:**
- INFO: "Locked LE Request Not Sent"
  - Condition: Locked loan without Locked LE and email not sent
  - Remedy: Manually request Locked LE from LO/LP

After completing this substep, call:
```
write_todo(step_id="STEP_18", substep_id="18.2", status="completed", notes="<detailed report with every check result, field IDs/values, and flags>")
```

---

### Substep 18.3 - EMD Email to Realtor (if EMD missing or mismatch)
**Tool**: `send_emd_email`

Send email to the Realtor requesting EMD check copy if EMD missing or amount mismatch was flagged at Step 5. Realtor is payable-to entity from Purchase Contract. Copy recipients per recipients.json.


**LOS Fields (read from state):**

| Encompass Field | Field ID | Key | Purpose |
|-----------------|----------|-----|---------|
| Realtor Email | `CX.REALTOR.EMAIL` | `realtor_email` | Email recipient for EMD check request |
| EMD Amount | `186` | `emd_amount` | Include correct EMD amount in email |

**Document Types:**
- **Purchase Agreement**:
  - `emd_payable_to`
  - `emd_amount_pa`

**Business Rules:**
- **Send Only If EMD Flag Raised at Step 5** (custom): If EMD-missing or EMD-mismatch flag is in state["flags"] from Step 5: send Realtor email requesting EMD check copy. Otherwise, dynamically skip.


**Flags — raise when conditions are met:**
- INFO: "EMD Email Not Sent"
  - Condition: EMD flag raised but Realtor email not sent
  - Remedy: Manually email Realtor to request EMD check copy

After completing this substep, call:
```
write_todo(step_id="STEP_18", substep_id="18.3", status="completed", notes="<detailed report with every check result, field IDs/values, and flags>")
```

---

## Step Completion

When ALL substeps above are completed:
1. Call `save_step_report(step_name="STEP_18", status="completed", ...)`
2. Call `write_todo(step_id="STEP_18", status="completed")` to advance to the next step

⚠️ You MUST call write_todo to advance — do NOT produce a text-only response.
