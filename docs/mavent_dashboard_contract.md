# Mavent Dashboard Contract

Frontend spec for displaying full Mavent ECS results from the processor review agent.

**Related:** `Dashboard-Officer/docs/processor_mavent_ui_plan.md`

---

## State keys

| Key | Written by | Purpose |
|-----|------------|---------|
| `mavent_results` | `run_mavent_compliance` (STEP_13.2) | Full structured ECS data for detail panel |
| `mavent_verification` | `run_mavent_compliance` | Run summary counts and overall status |
| `flags[]` | `run_mavent_compliance` | Short per-category cards (`check_id`: `mavent_*`) |
| `comms_actions[]` | `build_action_items` (14.3) | Rerun tile when `action_type === "rerun_mavent"` |

---

## `mavent_results`

```jsonc
{
  "run_date": "2026-07-13T10:00:00Z",
  "report_status": "Alert",
  "categories": {
    "HMDA": "FAIL",
    "State Rules": "WARNING",
    "ATR/QM": "PASS"
  },
  "compliance_messages_by_category": {
    "HMDA": [
      {
        "status": "Fail",
        "message": "Full compliance message text — not truncated",
        "service_group": "HMDA"
      }
    ]
  },
  "api_ran": true
}
```

---

## `mavent_verification`

```jsonc
{
  "success": true,
  "status": "fail",
  "tool": "run_mavent_compliance",
  "report_status": "Alert",
  "ordered_date": "2026-07-13T10:00:00Z",
  "api_ran": true,
  "category_count": 12,
  "passed": false,
  "fail_count": 1,
  "warning_count": 2,
  "info_count": 9,
  "message": "Mavent compliance (Alert): 1 fails, 2 warnings, 9 info — 12 categories evaluated"
}
```

---

## Flag `evidence` (non-pass categories)

```jsonc
{
  "category": "HMDA",
  "report_status": "Alert",
  "message_count_total": 5,
  "messages_shown": 3,
  "messages": [
    { "status": "Fail", "message": "…", "service_group": "HMDA" }
  ]
}
```

`flag.details` remains a **short summary** for the flag list. Use `evidence.messages` or `mavent_results.compliance_messages_by_category` for full text.

---

## Rerun action item

```jsonc
{
  "id": "rerun_mavent",
  "component": "integrations",
  "action_type": "rerun_mavent",
  "trigger": {
    "agent": "processor_assistant_review",
    "tool": "run_mavent_compliance",
    "resume_contract": "integration_rerun",
    "payload": {
      "loan_id": "<guid>",
      "loan_number": "<number>",
      "env": "Prod",
      "force_refresh": true
    }
  }
}
```

Invoke the review agent with `force_refresh: true` to skip GET and order a fresh ECS report.
