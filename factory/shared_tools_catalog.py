"""Shared Tools Catalog — Documents all available shared utilities.

This catalog is injected into the LLM Tool Maker's system prompt so Claude
knows what shared utilities are available when generating tool code.
The catalog is structured as a plain-text reference that Claude can use
to import and call the right utilities.
"""

SHARED_TOOLS_CATALOG = """
## Available Shared Utilities

These modules live in the `shared/` package (import as `from shared.<module> import ...`).
Use them instead of reimplementing common operations.

---

### 1. `shared.llm_call` — LLM Reasoning Within Tools

When a tool needs AI judgment (fuzzy matching, text analysis, classification),
use this instead of hardcoded heuristics.

**PREFERRED: `llm_structured_call`** — Define a schema, get back exactly that shape.
Uses Claude's tool-use under the hood to guarantee the response structure.

```python
from shared.llm_call import llm_structured_call, llm_call, llm_json_call, llm_classify, llm_compare

# ═══ STRUCTURED RESPONSE (RECOMMENDED for most tool use cases) ═══
# Define the exact shape you want back. Claude is forced to return it.
data = llm_structured_call(
    prompt="Compare borrower name in LOS vs the document",
    schema={
        "match": {"type": "boolean", "description": "Whether the names match"},
        "confidence": {"type": "number", "description": "Confidence score 0.0 to 1.0"},
        "los_value": {"type": "string", "description": "Name as it appears in LOS"},
        "doc_value": {"type": "string", "description": "Name as it appears in document"},
        "explanation": {"type": "string", "description": "Brief explanation"},
        "issues": {
            "type": "array",
            "items": {"type": "string"},
            "description": "List of specific discrepancies found",
        },
    },
    system="You are a mortgage document reviewer comparing borrower names.",
    context={"los_name": "John A. Smith", "doc_name": "Smith, John"},
)
# data -> {"match": True, "confidence": 0.95, "los_value": "John A. Smith",
#          "doc_value": "Smith, John", "explanation": "Same person...", "issues": []}
# GUARANTEED to have exactly those keys, or None on failure.

# With enum constraints
severity = llm_structured_call(
    prompt=f"Assess the severity of this mismatch: {mismatch_info}",
    schema={
        "severity": {"type": "string", "enum": ["low", "medium", "high", "critical"]},
        "action": {"type": "string", "description": "Recommended action"},
        "flag": {"type": "boolean", "description": "Should this be flagged?"},
    },
)

# With nested objects
analysis = llm_structured_call(
    prompt="Analyze this insurance policy",
    schema={
        "policy_valid": {"type": "boolean", "description": "Is policy acceptable?"},
        "coverage": {
            "type": "object",
            "properties": {
                "amount": {"type": "number"},
                "deductible": {"type": "number"},
                "type": {"type": "string"},
            },
            "description": "Parsed coverage details",
        },
        "issues": {"type": "array", "items": {"type": "string"}},
    },
)

# Shorthand schema — just field name: description (all become strings)
quick = llm_structured_call(
    prompt="Extract the key info",
    schema={
        "borrower_name": "Full name of primary borrower",
        "loan_amount": "Loan amount as a string",
        "property_state": "Two-letter state code",
    },
)

# ═══ SIMPLE TEXT RESPONSE ═══
result = llm_call(
    prompt="Analyze this closing condition: ...",
    system="You are a mortgage underwriter. Be concise.",
    max_tokens=500,
)
# result.text -> str, result.success -> bool, result.usage -> dict

# ═══ LOOSE JSON RESPONSE (less reliable than structured) ═══
data = llm_json_call(
    prompt="Return JSON with match results for: ...",
    system="Return valid JSON only.",
)
# data -> dict | list | None

# ═══ CLASSIFICATION ═══
category = llm_classify(
    text="John A. Smith, Trustee of the Smith Family Trust dated 1/1/2020",
    categories=["individual", "trust", "corporation", "llc"],
)
# category -> "trust"

# ═══ COMPARISON ═══
match = llm_compare(
    item_a="John Anthony Smith",    # from LOS
    item_b="Smith, John A.",        # from document
    comparison_type="name",         # "name" | "address" | "date" | "amount" | "general"
)
# match -> {"match": True, "confidence": 0.95, "explanation": "Same person, different format"}
```

**When to use which:**
- **`llm_structured_call`** — When you need specific fields back (match results,
  parsed data, severity assessments). This is the safest option. Use it by default.
- **`llm_call`** — When you need free-form text (summaries, explanations).
- **`llm_json_call`** — Legacy; prefer `llm_structured_call` instead.
- **`llm_classify`** — Quick single-category classification.
- **`llm_compare`** — Convenience wrapper for comparing two values.

---

### 2. `shared.encompass_io` — Read/Write Encompass Fields

```python
from shared.encompass_io import read_field, read_fields, write_field, write_fields

# Read a single field
value = read_field(loan_id, "4000", context="borrower_name", state=state)

# Read multiple fields (batch)
values = read_fields(loan_id, ["4000", "4002", "1172"], context="preflight", state=state)
# values -> {"4000": "John", "4002": "Smith", "1172": "Conventional"}

# Write a single field
write_field(loan_id, "CX.ELECTIVE.INS", "No", context="default_set", state=state)

# Write multiple fields (batch)
write_fields(loan_id, {
    "CX.ELECTIVE.INS": "No",
    "CX.CUSTOM.FLAG": "Reviewed",
}, context="field_corrections", state=state)
```

**Note:** Most tools should NOT call write_fields directly during verification.
Instead, append to `field_corrections` in the Command update, and let the
write-back substep handle the actual writes.

---

### 3. `shared.efolder_client` — Read Document Fields (GET only)

```python
from shared.efolder_client import EfolderClient

client = EfolderClient()

# GET /efolder — reads DynamoDB cache, returns extracted fields + DocRepo locations
response = client.get_documents("2512953182", include_fields=True)

for doc in response.get("documents", []):
    doc_type = doc.get("DocType", "")
    status = doc.get("Status", "")
    fields = doc.get("ExtractedFields", {})
    print(f"{doc_type}: status={status}, fields={len(fields)}")
```

**IMPORTANT:** The agent uses GET-only flow (reads DynamoDB cache). We do NOT
call `POST /efolder/direct` (efolderDirectProcess) from the agent. Extraction
is handled separately by the eFolder pipeline. The agent only reads results.

**Supported document types (27+):** 1003 URLA, Credit Report, Title Report,
Compliance Report, Appraisal Report, Purchase Agreement, Closing Disclosure,
HOI Policy, Flood Certificate, Trust Document, Driver's License,
Fraud Report, Tax Return, W2, Pay Stub, Bank Statement, Gift Letter,
VOE, VOD, Note, Deed of Trust, HUD-1, and more.

---

### 4. `shared.field_utils` — Field Access & Loan Utilities

```python
from shared.field_utils import resolve_loan_id, lfs_value, get_fields_from_encompass

# ALWAYS use this for loan_id — prevents LLM hallucination
loan_id = resolve_loan_id(state, param_loan_id, step_label="STEP_02.3")

# Access loan_field_summary (pre-loaded in state)
ctc_status = lfs_value(state, "2305")

# Read from Encompass API with fallback
fields = get_fields_from_encompass(loan_id, ["4000", "4002"], state=state)
```

---

### 5. `shared.usps_validator` — USPS Address Validation

```python
from shared.usps_validator import validate_address, USPSAddressResult

result = validate_address(
    street="123 Main St",
    city="Las Vegas",
    state="NV",
    zip_code="89101",
)
# result.success, result.standardized_address, result.delivery_point
```

---

### 6. `shared.conditions` — Underwriting Conditions

```python
from shared.conditions import get_loan_conditions, create_condition

# Get all conditions (optionally filter PTF only)
result = get_loan_conditions(loan_id, filter_ptf=True)
# result["conditions"], result["count"], result["ptf_count"]
```

---

### 7. `shared.constants` — Loan Type & Field Constants

```python
from shared.constants import LoanType, LoanPurpose, PropertyState

LoanType.is_mvp_supported("Conventional")  # True
LoanType.VA  # "VA"
LoanPurpose.PURCHASE  # "Purchase"
```

---

### 8. `shared.insurance_helpers` — Insurance Date/Coverage Utilities

```python
from shared.insurance_helpers import _parse_date, validate_coverage_dates

parsed = _parse_date("01/15/2025")  # datetime
```

---

### 9. `shared.reporting` — Step Report Generation

```python
from shared.reporting import save_step_report

# Use this at the end of a step to persist detailed results
save_step_report(
    step_name="STEP_02",
    status="completed",
    summary="Verified 6 borrower fields, found 2 mismatches",
    details={...},
)
```

---

### 10. `shared.docrepo` — S3 Document Storage

```python
from shared.docrepo import upload_to_docrepo, upload_json_to_docrepo

# Upload a report for UI access
result = upload_json_to_docrepo(
    data={"findings": [...]},
    filename="step_02_findings.json",
)
# result -> {"url": "https://...", "key": "..."}
```

---

### 11. `shared.document_type_registry` — Document Schema Registry

```python
from shared.document_type_registry import get_document_schema, DOCUMENT_TYPE_REGISTRY

schema = get_document_schema("Closing Disclosure")
# Returns field definitions, schema ID, extraction instructions
```

"""
