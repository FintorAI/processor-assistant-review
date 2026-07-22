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
| `almas_notes_images` | `list[dict]` | Images attached to Almas' notes, uploaded to DocRepo by the frontend. Each item: `{ "filename", "url", "client_id", "doc_id", "bucket" }`. Step 0.6 (`extract_almas_images`) OCRs each via Claude vision; the text is appended to the cover letter and the image is surfaced as a 7.1 flag reference document. |

#### `almas_notes_images` item shape

```json
{
  "filename": "purchase_agreement_p3.png",
  "url": "https://.../docrepo-signed-url",
  "client_id": "AWM-test",
  "doc_id": "docsorchagent/almas_notes_<...>",
  "bucket": "encompass"
}
```

Only `url` is strictly required (used for OCR). `client_id`/`doc_id`/`bucket` are the
DocRepo coordinates used to build the flag reference document (see
`docs/flag_document_references.md`). After Step 0.6 the list is re-written to
`state["almas_notes_images"]` with each item enriched by `extracted_text` + `ocr_status`.

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
| `almas_notes_images` | `list[dict]` | `extract_almas_images` (0.6) | Input images (from `additional_info`) enriched with `extracted_text` + `ocr_status` via Claude vision |
| `loan_summary` | `dict` | `build_loan_summary` | Structured URLA summary built from `los_fields` + `almas_notes` |
| `address_validation` | `dict` | `review_property_listing` (1.3) | USPS address validation result (see below). Formerly produced by STEP_00 `validate_property_address` (0.5); consolidated into Pre-Checks. |
| `property_verification` | `dict` | `review_property_listing` (1.3) | Zillow/HasData PUD + new-construction signals (see below). Consumed by Transmittal Summary 11.1, FHA Management 12.1, HUD Transmittal 12.2. |
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

Produced by STEP_01 substep 1.3 (`review_property_listing`). Shape is unchanged
from the former STEP_00 0.5 producer so existing consumers
(`review_borrower_summary`, `review_flood_hazard_insurance`, `build_action_items`)
need no changes.

```json
{
  "valid": true,
  "normalized": "2814 CARLISLE DR UNIT 18 NEW WINDSOR MD 21776",
  "dpv_confirmation": "Y",
  "error": null,
  "warnings": [],
  "mismatch_with_purchase_contract": false,
  "purchase_contract_address": "2814 Carlisle Dr Unit 18",
  "los_address": "2814 Carlisle Dr Unit 18, New Windsor, MD 21776"
}
```

If the property address was not available in `los_fields` at validation time:

```json
{
  "valid": null,
  "skipped": true,
  "skip_reason": "property_address not in los_fields — fetch_los_fields may not have run yet or failed",
  "normalized": null,
  "mismatch_with_purchase_contract": null,
  "purchase_contract_address": null
}
```

### `property_verification` shape

Produced by STEP_01 substep 1.3 (`review_property_listing`) via one HasData
Zillow Listing API call per loan. Downstream tools prefer this over a live
re-lookup (they fall back via `_get_or_detect_property_verification` only when
1.3 was skipped).

```json
{
  "pud": {
    "is_condo": false,
    "pud_signals": ["Zillow home/structure type = TOWNHOUSE", "Zillow shows HOA dues ($88 monthly)"],
    "strong": true,
    "zillow_url": "https://www.zillow.com/homedetails/...",
    "zillow_deep_link": "https://www.zillow.com/homes/...",
    "zillow_subdivision": "Germantown View",
    "zillow_attached": false,
    "zillow_signals": ["Zillow home/structure type = TOWNHOUSE", "Zillow shows HOA dues ($88 monthly)"],
    "appraisal_says_pud": false,
    "hoa_present": true,
    "attached": true,
    "home_type": "TOWNHOUSE",
    "structure_type": "End of Row/Townhouse",
    "hoa_fee": "$88 monthly",
    "found": true,
    "error": null
  },
  "new_construction": {
    "is_new_construction": false,
    "year_built": 1983,
    "zillow_flag": false,
    "zillow_url": "https://www.zillow.com/homedetails/...",
    "confidence": "none",
    "found": true,
    "error": null
  }
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
