# eFolder Document Extraction — How It Works & How to Inspect It

This is the canonical reference for **where document extraction schemas live** and **how to
check what the eFolder service actually returns for a loan**. Read this before debugging any
"field came back empty / document says 0" issue, so we stop going back and forth.

---

## TL;DR

- The eFolder **client** in this repo (`shared/efolder_client.py`) does **NOT** contain field
  schemas. It only sends a list of `documentTypes` + flags to a server-side service.
- **Field schemas live server-side** in the **CatchingDoc / EfolderConnect Direct Process**
  service (the Lambda at `EFOLDER_API_BASE_URL`). That service decides which fields to extract
  per doc type. We change them via the **`LG-docsOrch/devTool/catchingDoc`** toolkit
  (`schema_manager.py` / CLI / Web UI), **not** from this repo.
- **The schema is matched by the EXACT doc-type string the client posts.** The client posts the
  *actual eFolder bucket title* (e.g. `"Bank Statements"` plural), not the canonical name
  (`"Bank Statement"` singular). If no schema is registered under that exact string, the service
  returns only `document_content` (raw OCR) — even though a schema exists under a near-identical
  name. This is why the live schema list has duplicate entries like `Lock Confirmation` +
  `Lock Confirmation Document` and `Evidence of Insurance` ×4: same schema, multiple bucket-title
  variants.
- `output/config/required_docs.json` (`fields_extracted`) and
  `shared/document_type_registry.py` (`fields_provided`) describe what the client **expects**
  to receive and how to normalise it into state — they do **not** drive extraction.
- If a doc returns only `document_content` (count = 1), the most likely cause is a **doc-type /
  bucket-title name mismatch** (no schema registered under the exact posted string), *not*
  necessarily a totally missing schema. Verify with the inspector + `get_schema`.
- To see what's really happening: **`python3 local/inspect_efolder.py <loan#> --env Prod`**.

---

## Architecture

```
this repo (client)                         server-side (EfolderConnect Direct Process Lambda)
──────────────────                         ──────────────────────────────────────────────────
shared/efolder_client.py                   • Per-doc-type extraction schemas (LandingAI / LLM)
  POST /efolder/direct  ──────────────►    • Decides which fields each doc type yields
  GET  /efolder?...&includeFields=true     • Caches results in DynamoDB
        │                                   • Returns { documents: [ { DocType, Status,
        ▼                                                ExtractedFields, ... } ] }
  data_gathering.py (FIELD_MAP / _doc)
  normalises ExtractedFields → state["doc_fields"]
```

### What the client sends (the *only* knobs we control)

From `shared/efolder_client.py::_call_api` — the POST payload:

```python
payload = {
    'clientId':      client_id,      # AWM-prod / AWM-test (from ENCOMPASS_ENV)
    'environment':   environment,    # prod / test
    'selectionMode': selection_mode, # "All" (every attachment) | "Best" (best match)
    'useCache':      use_cache,
    'useLlm':        True,
    'loanNumber':    loan_number,
    'documentTypes': document_types, # list of doc-type NAMES — NO field schema
    'overrideNotFound': override_not_found,
}
```

There is **no field list / schema** in the request. We ask for a *document type*; the service
returns whatever its server-side schema for that type produces. `LG-docsOrch` uses a byte-for-byte
identical client (same 663-line `shared/efolder_client.py`) — confirmed, so this is shared
behaviour across the processor-assistant agents, not specific to this repo.

### Where the doc-type list comes from

`output/config/required_docs_conditions.json` → `get_document_types_for_loan(loan_type, purpose)`
in `efolder_client.py`. Today a single unified fallback returns the full ~30 doc-type list.

### `required_docs.json` is the client's *expectation*, not the schema

`output/config/required_docs.json` lists `fields_extracted` per doc — this is what
`data_gathering.py` normalises into `state["doc_fields"]` and reports in `fields_missing`. It is
documentation of the contract we *expect*; the server may return fewer fields. When the two
disagree, that's an **extraction gap** (see below), not a client bug.

---

## How to inspect extraction for a loan — `local/inspect_efolder.py`

Use this instead of digging through `state.json` by hand.

```bash
# All docs for a loan (fast cache GET):
python3 local/inspect_efolder.py 2605968608 --env Prod

# One doc type, with the MISSING-expected-fields cross-check:
python3 local/inspect_efolder.py 2605968608 --env Prod --doc "Bank Statement"

# Force a fresh extraction (POST) rather than reading cache:
python3 local/inspect_efolder.py 2605968608 --env Prod --extract --doc "Bank Statement"

# Show field values (truncated), not just keys:
python3 local/inspect_efolder.py 2605968608 --env Prod --doc "VOE" --values

# Raw JSON dump:
python3 local/inspect_efolder.py 2605968608 --env Prod --doc "Bank Statement" --json
```

For each document it prints: `DocType`, `Status`, `ExtractedFieldsCount`, the returned field
keys, and — by cross-referencing `required_docs.json` — which **expected fields are missing**.
Docs that come back with only `document_content` are flagged in an **EXTRACTION GAPS** summary.

---

## Worked example: "Bank statements = 0 months" (loan 2605968608, Stumpf)

`run_pre_checks` flagged *"Insufficient Bank Statements — Found 0 month(s)"* even though the
Bank Statement doc was present. Running the inspector:

