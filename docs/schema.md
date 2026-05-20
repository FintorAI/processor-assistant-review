# Review Agent — Input / Output Schema

This document describes the exact contract for invoking the `review` agent and
reading its results. The orchestrator and UI must conform to this contract.

---

## Invocation (LangGraph Thread Input)

The agent is started by creating a LangGraph thread with the following input.
Required fields are enforced by `ProcessorAgentState`.

```json
{
  "loan_number": "2604964148",
  "env": "Prod",
  "borrower_name": "Cyndy Appell Jermain",
  "additional_info": {
    "almas_notes": "...",
    "loan_id": "0823c494-3b70-451b-88bb-bb79b4d9140b"
  }
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `loan_number` | `str` | ✅ | Encompass loan number (e.g. `"2604964148"`) — NOT the GUID |
| `env` | `str` | ✅ | `"Prod"` or `"Test"` — selects the Encompass environment |
| `borrower_name` | `str` | — | Display name for borrower (used in cover letter + UI context) |
| `additional_info` | `dict` | — | Pre-fetched context from the orchestrator (see below) |

### `additional_info` keys

| Key | Type | Description |
|---|---|---|
| `loan_id` | `str` | Encompass loan GUID — if provided, `find_loan` is skipped |
| `almas_notes` | `str` | LOA notes from ALMAS system — used by `draft_cover_letter` to populate `CX.KM.SUBMISSION.NOTES` |

---

## Internal State (populated during run)

These keys are written to the LangGraph thread state as tools execute.
They are **not** in the input — they carry data between tools.

| State Key | Type | Populated by | Description |
|---|---|---|---|
| `loan_id` | `str` | `find_loan` | Encompass GUID (UUID) resolved from `loan_number` |
| `los_fields` | `dict` | `fetch_los_fields` | All 236 LOS fields keyed by snake_case key (see below) |
| `doc_fields` | `dict` | `fetch_doc_fields` | Document extraction fields (e.g. `purchase_price_doc`) keyed by doc type |
| `efolder_documents` | `list[dict]` | `fetch_doc_fields` | Raw eFolder document list from Encompass |
| `loan_summary` | `dict` | `build_loan_summary` | Structured URLA summary built from `los_fields` + `almas_notes` |
| `address_validation` | `dict` | `validate_property_address` | USPS address validation result (see below) |
| `vod_data` | `list[dict]` | `fetch_los_fields` → tools | Cached VOD records fetched by tools |
| `flags` | `list[dict]` | All review tools | Accumulated flags from all substeps (see Flag schema below) |
| `pending_field_updates` | `list[dict]` | Form update tools | Field writes staged but not yet committed |
| `los_errors` | `list[str]` | `fetch_los_fields` | Field IDs that failed to read from Encompass (logged, not blocking) |

### `los_fields` entry shape

Each entry in `los_fields` is a dict keyed by the snake_case key defined in `FIELD_MAP`:

```json
{
  "borrower_marital_status": {
    "value": "Unmarried",
    "field_id": "52",
    "field_name": "Borrower Marital Status",
    "category": "borrower_info"
  }
}
```

### `address_validation` shape

```json
{
  "valid": true,
  "address": "2814 Carlisle Dr Unit 18, New Windsor, MD 21776",
  "usps_response": { ... }
}
```

If the property address was not available in `los_fields` at validation time:

```json
{
  "valid": null,
  "skipped": true,
  "skip_reason": "Property address not in state — los_fields may not be populated"
}
```

---

## Output (read from thread state after run)

The orchestrator reads the following keys from the final LangGraph thread state:

| Key | Type | Description |
|---|---|---|
| `flags` | `list[dict]` | **Primary output.** All flags raised across all substeps. |
| `los_fields` | `dict` | Final LOS field values (post any writes) |
| `loan_summary` | `dict` | Structured URLA summary for display / HITL review |
| `pending_field_updates` | `list[dict]` | Field writes proposed but not committed (for HITL approval) |

---

## Flag Schema

Every flag raised by a review tool conforms to this dict shape:

```json
{
  "substep": "2.1",
  "substep_name": "Review Borrower Summary - Origination",
  "title": "Email Missing",
  "severity": "warning",
  "details": "Borrower email address (field 1240) is blank.",
  "suggestion": "Obtain and enter borrower email address.",
  "resolved": false,
  "timestamp": "2026-05-19T03:04:08.847318+00:00",
  "relevant_documents": ["VOE"]
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `substep` | `str` | ✅ | Substep ID, e.g. `"2.1"` |
| `substep_name` | `str` | — | Human-readable substep name (added by agent orchestrator) |
| `title` | `str` | ✅ | Short flag title shown in the UI |
| `severity` | `str` | ✅ | One of: `info`, `info-overwrite`, `warning`, `critical`, `blocking` |
| `details` | `str` | ✅ | Full description of the issue |
| `suggestion` | `str` | ✅ | Action for the processor to take |
| `resolved` | `bool` | ✅ | `true` if the tool itself resolved the issue (e.g. write succeeded) |
| `timestamp` | `str` | ✅ | ISO 8601 UTC timestamp |
| `relevant_documents` | `list[str]` | — | Document types related to this flag (eFolder doc type names) |

### Severity meanings

| Severity | Meaning |
|---|---|
| `info` | Informational — processor should be aware, no action required |
| `info-overwrite` | The agent wrote a field — processor should confirm the value |
| `warning` | Something needs processor attention before submission |
| `critical` | A required item is missing or incorrect — blocks submission |
| `blocking` | A prerequisite API collection has not been created (e.g. no VOL rows) |

---

## Formal Contract (shared Pydantic models)

For structured inter-agent communication, `shared/contracts.py` defines Pydantic
models that wrap the above. These are used by the orchestrator when calling the
agent via `RemoteGraph` rather than the LangGraph UI.

```python
class AgentInput(BaseModel):
    loan_id: str           # Encompass GUID (orchestrator pre-resolves from loan_number)
    action: Optional[str]  # Tool name for one-off; None = full workflow run
    inputs: Optional[dict] # Pre-fetched context (e.g. almas_notes, los_fields)
    processor_name: Optional[str]
    env: Optional[str]     # "Prod" | "Test"

class AgentOutput(BaseModel):
    loan_id: str
    action: Optional[str]
    status: Literal["ok", "failed", "needs_hitl"]
    flags: List[Flag]
    field_writes: List[FieldWrite]
    external_results: List[ExternalResult]   # not used by review agent
    efolder_actions: List[EfolderAction]     # not used by review agent
    errors: List[str]
    summary: Optional[str]
```

See `shared/contracts.py` for complete field definitions.

---

## One-Off Action Invocation

To run a single substep (e.g. from the UI tile), pass `action` in `additional_info`:

```json
{
  "loan_number": "2604964148",
  "env": "Prod",
  "additional_info": {
    "loan_id": "0823c494-3b70-451b-88bb-bb79b4d9140b",
    "action": "review_urla_declarations"
  }
}
```

The agent will run only that substep (after data gathering) and return immediately.
