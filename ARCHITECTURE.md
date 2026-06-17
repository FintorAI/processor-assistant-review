# Processor-Assistant: Multi-Agent Architecture

## Overview

The processor-assistant system is divided into four independently deployable
LangGraph Cloud agents. Each agent owns a well-defined slice of the mortgage
loan submission workflow. Agents communicate through a shared input/output
contract and, at the orchestration layer, via LangGraph's `RemoteGraph`.

```
┌───────────────────────────────────────────────────────────────────────┐
│  Processor UI (chat + per-action tiles)                               │
└──┬──────────────────────┬─────────────────────┬──────────────────────┘
   │ full workflow         │ one-off action       │ one-off action
   ▼                       │                      │
┌────────────────────┐     │                      │
│  orchestrator      │─────┘──────────────────────┘
│  (HITL + dispatch) │
└──┬───────┬────┬────┘
   │       │    │   RemoteGraph calls
   ▼       ▼    ▼
┌──────┐ ┌────────────────┐ ┌──────────────┐
│review│ │  integrations  │ │ computer-use │
└──────┘ └────────────────┘ └──────────────┘
   │              │                 │
   └──────────────┴─────────────────┘
                  ▼
         Encompass (canonical LOS)
         S3 (document storage)
         LangSmith (observability)
```

**Deployment → repo mapping:**

| Deployment | Repo | Substep ownership |
|---|---|---|
| `review` | `processor-assistant-review` | Steps 0–9, 15, 17.3 |
| `integrations` | `processor-assistant-integrations` | Steps 10.3, 12, 14, 18 |
| `computer-use` | `processor-assistant-computer-use` | Steps 11, 13 |
| `orchestrator` | `processor-assistant-orchestrator` | Step 16 HITL + dispatch |

**TBD (pending Encompass API research):** Steps 10.1, 10.2, 17.1, 17.2, 17.4 —
will be routed to `integrations` if an API exists, otherwise `computer-use`.

---

## Design Principles

### 1. Bootstrap from `loan_id` alone

Every agent must be able to run given only a `loan_id`. In workflow mode the
orchestrator pre-fills known inputs; in one-off mode the agent fetches what it
needs from Encompass directly. No agent depends on another agent being
called first.

### 2. Encompass is canonical

No durable cross-agent state store at v1. Encompass holds the source-of-truth
for all LOS fields. Each deployment's LangGraph checkpointer holds run-local
working memory. LangSmith provides cross-run observability.

A cross-loan Postgres store (`loan_runs`) is documented in
`docs/future_loan_runs_schema.md` and will be added when a dashboard is built.

### 3. One contract, every agent

All agents accept `AgentInput` and return `AgentOutput` (Pydantic models in
`shared/contracts.py`). This makes every agent uniformly callable from the
orchestrator and from the UI.

### 4. Actions are first-class

Setting `action="order_appraisal"` in `AgentInput` runs just that substep.
Setting `action=None` runs the agent's full internal workflow. The same HTTP
endpoint and the same code path handles both cases — the orchestrator just
omits `action` when dispatching a full-phase run.

---

## Agent Input / Output Contract

Defined in `shared/contracts.py`. Summarized here for reference.

### AgentInput

```python
class AgentInput(BaseModel):
    loan_id: str           # Encompass loan GUID (UUID)
    action: Optional[str]  # Tool name for one-off; None = full run
    inputs: Optional[Dict] # Pre-fetched fields from orchestrator
    processor_name: Optional[str]
    env: Optional[str]     # "Prod" | "Test"
```

### AgentOutput

```python
class AgentOutput(BaseModel):
    loan_id: str
    action: Optional[str]
    status: Literal["ok", "failed", "needs_hitl"]
    flags: List[Flag]
    field_writes: List[FieldWrite]
    external_results: List[ExternalResult]  # integrations agent
    efolder_actions: List[EfolderAction]    # computer-use agent
    errors: List[str]
    summary: Optional[str]
```

### Flag

```python
class Flag(BaseModel):
    code: str              # e.g. "EMD_AMOUNT_MISMATCH"
    severity: "info" | "info-overwrite" | "warning" | "critical"
    substep_id: str        # e.g. "5.2"
    substep_name: str
    message: str
    payload: Optional[Dict]
    resolved: bool
    resolution_note: Optional[str]
```