```
• 'Bank Statements'   status=completed  count=1  ⚠️ ONLY document_content (no structured extraction)
    returned keys (1): ['document_content']
    ✗ MISSING expected fields (26/26) [schema='Bank Statement']:
      ['institution_name', ..., 'statement_period_start', 'statement_period_end',
       'bank_statement_months', 'beginning_balance', 'ending_balance', ...]

• 'VOE - non service provider'   status=completed  count=60
    ✓ all 60 expected fields present
```

**Root cause (corrected after live `get_schema` checks):** it is **NOT** a missing schema. A full
26-field schema *does* exist — but it is registered under `"Bank Statement"` (singular):

```
get_schema("Bank Statement")   → FOUND, 26 extraction properties (all fields required_docs expects)
get_schema("Bank Statements")  → 404 not_found
```

The client posts the **actual eFolder bucket title**, which on this loan is `"Bank Statements"`
(plural — see `efolder_documents."Bank Statement".document_title` in `state.json`). The service
matches schemas by **exact DocumentType string**, finds no `"Bank Statements"` schema, locates the
PDF anyway, and falls back to raw `document_content`. So:

- The attachment IS found (bucket-title match works); only the **schema lookup** misses on the
  singular/plural difference → 0 of 26 structured fields.
- `run_pre_checks` reads `bank_statement_months` → `None` → coerces to `0` → false "0 months".
- `review_urla_assets` (3.1) passes presence but its recency loop over the (empty)
  `statement_period_end` silently no-ops, logging "recency check passed" without checking a date.

### Fixes
1. **Server-side (primary, Option A — via CatchingDoc):** register the existing 26-field schema
   under the plural bucket title `"Bank Statements"` as well. Schema file prepared at
   `LG-docsOrch/devTool/catchingDoc/schemas/Bank_Statements_schema.json`. Push it:
   ```bash
   cd LG-docsOrch/devTool/catchingDoc
   ./catchingDoc.sh schema update "Bank Statements" ./schemas/Bank_Statements_schema.json
   # or: python3 -c "from schema_manager import SchemaManager; \
   #     SchemaManager().update_schema_from_file('Bank Statements', \
   #     'schemas/Bank_Statements_schema.json', updated_by='bankstmt-name-fix')"
   ```
   This matches the existing convention (Lock Confirmation Document, Evidence of Insurance ×4 …)
   of registering one schema under every bucket-title variant. Re-extract with `--no-cache` to
   verify the 26 fields populate.
2. **Client guardrails (this repo, defence-in-depth):**
   - `run_pre_checks.py`: when `bank_statement_months` is `None` (not extracted) but the Bank
     Statement doc is present, don't claim "0 months" — flag *"statement coverage could not be
     determined"* or derive it from `document_content` / copy count.
   - `review_urla_assets.py`: if no `statement_period_end`, flag recency as **unverifiable**
     rather than silently passing.

> **Why not just post `"Bank Statement"` (singular) from the client instead?** The posted string
> must *also* equal the eFolder bucket title for the service to locate the attachment
> (`data_gathering.py` resolves canonical → actual bucket title precisely for this reason). Bucket
> titles vary per loan (`"Bank Statement"` vs `"Bank Statements"`), so no single client-side string
> is safe. Registering the schema under both names server-side fixes every loan regardless of how
> its bucket is titled.

---

## Known extraction gaps (run the inspector to refresh)

| Doc type (service `DocType`) | Expected fields (`required_docs.json`) | Actually returned | Status |
|---|---|---|---|
| `Bank Statements` (plural bucket title) | 26 (statement_period_*, bank_statement_months, balances, deposits) | `document_content` only | 🟠 Schema exists under `Bank Statement` (singular); not registered under the plural posted name → fix: register plural via `catchingDoc` |

> To regenerate this table for any loan: `python3 local/inspect_efolder.py <loan#> --env Prod`
> and read the **EXTRACTION GAPS** summary at the bottom.

---

## Reference: where things live

| Concern | Location |
|---|---|
| eFolder HTTP client (POST/GET) | `shared/efolder_client.py` |
| Doc-type list per loan type | `output/config/required_docs_conditions.json` + `get_document_types_for_loan()` |
| Expected fields per doc (client contract) | `output/config/required_docs.json` (`fields_extracted`) |
| Doc-type → Encompass bucket / aliases | `shared/document_type_registry.py` (no `Bank Statement` entry — bank schema is only in `required_docs.json`) |
| Normalisation of ExtractedFields → state | `output/tools/data_gathering.py` (`FIELD_MAP`, `_doc`, `_doc_all`) |
| **Actual extraction field schemas (source of truth)** | **CatchingDoc**, schemas stored in DynamoDB. Manage via `LG-docsOrch/devTool/catchingDoc` (`schema_manager.py`, `./catchingDoc.sh schema …`, Web UI). Reference JSONs in `devTool/catchingDoc/schemas/`. |
| Read/update a live schema (quick check) | `cd LG-docsOrch/devTool/catchingDoc && python3 -c "from api_client import EfolderAPIClient as A; print(A().get_schema('Bank Statement'))"` |
| Inspection helper | `local/inspect_efolder.py` |
| Field-usage reference convention (sibling repo) | `LG-docsOrch/docs/FILE_CONTACTS_EXTRACTION_SCHEMAS.md` |
