## Purpose

Clean the eFolder before running Ocrolus and fresh AUS. Delete junk buckets: UW, MI Quote, Ocrolus Processing, Paystubs, W2. MUST run before Steps 12 and 14.


**NOTE**: Each substep has its own dedicated tool. State (`los_fields`, `doc_fields`, `loan_summary`) is automatically injected.

## Available Tools

| Tool | Purpose |
|------|---------|
| `prep_efolder` | Prep eFolder - Delete Junk Buckets |

## Overview

| Substep | Description | Tool |
|---------|-------------|------|
| 11.1 | Prep eFolder - Delete Junk Buckets | `prep_efolder` |

## Tool Calls

```python
# Substep 11.1 - Prep eFolder - Delete Junk Buckets
prep_efolder(loan_guid=loan_id)
```

---

## Substeps

### Substep 11.1 - Prep eFolder - Delete Junk Buckets
**Tool**: `prep_efolder`

Delete the following eFolder buckets to clear stale data before new runs: UW (prior AUS findings), MI Quote, Ocrolus Processing, Paystubs (old), W2 (old). This ensures fresh Ocrolus and AUS uploads land in clean buckets.


**Document Types:**
- **Underwriting (DU / LP)** (ALL COPIES):
  - `aus_doc_ids`
- **MI Quote** (ALL COPIES):
  - `mi_quote_doc_ids`
- **Ocrolus Processing** (ALL COPIES):
  - `ocrolus_doc_ids`
- **Paystubs** (ALL COPIES):
  - `paystub_doc_ids`
- **W2** (ALL COPIES):
  - `w2_doc_ids`

**Business Rules:**
- **Delete Specified Buckets** (custom): Delete all docs in: UW, MI Quote, Ocrolus Processing, Paystubs, W2 buckets. These will be repopulated by Steps 12 (Ocrolus) and 14 (fresh AUS).


**Flags — raise when conditions are met:**
- WARNING: "eFolder Cleanup Incomplete"
  - Condition: One or more junk buckets could not be deleted
  - Remedy: Manually delete the bucket contents before running Step 12 or 14

After completing this substep, call:
```
write_todo(step_id="STEP_11", substep_id="11.1", status="completed", notes="<detailed report with every check result, field IDs/values, and flags>")
```

---

## Step Completion

When ALL substeps above are completed:
1. Call `save_step_report(step_name="STEP_11", status="completed", ...)`
2. Call `write_todo(step_id="STEP_11", status="completed")` to advance to the next step
3. Call `write_todo(step_id="STEP_12", status="in_progress")` to start STEP_12 (Order Additional Services)

⚠️ You MUST call write_todo to advance — do NOT produce a text-only response.
