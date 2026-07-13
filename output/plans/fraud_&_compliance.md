## Purpose

Review FraudGuard / fraud report alerts and LDP/OFAC clearance documents, then run Mavent compliance via the Encompass ECS API. Covers checklist section 15 — fraud high alerts, known participants / OFAC, Mavent review, and ordering a new Mavent report when none exists.


**NOTE**: Each substep has its own dedicated tool. State (`los_fields`, `doc_fields`, `loan_summary`) is automatically injected.

## Available Tools

| Tool | Purpose |
|------|---------|
| `review_fraud_compliance` | Review Fraud & LDP |
| `run_mavent_compliance` | Run Mavent Compliance |

## Overview

| Substep | Description | Tool |
|---------|-------------|------|
| 14.1 | Review Fraud & LDP | `review_fraud_compliance` |
| 14.2 | Run Mavent Compliance | `run_mavent_compliance` |

## Tool Calls

```python
# Substep 14.1 - Review Fraud & LDP
review_fraud_compliance(loan_guid=loan_id)
# Substep 14.2 - Run Mavent Compliance
run_mavent_compliance(loan_guid=loan_id)
```

---

## Substeps

### Substep 14.1 - Review Fraud & LDP
**Tool**: `review_fraud_compliance`

Review extracted Fraud Report fields for high alerts and confirm LDP is on file for known-participants / OFAC clearance. Read-only — does not order fraud or LDP reports.


**Document Types:**
- **Fraud Report**:
  - `fraud_alert_status`
  - `fraud_score`
  - `borrower_name`
  - `borrower_ssn`
- **LDP**:
  - `ldp_present`

**Business Rules:**
- **Fraud High Alerts** (value_check): When a Fraud Report is in the eFolder, review fraud_alert_status and fraud_score. Flag HIGH/ALERT/FAIL statuses and elevated scores for processor review.

- **LDP Present** (existence_check): LDP document should be on file before submission. Warn when absent.


**Flags — raise when conditions are met:**
- WARNING: "Fraud Report High Alert"
  - Condition: fraud_alert_status indicates high risk or fraud_score exceeds threshold
  - Remedy: Review FraudGuard alerts and clear or escalate before submission
- WARNING: "LDP Missing"
  - Condition: LDP not present in eFolder
  - Remedy: Obtain LDP/GSA before submission

After completing this substep, call:
```
write_todo(step_id="STEP_14", substep_id="14.1", status="completed", notes="<detailed report with every check result, field IDs/values, and flags>")
```

---

### Substep 14.2 - Run Mavent Compliance
**Tool**: `run_mavent_compliance`

GET existing ECS Mavent report or POST a new Review report when missing. Surface per-category flags and persist full compliance messages in state.mavent_results for the dashboard detail panel (15 #3, #4).


**Business Rules:**
- **Mavent Category Review** (custom): Parse BaseReviewerStatuses from ECS API. Flag FAIL/ALERT/WARNING per applicable category; enrich with ComplianceMessages. Persist full structured results in mavent_results for dashboard display.


**Flags — raise when conditions are met:**
- CRITICAL: "Mavent Category Non-Pass"
  - Condition: Any applicable Mavent category is FAIL
  - Remedy: Resolve in Encompass Compliance Review, then rerun Mavent
- WARNING: "Mavent Overall Non-Pass"
  - Condition: ECS report_status is not Pass
  - Remedy: Review category flags and rerun Mavent after fixes

After completing this substep, call:
```
write_todo(step_id="STEP_14", substep_id="14.2", status="completed", notes="<detailed report with every check result, field IDs/values, and flags>")
```

---

## Step Completion

When ALL substeps above are completed:
1. Call `save_step_report(step_name="STEP_14", status="completed", ...)`
2. Call `write_todo(step_id="STEP_14", status="completed")` to advance to the next step

⚠️ You MUST call write_todo to advance — do NOT produce a text-only response.
