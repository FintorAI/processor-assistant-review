## Purpose

Run Ocrolus income calculation and generate Income Calc. Save results back to eFolder. Depends on Step 11 (eFolder cleaned first).


**NOTE**: Each substep has its own dedicated tool. State (`los_fields`, `doc_fields`, `loan_summary`) is automatically injected.

## Available Tools

| Tool | Purpose |
|------|---------|
| `run_additional_services` | Run Ocrolus and Income Calc |

## Overview

| Substep | Description | Tool |
|---------|-------------|------|
| 12.1 | Run Ocrolus and Income Calc | `run_additional_services` |

## Tool Calls

```python
# Substep 12.1 - Run Ocrolus and Income Calc
run_additional_services(loan_guid=loan_id)
```

---

## Substeps

### Substep 12.1 - Run Ocrolus and Income Calc
**Tool**: `run_additional_services`

Trigger Ocrolus run to process income documents (paystubs, W2s, tax returns). After Ocrolus completes, generate the Income Calc (1084 worksheets etc.). Save Income Calc PDF back to the Income Calc eFolder bucket.


**LOS Fields (read from state):**

| Encompass Field | Field ID | Key | Purpose |
|-----------------|----------|-----|---------|
| Mortgage Type | `1172` | `loan_type` | Income calculation method may vary by loan type |
| Gross Monthly Income - Base | `1072` | `base_monthly_income` | Baseline for Ocrolus comparison |

**Document Types:**
- **Paystubs** (ALL COPIES):
  - `paystub_ytd`
  - `paystub_pay_period`
- **W2** (ALL COPIES):
  - `w2_wages`
- **Income Calc**:
  - `income_calc_monthly`
  - `income_calc_run_date`

**Business Rules:**
- **Ocrolus Run Triggers Income Calc** (custom): Run Ocrolus via the income ordering service. After completion, trigger Income Calc generation. Save Income Calc to eFolder Income Calc bucket.


**Flags — raise when conditions are met:**
- WARNING: "Ocrolus Run Failed"
  - Condition: Ocrolus run returned an error or did not complete
  - Remedy: Retry Ocrolus or manually calculate income
- WARNING: "Income Calc Not Generated"
  - Condition: Income Calc document not found in eFolder after run
  - Remedy: Manually trigger Income Calc generation and save to eFolder

After completing this substep, call:
```
write_todo(step_id="STEP_12", substep_id="12.1", status="completed", notes="<detailed report with every check result, field IDs/values, and flags>")
```

---

## Step Completion

When ALL substeps above are completed:
1. Call `save_step_report(step_name="STEP_12", status="completed", ...)`
2. Call `write_todo(step_id="STEP_12", status="completed")` to advance to the next step
3. Call `write_todo(step_id="STEP_13", status="in_progress")` to start STEP_13 (Print 1003 and Transmittal)

⚠️ You MUST call write_todo to advance — do NOT produce a text-only response.