**Canonical flag severities** (workflow tools only):

| Severity | Meaning |
|---|---|
| `critical` | Hard blocker — the workflow cannot proceed correctly without resolution (e.g. VOE form empty, required doc missing). Checked by `run_pre_checks` to halt early. |
| `warning` | Needs processor attention or manual action, but does not halt the workflow. |
| `info` | Informational — field verified, value confirmed, or FYI context for the processor. |
| `info-overwrite` | Emitted automatically by `_write_fields()` each time a field is written to Encompass. Provides a per-field audit trail. |

> **`action`** is reserved for `comms_actions` items (STEP_11.3 `build_action_items`) only — it is
> not a workflow flag severity and must not be used in review/update tools.

### FieldWrite

```python
class FieldWrite(BaseModel):
    field_id: str          # Encompass field ID
    field_name: str
    old_value: Any
    new_value: Any
    committed: bool        # True = written to Encompass; False = staged
    substep_id: str
```

---

## Orchestrator Routing Table

The orchestrator maps each named action to a `(deployment_url, graph_name)`.
Flip a substep between `integrations` and `computer-use` by changing one entry
here — no other code changes required.

```python
# processor-assistant-orchestrator/routing.py
ACTION_ROUTES: dict[str, tuple[str, str]] = {
    # review agent
    "run_pre_checks":           (REVIEW_URL, "review"),
    "review_borrower_summary":  (REVIEW_URL, "review"),
    "review_urla_page1":        (REVIEW_URL, "review"),
    "review_urla_employment":   (REVIEW_URL, "review"),
    "review_urla_other_income": (REVIEW_URL, "review"),
    "review_urla_assets":       (REVIEW_URL, "review"),
    "review_urla_emd":          (REVIEW_URL, "review"),
    "review_urla_liabilities":  (REVIEW_URL, "review"),
    "review_urla_reo":          (REVIEW_URL, "review"),
    "review_urla_downpayment":  (REVIEW_URL, "review"),
    "review_urla_declarations": (REVIEW_URL, "review"),
    "review_urla_ethnicity":    (REVIEW_URL, "review"),
    "draft_cover_letter":       (REVIEW_URL, "review"),
    "update_borrower_vesting":  (REVIEW_URL, "review"),
    "update_transmittal_summary": (REVIEW_URL, "review"),
    "update_processor_workflow":  (REVIEW_URL, "review"),
    "update_processor_closing":   (REVIEW_URL, "review"),
    "update_milestone":           (REVIEW_URL, "review"),

    # integrations agent
    "run_additional_services":  (INTEGRATIONS_URL, "integrations"),
    "run_fannie_aus":           (INTEGRATIONS_URL, "integrations"),
    "run_freddie_aus":          (INTEGRATIONS_URL, "integrations"),
    "send_title_order_email":   (INTEGRATIONS_URL, "integrations"),
    "send_lock_desk_email":     (INTEGRATIONS_URL, "integrations"),
    "request_locked_le":        (INTEGRATIONS_URL, "integrations"),
    "send_emd_email":           (INTEGRATIONS_URL, "integrations"),

    # computer-use agent
    "prep_efolder":             (COMPUTER_USE_URL, "computer-use"),
    "print_forms":              (COMPUTER_USE_URL, "computer-use"),

    # TBD — routed to computer-use until API availability is confirmed
    "order_appraisal":          (COMPUTER_USE_URL, "computer-use"),
    "order_condo_questionnaire":(COMPUTER_USE_URL, "computer-use"),
    "mark_docs_ready_for_uw":   (COMPUTER_USE_URL, "computer-use"),
    "complete_required_fields": (COMPUTER_USE_URL, "computer-use"),
    "final_efolder_cleanup":    (COMPUTER_USE_URL, "computer-use"),
}
```

---

## Local Development

Each repo has a `langgraph.json` at its root. Run any agent locally with:

```bash
langgraph dev
```

To run the full multi-agent stack locally, start each agent in a separate
terminal on a different port, then configure the orchestrator's `ACTION_ROUTES`
to point at `http://localhost:<port>` instead of production URLs.

---

## Future: Cross-Loan Postgres Store

A `loan_runs` Postgres schema is documented in `docs/future_loan_runs_schema.md`.
It will be added when a cross-loan dashboard is needed. At v1 LangSmith covers
observability needs.
