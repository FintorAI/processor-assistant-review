"""Step 0 Auto-Generator.

Generates Step 0 (Data Gathering) automatically from the field registry.
Step 0 = "fetch every LOS field and doc field that any subsequent step needs."

This module does NOT create a YAML definition file for Step 0 — instead it
directly produces the StepDef object and generates the tool code + plan.
"""

from __future__ import annotations

import json
import os

from .field_registry import FieldRegistry
from .schema import (
    DevConfig,
    DocFieldRef,
    DocTypeConfig,
    FieldRef,
    StepDef,
    SubstepDef,
)


def generate_step0_definition(registry: FieldRegistry) -> StepDef:
    """Build a Step 0 definition from the field registry.

    Substeps:
        0.1 - Find Loan (find loan GUID by loan_number/borrower_name)
        0.2 - Fetch LOS Fields (batch read all field IDs)
        0.3 - Fetch Doc Fields (extract from required document types)

    Args:
        registry: Populated field registry

    Returns:
        StepDef for STEP_00
    """
    all_los = registry.get_all_field_ids()
    all_doc_types = registry.get_all_doc_types()

    # ── Substep 0.1: Find Loan ──
    substep_01 = SubstepDef(
        id="0.1",
        name="Find Loan",
        tool="find_loan",
        description="Find the loan GUID using the loan number or borrower name.",
    )

    # ── Substep 0.2: Fetch LOS Fields ──
    los_field_refs = []
    for fid in all_los:
        info = registry.get_field_by_id(fid)
        if info:
            los_field_refs.append(
                FieldRef(
                    key=info.key,
                    field_id=info.field_id,
                    field_name=info.field_name,
                    category=info.category,
                    purpose=f"Used by: {', '.join(info.used_by_steps)}",
                )
            )

    substep_02 = SubstepDef(
        id="0.2",
        name=f"Fetch LOS Fields ({len(all_los)} fields)",
        tool="fetch_los_fields",
        description=(
            f"Batch-read {len(all_los)} Encompass field IDs in a single API call. "
            f"Organizes results into state['los_fields'] by key."
        ),
        los_fields_read=los_field_refs,
    )

    # ── Substep 0.3: Fetch Doc Fields ──
    doc_type_configs = []
    for dt_name, dti in sorted(registry.doc_type_info.items()):
        fields = [
            DocFieldRef(key=k, purpose=f"Used by: {', '.join(registry.doc_fields[k].used_by_steps)}")
            for k in dti.fields if k in registry.doc_fields
        ]
        doc_type_configs.append(
            DocTypeConfig(
                document_type=dt_name,
                all_copies=dti.all_copies,
                fields=fields,
            )
        )

    substep_03 = SubstepDef(
        id="0.3",
        name=f"Fetch Doc Fields ({len(all_doc_types)} document types)",
        tool="fetch_doc_fields",
        description=(
            f"Extract fields from {len(all_doc_types)} document types via eFolder. "
            f"Document types: {', '.join(all_doc_types)}. "
            f"Stores results in state['doc_fields'] by key."
        ),
        doc_types=doc_type_configs,
    )

    # ── Substep 0.4: Build Loan Summary (URLA) ──
    substep_04 = SubstepDef(
        id="0.4",
        name="Build Loan Summary (URLA)",
        tool="build_loan_summary",
        description=(
            "Build a categorized loan summary snapshot from los_fields. "
            "This is the URLA equivalent — organized by borrower, property, "
            "loan terms, dates, vesting, and derived flags (has_coborrower, "
            "is_note_llc). Set once in Step 0, never changes afterwards."
        ),
    )

    return StepDef(
        id="STEP_00",
        name="Data Gathering",
        phase="VERIFICATION",
        description=(
            f"Auto-generated step that fetches all data needed by subsequent steps. "
            f"LOS: {len(all_los)} fields, Docs: {len(all_doc_types)} document types. "
            f"Builds loan_summary (URLA) snapshot."
        ),
        substeps=[substep_01, substep_02, substep_03, substep_04],
        dev=DevConfig(skip=False),
    )


def generate_step0_tool_code(registry: FieldRegistry, project_root: str | None = None) -> str:
    """Generate the Python tool code for Step 0 (data_gathering.py).

    Produces four tools:
    - find_loan: Search for loan GUID
    - fetch_los_fields: Batch read all LOS fields
    - fetch_doc_fields: Extract fields from eFolder documents + normalize
    - build_loan_summary: Build URLA-style loan summary

    The generated code includes dynamic document selection based on loan
    characteristics loaded from required_docs_conditions.json at runtime.

    Args:
        registry: Populated field registry
        project_root: Project root dir (unused — kept for API compat). Optional.
    """
    # Build the FIELD_MAP dict entries from the registry (LOS fields)
    field_map_lines = []
    for fid in sorted(registry.los_fields.keys()):
        info = registry.los_fields[fid]
        field_map_lines.append(
            f'    "{fid}": {{"key": "{info.key}", "field_name": "{info.field_name}", "category": "{info.category}"}},'
        )
    field_map_str = "\n".join(field_map_lines)
    los_count = len(registry.los_fields)

    # ── Build code by section concatenation ──
    # This avoids massive f-string brace escaping for the generated Python code.
    # Only FIELD_MAP and LOS_COUNT are registry-derived; everything else is static.

    # Section 1: Module header, imports, dynamic config + condition functions
    part_header = '''\
"""Step 0: Data Gathering — Extended with dynamic document selection.

Originally auto-generated from field registry, then extended with:
  - Dynamic document type selection based on loan characteristics
  - eFolder GET-only flow (no POST /efolder/direct)
  - efolder_documents state population with DocRepo locations

The factory will NOT overwrite this file if it already exists.
To regenerate from scratch, delete this file first, then run `generate --all`.
"""

import json
import logging
import os
from datetime import datetime
from typing import Annotated, Optional

from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.prebuilt import InjectedState
from langgraph.types import Command

logger = logging.getLogger(__name__)

# ── Config directory (output/config/) ──
_CONFIG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config")
_conditions_cache: dict | None = None
_doc_defs_cache: dict | None = None


def _load_conditions_config() -> dict:
    """Load required_docs_conditions.json (cached)."""
    global _conditions_cache
    if _conditions_cache is None:
        path = os.path.join(_CONFIG_DIR, "required_docs_conditions.json")
        with open(path) as f:
            _conditions_cache = json.load(f)
    return _conditions_cache


def _load_doc_definitions() -> dict:
    """Load required_docs.json (cached)."""
    global _doc_defs_cache
    if _doc_defs_cache is None:
        path = os.path.join(_CONFIG_DIR, "required_docs.json")
        with open(path) as f:
            _doc_defs_cache = json.load(f)
    return _doc_defs_cache


def get_doc_field_map() -> dict[str, list[str]]:
    """Build DOC_FIELD_MAP dynamically from required_docs.json.

    Returns dict: { "Credit Report": ["credit_score", "borrower_ssn", ...], ... }
    """
    defs = _load_doc_definitions()
    result: dict[str, list[str]] = {}
    for _key, doc_info in defs.get("documents", {}).items():
        doc_name = doc_info.get("name", "")
        fields = doc_info.get("fields_extracted", [])
        if doc_name and fields:
            result[doc_name] = fields
    return result


def get_required_documents_for_loan(
    loan_type: str,
    loan_purpose: str,
    borrower_count: int,
) -> tuple[list[str], dict[str, str]]:
    """Select required document types based on loan characteristics.

    Returns:
        (document_list, extraction_modes) where extraction_modes maps
        doc names to 'all' when multiple borrowers need full extraction.
    """
    conditions_cfg = _load_conditions_config()
    conditions = conditions_cfg.get("conditions", [])

    lt = (loan_type or "").strip()
    lp = (loan_purpose or "").strip()
    bc = borrower_count or 1

    # Try exact match first
    for entry in conditions:
        cond = entry.get("condition", {})
        if cond.get("fallback"):
            continue
        if (cond.get("loan_type", "").lower() == lt.lower()
                and cond.get("loan_purpose", "").lower() == lp.lower()
                and cond.get("borrower_count", 1) == bc):
            doc_list = entry.get("document_list", [])
            ext_modes = entry.get("extraction_mode", {})
            ext_modes.pop("_comment", None)
            logger.info(
                f"[DOC_SELECT] Matched: {lt}/{lp}/bc={bc} -> {len(doc_list)} docs"
            )
            return doc_list, ext_modes

    # Try match without borrower_count
    for entry in conditions:
        cond = entry.get("condition", {})
        if cond.get("fallback"):
            continue
        if (cond.get("loan_type", "").lower() == lt.lower()
                and cond.get("loan_purpose", "").lower() == lp.lower()):
            doc_list = entry.get("document_list", [])
            ext_modes = entry.get("extraction_mode", {})
            ext_modes.pop("_comment", None)
            logger.info(
                f"[DOC_SELECT] Partial match (type+purpose): {lt}/{lp} -> {len(doc_list)} docs"
            )
            return doc_list, ext_modes

    # Fallback
    for entry in conditions:
        cond = entry.get("condition", {})
        if cond.get("fallback"):
            doc_list = entry.get("document_list", [])
            logger.info(f"[DOC_SELECT] Using fallback -> {len(doc_list)} docs")
            return doc_list, {}

    logger.warning("[DOC_SELECT] No matching condition and no fallback!")
    return [], {}


def _derive_loan_characteristics(state: dict) -> tuple[str, str, int]:
    """Extract loan_type, loan_purpose, borrower_count from state."""
    los = state.get("los_fields", {})
    summary = state.get("loan_summary", {})

    # loan_type
    loan_type = ""
    if summary and summary.get("derived", {}).get("loan_type"):
        loan_type = summary["derived"]["loan_type"]
    elif los.get("preflight_mortgage_type", {}).get("value"):
        loan_type = los["preflight_mortgage_type"]["value"]

    # loan_purpose
    loan_purpose = ""
    if summary and summary.get("derived", {}).get("loan_purpose"):
        loan_purpose = summary["derived"]["loan_purpose"]
    elif los.get("preflight_loan_purpose", {}).get("value"):
        loan_purpose = los["preflight_loan_purpose"]["value"]

    # borrower_count
    borrower_count = 1
    if summary and summary.get("derived", {}).get("has_coborrower"):
        borrower_count = 2
    elif los.get("preflight_has_coborrower", {}).get("value"):
        val = los["preflight_has_coborrower"]["value"]
        if val and str(val).strip():
            borrower_count = 2

    return loan_type, loan_purpose, borrower_count

'''

    # Section 2: FIELD_MAP (dynamically generated from registry)
    part_field_map = (
        "# ── Field mapping: field_id -> {key, field_name, category} ──\n"
        "FIELD_MAP = {\n" + field_map_str + "\n}\n\n"
        "ALL_FIELD_IDS = list(FIELD_MAP.keys())\n\n"
    )

    # Section 3: Dynamic DOC_FIELD_MAP, normalize, all 4 tools, URLA keys
    # Uses __LOS_COUNT__ as placeholder for the registry-derived LOS field count.
    part_body = '''\
# ── Doc field mapping: built dynamically from required_docs.json ──
DOC_FIELD_MAP = get_doc_field_map()

# Flat set of all expected doc field keys (for quick lookup during normalization)
ALL_DOC_FIELD_KEYS = set()
for _keys in DOC_FIELD_MAP.values():
    ALL_DOC_FIELD_KEYS.update(_keys)


def _normalize_efolder_output(
    documents: list[dict],
    field_map: dict[str, list[str]] | None = None,
    multi_copy_types: set[str] | None = None,
) -> dict:
    """Normalize efolderGet (GET /efolder) response into state['doc_fields'] format.

    The GET /efolder API returns DynamoDB records with PascalCase keys:
      DocType, Status, ExtractedFields, DocRepoLocation, etc.

    For multi-copy doc types (extraction_mode="all"), field entries include a
    ``copies`` list so that values from every copy are preserved.

    Args:
        documents: List of document dicts from efolderGet response.
        field_map: Optional override for DOC_FIELD_MAP (defaults to module-level).
        multi_copy_types: Doc types where every copy should be kept (extraction_mode="all").

    Returns:
        Dict keyed by field_key with value, source_document, confidence, all_sources,
        raw_key, and optionally ``copies`` for multi-copy doc types.
    """
    active_map = field_map or DOC_FIELD_MAP
    multi_copy = multi_copy_types or set()
    all_keys = set()
    for _keys in active_map.values():
        all_keys.update(_keys)

    doc_fields: dict = {}

    def _upsert_field(field_key: str, value, doc_type: str, confidence: float,
                      raw_key: str, copy_index: int | None):
        """Insert or update a single field entry, handling multi-copy copies list."""
        has_value = value is not None and str(value).strip() != ""
        is_multi = doc_type in multi_copy

        if field_key not in doc_fields:
            entry: dict = {
                "value": value,
                "source_document": doc_type,
                "confidence": confidence,
                "raw_key": raw_key,
                "all_sources": [doc_type],
            }
            if is_multi:
                entry["copies"] = [{
                    "value": value,
                    "source_document": doc_type,
                    "confidence": confidence,
                    "copy_index": copy_index if copy_index is not None else 0,
                }]
            doc_fields[field_key] = entry
        else:
            existing = doc_fields[field_key]
            existing["all_sources"].append(doc_type)

            if is_multi:
                if "copies" not in existing:
                    existing["copies"] = [{
                        "value": existing["value"],
                        "source_document": existing["source_document"],
                        "confidence": existing["confidence"],
                        "copy_index": 0,
                    }]
                existing["copies"].append({
                    "value": value,
                    "source_document": doc_type,
                    "confidence": confidence,
                    "copy_index": copy_index if copy_index is not None else len(existing["copies"]),
                })
                if has_value:
                    primary_empty = existing.get("value") is None or str(existing.get("value", "")).strip() == ""
                    if primary_empty:
                        existing["value"] = value
                        existing["source_document"] = doc_type
                        existing["confidence"] = confidence
                        existing["raw_key"] = raw_key
            else:
                existing_val = existing.get("value")
                existing_empty = existing_val is None or str(existing_val).strip() == ""
                if existing_empty and has_value:
                    existing["value"] = value
                    existing["source_document"] = doc_type
                    existing["confidence"] = confidence
                    existing["raw_key"] = raw_key
                elif has_value and confidence > existing.get("confidence", 0):
                    existing["value"] = value
                    existing["source_document"] = doc_type
                    existing["confidence"] = confidence
                    existing["raw_key"] = raw_key

    for doc in documents:
        doc_type = doc.get("DocType") or doc.get("doc_type", "")
        doc_status = (doc.get("Status") or doc.get("status", "")).lower()

        if doc_status not in ("completed", "stored_no_extraction", "success"):
            continue

        extracted = doc.get("ExtractedFields") or doc.get("extracted_fields", {})
        if not extracted:
            continue

        copy_index = doc.get("_copy_index")
        expected_keys = set(active_map.get(doc_type, []))

        normalized_extracted = {}
        for raw_key, raw_val in extracted.items():
            norm_key = raw_key.strip().lower().replace(" ", "_").replace("-", "_")
            normalized_extracted[norm_key] = (raw_key, raw_val)

        for expected_key in expected_keys:
            if expected_key in normalized_extracted:
                raw_key, raw_val = normalized_extracted[expected_key]
                value = raw_val if not isinstance(raw_val, dict) else raw_val.get("value", raw_val)
                confidence = raw_val.get("confidence", 1.0) if isinstance(raw_val, dict) else 1.0
                _upsert_field(expected_key, value, doc_type, confidence, raw_key, copy_index)

        for norm_key, (raw_key, raw_val) in normalized_extracted.items():
            if norm_key in all_keys:
                value = raw_val if not isinstance(raw_val, dict) else raw_val.get("value", raw_val)
                confidence = raw_val.get("confidence", 1.0) if isinstance(raw_val, dict) else 1.0
                _upsert_field(norm_key, value, doc_type, confidence, raw_key, copy_index)

    return doc_fields


@tool
def find_loan(
    tool_call_id: Annotated[str, InjectedToolCallId],
    state: Annotated[dict, InjectedState],
    loan_number: Optional[str] = None,
    borrower_name: Optional[str] = None,
) -> Command:
    """Find the loan GUID using loan number or borrower name.

    Uses the Encompass API to search for the loan and return its GUID.
    The GUID is stored in state['loan_id'] for all subsequent tools.

    Args:
        loan_number: The loan number to search for (preferred).
        borrower_name: Borrower name to search for (fallback).
    """
    ln = loan_number or state.get("loan_number")
    bn = borrower_name or state.get("borrower_name")

    # If loan_id already in state, just confirm it
    existing_loan_id = state.get("loan_id")
    if existing_loan_id:
        logger.info(f"[FIND_LOAN] Loan ID already in state: {existing_loan_id[:8]}...")
        result = {
            "success": True,
            "loan_id": existing_loan_id,
            "loan_number": ln,
            "message": f"Loan ID already available: {existing_loan_id[:8]}...",
            "source": "state",
        }
        return Command(update={
            "loan_number": ln,
            "messages": [ToolMessage(content=json.dumps(result), tool_call_id=tool_call_id)],
        })

    if not ln and not bn:
        return Command(update={"messages": [ToolMessage(
            content=json.dumps({"error": "No loan_number or borrower_name provided"}),
            tool_call_id=tool_call_id,
        )]})

    logger.info(f"[FIND_LOAN] Searching for loan: {ln or bn}")

    try:
        # Import the encompass client from the project's client module
        try:
            from encompass_client import get_encompass_client
        except ImportError:
            from shared.field_utils import resolve_loan_id
            # Fallback: try to extract loan_id from additional_info
            additional = state.get("additional_info", {})
            if isinstance(additional, dict) and "loan_id" in additional:
                loan_id = additional["loan_id"]
                result = {
                    "success": True,
                    "loan_id": loan_id,
                    "loan_number": ln,
                    "message": f"Found loan GUID from additional_info: {loan_id[:8]}...",
                    "source": "additional_info",
                }
                return Command(update={
                    "loan_id": loan_id,
                    "loan_number": ln,
                    "messages": [ToolMessage(content=json.dumps(result), tool_call_id=tool_call_id)],
                })
            return Command(update={"messages": [ToolMessage(
                content=json.dumps({"error": "Encompass client not available. Provide loan_id in additional_info."}),
                tool_call_id=tool_call_id,
            )]})

        client = get_encompass_client(state=state)
        if ln:
            results = client.search_loans_pipeline(loan_number=ln)
        else:
            results = client.search_loans_pipeline(borrower_name=bn)

        if not results:
            return Command(update={"messages": [ToolMessage(
                content=json.dumps({"error": f"No loan found for {ln or bn}"}),
                tool_call_id=tool_call_id,
            )]})

        loan_id = results[0] if isinstance(results[0], str) else results[0].get("loanGuid", results[0].get("id"))

        result = {
            "success": True,
            "loan_id": loan_id,
            "loan_number": ln,
            "message": f"Found loan GUID: {loan_id[:8]}...",
            "source": "encompass_search",
        }

        logger.info(f"[FIND_LOAN] Found: {loan_id[:8]}...")

        return Command(update={
            "loan_id": loan_id,
            "loan_number": ln,
            "messages": [ToolMessage(content=json.dumps(result), tool_call_id=tool_call_id)],
        })

    except Exception as e:
        logger.error(f"[FIND_LOAN] Error: {e}")
        return Command(update={"messages": [ToolMessage(
            content=json.dumps({"error": str(e)}),
            tool_call_id=tool_call_id,
        )]})


@tool
def fetch_los_fields(
    tool_call_id: Annotated[str, InjectedToolCallId],
    state: Annotated[dict, InjectedState],
) -> Command:
    """Batch-read all __LOS_COUNT__ LOS fields from Encompass.

    Reads all field IDs in a single batch API call and organizes them into
    state['los_fields'] keyed by the internal field key.

    Each entry: {key: {value, field_id, field_name, category}}
    """
    from shared.encompass_io import read_fields

    loan_id = state.get("loan_id")
    if not loan_id:
        return Command(update={"messages": [ToolMessage(
            content=json.dumps({"error": "No loan_id in state. Run find_loan first."}),
            tool_call_id=tool_call_id,
        )]})

    logger.info(f"[FETCH_LOS] Reading {len(ALL_FIELD_IDS)} fields for loan {loan_id[:8]}...")

    try:
        raw_results = read_fields(loan_id, ALL_FIELD_IDS, context="step0_los", state=state)

        los_fields = {}
        found_count = 0
        missing_count = 0

        for field_id, value in raw_results.items():
            mapping = FIELD_MAP.get(field_id)
            if not mapping:
                continue

            key = mapping["key"]
            stripped = str(value).strip() if value is not None else ""
            has_value = value is not None and stripped != "" and stripped != "//"

            los_fields[key] = {
                "value": value if has_value else None,
                "field_id": field_id,
                "field_name": mapping["field_name"],
                "category": mapping["category"],
            }

            if has_value:
                found_count += 1
            else:
                missing_count += 1

        result = {
            "success": True,
            "fields_found": found_count,
            "fields_missing": missing_count,
            "total_fields": len(ALL_FIELD_IDS),
            "coverage_percent": round(found_count / max(len(ALL_FIELD_IDS), 1) * 100, 1),
            "message": f"Fetched {found_count}/{len(ALL_FIELD_IDS)} LOS fields ({missing_count} missing)",
        }

        logger.info(f"[FETCH_LOS] {result['message']}")

        return Command(update={
            "los_fields": los_fields,
            "messages": [ToolMessage(content=json.dumps(result), tool_call_id=tool_call_id)],
        })

    except Exception as e:
        logger.error(f"[FETCH_LOS] Error: {e}")
        return Command(update={"messages": [ToolMessage(
            content=json.dumps({"error": str(e)}),
            tool_call_id=tool_call_id,
        )]})


@tool
def fetch_doc_fields(
    tool_call_id: Annotated[str, InjectedToolCallId],
    state: Annotated[dict, InjectedState],
) -> Command:
    """Fetch required document fields from eFolder DynamoDB cache via GET /efolder.

    1. Derives which doc types are REQUIRED based on loan characteristics
       (loan_type, loan_purpose, borrower_count) from required_docs_conditions.json.
    2. Calls GET /efolder?loanNumber=X&includeFields=true — reads DynamoDB cache.
    3. Filters response to ONLY required doc types (ignores irrelevant docs like
       FHA/VA forms on a Conventional loan).
    4. Stores required documents in state['efolder_documents'].
    5. Normalizes expected doc fields into state['doc_fields'].

    Flow: derive required docs -> GET /efolder -> filter to required -> normalize -> state
    """
    loan_number = state.get("loan_number")
    if not loan_number:
        return Command(update={"messages": [ToolMessage(
            content=json.dumps({"error": "No loan_number in state."}),
            tool_call_id=tool_call_id,
        )]})

    env = state.get("env", "Test").lower()

    # ── Derive loan characteristics for dynamic doc type selection ──
    loan_type, loan_purpose, borrower_count = _derive_loan_characteristics(state)
    required_doc_types, extraction_modes = get_required_documents_for_loan(
        loan_type, loan_purpose, borrower_count,
    )

    logger.info(
        f"[FETCH_DOCS] Loan: type={loan_type or '?'}, purpose={loan_purpose or '?'}, "
        f"borrowers={borrower_count} -> {len(required_doc_types)} required doc types"
    )
    logger.info(f"[FETCH_DOCS] Fetching docs for loan {loan_number} ({env}) via GET /efolder...")

    try:
        from shared.efolder_client import EfolderClient

        client = EfolderClient()

        # Single GET call — reads DynamoDB, returns ExtractedFields + DocRepoLocation
        get_resp = client.get_documents(loan_number, include_fields=True)

        if "error" in get_resp:
            error_msg = get_resp["error"]
            logger.error(f"[FETCH_DOCS] efolderGet error: {error_msg}")
            return Command(update={"messages": [ToolMessage(
                content=json.dumps({"error": error_msg}),
                tool_call_id=tool_call_id,
            )]})

        all_documents = get_resp.get("documents", [])
        logger.info(f"[FETCH_DOCS] efolderGet returned {len(all_documents)} total documents from DynamoDB")

        # Group ALL documents by DocType (keep every copy)
        all_docs_by_type: dict[str, list[dict]] = {}
        for doc in all_documents:
            dt = doc.get("DocType", "")
            if not dt:
                continue
            all_docs_by_type.setdefault(dt, []).append(doc)

        # Filter to ONLY required doc types — ignore irrelevant docs
        required_set = set(required_doc_types)
        docs_by_type: dict[str, list[dict]] = {
            dt: docs for dt, docs in all_docs_by_type.items() if dt in required_set
        }
        ignored_count = len(all_docs_by_type) - len(docs_by_type)

        # For "all" extraction mode keep every copy; otherwise keep best single doc
        multi_copy_types = {dt for dt, mode in extraction_modes.items() if mode == "all"}
        flat_docs_for_normalize: list[dict] = []
        for dt, doc_list in docs_by_type.items():
            if dt in multi_copy_types:
                for idx, d in enumerate(doc_list):
                    d["_copy_index"] = idx
                    flat_docs_for_normalize.append(d)
            else:
                best = doc_list[0]
                for d in doc_list[1:]:
                    s = (d.get("Status", "") or "").lower()
                    if s == "completed" and (best.get("Status", "") or "").lower() != "completed":
                        best = d
                flat_docs_for_normalize.append(best)

        total_docs_kept = len(flat_docs_for_normalize)
        logger.info(
            f"[FETCH_DOCS] DynamoDB: {len(all_docs_by_type)} unique types, "
            f"{len(docs_by_type)} required ({total_docs_kept} docs incl. copies), "
            f"{ignored_count} ignored"
        )

        # ── Normalize to doc_fields — only from required docs ──
        doc_fields = _normalize_efolder_output(
            flat_docs_for_normalize, multi_copy_types=multi_copy_types,
        )

        # ── Build document inventory — required docs only ──
        efolder_documents = {}
        completed_count = 0
        not_found_types = []
        pending_types = []
        failed_types = []

        for dt, doc_list in docs_by_type.items():
            is_multi = dt in multi_copy_types
            copies_info: list[dict] = []
            dt_completed = 0

            for idx, doc in enumerate(doc_list):
                status = (doc.get("Status", "") or "").lower()
                extracted = doc.get("ExtractedFields", {})

                fields_summary = {}
                for field_name, field_val in extracted.items():
                    if isinstance(field_val, dict):
                        fields_summary[field_name] = {
                            "value": field_val.get("value", field_val),
                            "confidence": field_val.get("confidence", 1.0),
                        }
                    else:
                        fields_summary[field_name] = {
                            "value": field_val,
                            "confidence": 1.0,
                        }

                copy_entry = {
                    "copy_index": idx,
                    "status": status,
                    "source": doc.get("Source", ""),
                    "document_title": doc.get("DocumentTitle", ""),
                    "attachment_id": doc.get("AttachmentID", ""),
                    "attachment_name": doc.get("AttachmentName", ""),
                    "file_size": doc.get("FileSizeBytes", 0),
                    "extracted_fields_count": doc.get("ExtractedFieldsCount", len(extracted)),
                    "extracted_fields": fields_summary,
                    "docrepo_location": doc.get("DocRepoLocation", ""),
                    "docrepo_bucket": doc.get("Bucket", ""),
                    "docrepo_client_id": doc.get("Client", ""),
                    "error": doc.get("FailureReason"),
                }
                copies_info.append(copy_entry)

                if status in ("completed", "stored_no_extraction"):
                    dt_completed += 1
                elif status == "pending" and dt not in pending_types:
                    pending_types.append(dt)
                elif status.startswith("error") and dt not in failed_types:
                    failed_types.append(dt)

                logger.info(
                    f"[FETCH_DOCS]   {dt}[{idx}]: status={status}, "
                    f"fields={len(extracted)}, "
                    f"docrepo={doc.get('DocRepoLocation', '') or 'N/A'}"
                )

            if dt_completed > 0:
                completed_count += 1

            primary = copies_info[0] if copies_info else {}
            efolder_documents[dt] = {
                "doc_type": dt,
                "copy_count": len(doc_list),
                "is_multi_copy": is_multi,
                "status": primary.get("status", "unknown") if len(copies_info) == 1 else "multiple",
                "source": primary.get("source", ""),
                "document_title": primary.get("document_title", ""),
                "attachment_id": primary.get("attachment_id", ""),
                "attachment_name": primary.get("attachment_name", ""),
                "file_size": primary.get("file_size", 0),
                "extracted_fields_count": primary.get("extracted_fields_count", 0),
                "extracted_fields": primary.get("extracted_fields", {}),
                "docrepo_location": primary.get("docrepo_location", ""),
                "docrepo_bucket": primary.get("docrepo_bucket", ""),
                "docrepo_client_id": primary.get("docrepo_client_id", ""),
                "extraction_mode": extraction_modes.get(dt, "best"),
                "error": primary.get("error"),
                "copies": copies_info,
            }

        # Mark required docs that are missing from DynamoDB
        for dt in required_doc_types:
            if dt not in docs_by_type:
                not_found_types.append(dt)
                efolder_documents[dt] = {
                    "doc_type": dt,
                    "copy_count": 0,
                    "is_multi_copy": dt in multi_copy_types,
                    "status": "not_found",
                    "source": "",
                    "document_title": "",
                    "attachment_id": "",
                    "attachment_name": "",
                    "file_size": 0,
                    "extracted_fields_count": 0,
                    "extracted_fields": {},
                    "docrepo_location": "",
                    "docrepo_bucket": "",
                    "docrepo_client_id": "",
                    "extraction_mode": extraction_modes.get(dt, "best"),
                    "error": "Not found in DynamoDB cache",
                    "copies": [],
                }
                logger.info(f"[FETCH_DOCS]   {dt}: NOT FOUND in DynamoDB")

        # Overall metadata
        first_doc_list = next(iter(docs_by_type.values()), [])
        first_doc = first_doc_list[0] if first_doc_list else {}
        efolder_documents["_meta"] = {
            "loan_number": loan_number,
            "loan_guid": first_doc.get("LoanGuid", ""),
            "loan_type": loan_type,
            "loan_purpose": loan_purpose,
            "borrower_count": borrower_count,
            "multi_copy_doc_types": sorted(list(multi_copy_types)),
            "total_in_dynamodb": len(all_docs_by_type),
            "total_required": len(required_doc_types),
            "ignored_non_required": ignored_count,
            "completed": completed_count,
            "not_found": len(not_found_types),
            "pending": len(pending_types),
            "failed": len(failed_types),
            "not_found_doc_types": not_found_types,
            "pending_doc_types": pending_types,
            "failed_doc_types": failed_types,
            "required_doc_types": required_doc_types,
            "source": "efolderGet (GET /efolder DynamoDB)",
            "retrieved_at": datetime.now().isoformat(),
        }

        # Track which expected doc fields are still missing
        expected_total = len(ALL_DOC_FIELD_KEYS)
        found_keys = set(doc_fields.keys())
        missing_keys = ALL_DOC_FIELD_KEYS - found_keys

        result = {
            "success": True,
            "loan_type": loan_type,
            "loan_purpose": loan_purpose,
            "borrower_count": borrower_count,
            "total_in_dynamodb": len(all_docs_by_type),
            "required_doc_types": len(required_doc_types),
            "ignored_non_required": ignored_count,
            "completed": completed_count,
            "not_found": len(not_found_types),
            "pending": len(pending_types),
            "fields_normalized": len(doc_fields),
            "fields_expected": expected_total,
            "fields_missing": sorted(list(missing_keys)),
            "not_found_doc_types": not_found_types,
            "pending_doc_types": pending_types,
            "message": (
                f"{completed_count}/{len(required_doc_types)} required docs found "
                f"({ignored_count} non-required ignored). "
                f"Normalized {len(doc_fields)}/{expected_total} doc fields. "
                f"Loan: {loan_type} {loan_purpose}, {borrower_count} borrower(s)."
            ),
        }

        if not_found_types:
            result["message"] += f" Missing: {', '.join(not_found_types)}."
        if pending_types:
            result["message"] += f" Pending: {', '.join(pending_types)}."

        logger.info(f"[FETCH_DOCS] {result['message']}")

        return Command(update={
            "doc_fields": doc_fields,
            "efolder_documents": efolder_documents,
            "messages": [ToolMessage(content=json.dumps(result), tool_call_id=tool_call_id)],
        })

    except Exception as e:
        logger.error(f"[FETCH_DOCS] Error: {e}")
        return Command(update={"messages": [ToolMessage(
            content=json.dumps({"error": str(e)}),
            tool_call_id=tool_call_id,
        )]})


# ── URLA field key → loan_summary section mapping ──
# Maps los_fields keys to their slot in the loan_summary dict.
# This is auto-generated from the field registry.
URLA_BORROWER_KEYS = [
    "borrower_first_name", "borrower_middle_name", "borrower_last_name",
    "borrower_ssn", "borrower_dob", "borrower_marital_status",
    "borrower_sex", "borrower_aka",
]
URLA_PROPERTY_KEYS = [
    "property_address", "property_city", "property_state",
    "property_zip", "property_county",
]
URLA_LOAN_TERMS_KEYS = [
    "preflight_mortgage_type", "preflight_loan_purpose",
    "preflight_loan_amount", "preflight_appraised_value",
    "preflight_ltv", "preflight_note_rate",
    "occupancy_status",
]
URLA_DATES_KEYS = [
    "closing_date", "preflight_lock_expiration",
]
URLA_VESTING_KEYS = [
    "final_vesting", "manner_held",
]
URLA_PREFLIGHT_KEYS = [
    "preflight_ctc_status", "preflight_cd_status",  # field 2305 (Clear to Close date), CX.CD.APPROVED.DATE
    "preflight_over_under",
]
URLA_CLOSING_KEYS = [
    "closing_conditions_text", "elective_insurance",
]


def _safe_get(los_fields: dict, key: str) -> str | None:
    """Safely extract a value from los_fields."""
    entry = los_fields.get(key)
    if entry and isinstance(entry, dict):
        v = entry.get("value")
        if v is not None and str(v).strip():
            return str(v).strip()
    return None


def _mask_ssn(ssn: str | None) -> str | None:
    """Mask SSN to show only last 4 digits."""
    if not ssn:
        return None
    digits = ssn.replace("-", "").replace(" ", "")
    if len(digits) >= 4:
        return f"***-**-{digits[-4:]}"
    return ssn


@tool
def build_loan_summary(
    tool_call_id: Annotated[str, InjectedToolCallId],
    state: Annotated[dict, InjectedState],
) -> Command:
    """Build the loan summary (URLA equivalent) from los_fields.

    Creates a categorized, human-readable snapshot of the loan stored in
    state['loan_summary']. This runs once in Step 0.4 and NEVER changes
    afterwards — it is the single source of truth for the loan profile.

    Categories: borrower, property, loan_terms, dates, vesting, preflight,
    closing, derived (has_coborrower, is_note_llc), _meta.
    """
    los = state.get("los_fields", {})

    if not los:
        return Command(update={"messages": [ToolMessage(
            content=json.dumps({"error": "los_fields is empty. Run fetch_los_fields first."}),
            tool_call_id=tool_call_id,
        )]})

    logger.info("[LOAN_SUMMARY] Building loan summary (URLA) from los_fields...")

    def _get(key):
        return _safe_get(los, key)

    # ── Borrower ──
    borrower = {}
    for k in URLA_BORROWER_KEYS:
        v = _get(k)
        if k == "borrower_ssn":
            v = _mask_ssn(v)
        if v is not None:
            borrower[k] = v

    # ── Property ──
    prop = {}
    for k in URLA_PROPERTY_KEYS:
        v = _get(k)
        if v is not None:
            prop[k] = v

    # ── Loan Terms ──
    loan_terms = {}
    for k in URLA_LOAN_TERMS_KEYS:
        v = _get(k)
        if v is not None:
            loan_terms[k] = v

    # ── Dates ──
    dates = {}
    for k in URLA_DATES_KEYS:
        v = _get(k)
        if v is not None:
            dates[k] = v

    # ── Vesting ──
    vesting = {}
    for k in URLA_VESTING_KEYS:
        v = _get(k)
        if v is not None:
            vesting[k] = v

    # ── Preflight ──
    preflight = {}
    for k in URLA_PREFLIGHT_KEYS:
        v = _get(k)
        if v is not None:
            preflight[k] = v

    # ── Closing ──
    closing = {}
    for k in URLA_CLOSING_KEYS:
        v = _get(k)
        if v is not None:
            closing[k] = v

    # ── Derived flags ──
    has_coborrower = False
    coborrower_last = _get("preflight_has_coborrower")
    if coborrower_last:
        has_coborrower = True

    # is_note_llc: check LO/processor email, lender name, or CD page 5 lender
    is_note_llc = False
    prop_state = (_get("property_state") or "").upper()
    mortgage_type = (_get("preflight_mortgage_type") or "").lower()
    lo_email = (_get("lo_email") or "").lower()
    processor_email = (_get("processor_email") or "").lower()
    lender_name = (_get("lender_name_alt") or "").lower()
    cd5_lender = (_get("cd5_lender_name") or "").lower()
    _NOTE_LLC_NAMES = ("note mortgage", "note llc", "note mortgage llc")
    if (
        "@notemortgage.com" in lo_email
        or "@notemortgage.com" in processor_email
        or any(n in lender_name for n in _NOTE_LLC_NAMES)
        or any(n in cd5_lender for n in _NOTE_LLC_NAMES)
    ):
        is_note_llc = True

    # Trust flag
    is_trust = False
    trust_flag = (_get("close_trust_flag") or "").strip().lower()
    if trust_flag in ("true", "yes", "1", "y"):
        is_trust = True

    ltv_str = _get("preflight_ltv")
    ltv = None
    if ltv_str:
        try:
            ltv = float(ltv_str)
        except (ValueError, TypeError):
            ltv = None

    derived = {
        "has_coborrower": has_coborrower,
        "is_note_llc": is_note_llc,
        "is_trust": is_trust,
        "loan_type": _get("preflight_mortgage_type"),
        "loan_purpose": _get("preflight_loan_purpose"),
        "ltv": ltv,
    }

    # ── Loan Profile (5 discriminators for rule modifiers) ──
    loan_profile = {
        "loan_type": _get("preflight_mortgage_type") or "Conventional",
        "purpose": _get("preflight_loan_purpose") or "Purchase",
        "state": prop_state,
        "trust": is_trust,
        "note_llc": is_note_llc,
    }

    # ── Coverage stats ──
    all_urla_keys = (
        URLA_BORROWER_KEYS + URLA_PROPERTY_KEYS + URLA_LOAN_TERMS_KEYS
        + URLA_DATES_KEYS + URLA_VESTING_KEYS + URLA_PREFLIGHT_KEYS
        + URLA_CLOSING_KEYS
    )
    available = sum(1 for k in all_urla_keys if _get(k) is not None)
    missing_keys = [k for k in all_urla_keys if _get(k) is None]

    loan_summary = {
        "borrower": borrower,
        "property": prop,
        "loan_terms": loan_terms,
        "dates": dates,
        "vesting": vesting,
        "preflight": preflight,
        "closing": closing,
        "derived": derived,
        "_meta": {
            "total_urla_fields": len(all_urla_keys),
            "fields_available": available,
            "fields_missing": missing_keys,
            "coverage_percent": round(available / max(len(all_urla_keys), 1) * 100, 1),
            "built_at": datetime.now().isoformat(),
            "source": "los_fields",
            "immutable": True,
        },
    }

    result = {
        "success": True,
        "coverage_percent": loan_summary["_meta"]["coverage_percent"],
        "fields_available": available,
        "fields_missing_count": len(missing_keys),
        "has_coborrower": has_coborrower,
        "loan_type": derived["loan_type"],
        "loan_purpose": derived["loan_purpose"],
        "ltv": ltv,
        "loan_profile": loan_profile,
        "message": (
            f"Loan summary (URLA) built: {available}/{len(all_urla_keys)} fields "
            f"({loan_summary['_meta']['coverage_percent']}%). "
            f"Loan: {derived['loan_type']} {derived['loan_purpose']}, "
            f"LTV: {ltv}, CoBorrower: {has_coborrower}. "
            f"Profile: type={loan_profile['loan_type']}, "
            f"purpose={loan_profile['purpose']}, state={loan_profile['state']}, "
            f"trust={loan_profile['trust']}, note_llc={loan_profile['note_llc']}"
        ),
    }

    logger.info(f"[LOAN_SUMMARY] {result['message']}")

    return Command(update={
        "loan_summary": loan_summary,
        "loan_profile": loan_profile,
        "messages": [ToolMessage(content=json.dumps(result), tool_call_id=tool_call_id)],
    })
'''

    code = part_header + part_field_map + part_body.replace("__LOS_COUNT__", str(los_count))
    return code


def generate_step0_plan(registry: FieldRegistry, next_step_id: str = "STEP_01", next_step_name: str = "Verification") -> str:
    """Generate the plan markdown for Step 0."""
    all_los = registry.get_all_field_ids()
    all_doc_types = registry.get_all_doc_types()

    # Build field table
    field_rows = []
    for fid in sorted(registry.los_fields.keys()):
        info = registry.los_fields[fid]
        field_rows.append(f"| `{fid}` | {info.key} | {info.field_name} | {info.category} |")
    field_table = "\n".join(field_rows)

    doc_list = "\n".join(f"- {dt}" for dt in all_doc_types)

    plan = f"""# Step 0 - [VERIFICATION] Data Gathering

**Phase**: VERIFICATION
**Auto-generated**: Yes — this step is derived from fields used by all other steps.
**Tools**: `find_loan`, `fetch_los_fields`, `fetch_doc_fields`, `build_loan_summary`

## Purpose

Gather all data needed by the workflow in one upfront step:
- Find the loan GUID from the loan number
- Batch-read {len(all_los)} LOS fields from Encompass
- Extract fields from {len(all_doc_types)} document types
- Build the Loan Summary (URLA) — a categorized snapshot that never changes

**NOTE**: Each substep has its own dedicated tool. State is automatically injected.

## Available Tools

| Tool | Purpose |
|------|---------|
| `find_loan` | Search Encompass for loan GUID |
| `fetch_los_fields` | Fetch all needed LOS fields in one batch call |
| `fetch_doc_fields` | Extract fields from specific document types |
| `build_loan_summary` | Build categorized URLA-style loan summary from los_fields |

## Tool Calls

```python
# Substep 0.1 - Find Loan
find_loan(loan_number=loan_number, borrower_name=borrower_name)

# Substep 0.2 - Fetch LOS Fields
fetch_los_fields(loan_guid=loan_id)

# Substep 0.3 - Fetch Doc Fields
fetch_doc_fields(loan_guid=loan_id)

# Substep 0.4 - Build Loan Summary (URLA)
build_loan_summary()
```

---

## Substeps

### Substep 0.1 - Find Loan
**Tools**: `find_loan`

Call `find_loan()` — it automatically reads `loan_number` and `borrower_name` from state.
You do NOT need to pass any arguments. This stores the loan GUID in state for all subsequent tools.

### Substep 0.2 - Fetch LOS Fields ({len(all_los)} fields)
**Tools**: `fetch_los_fields`

Call `fetch_los_fields` to batch-read all Encompass field IDs.
Results are stored in `state["los_fields"]` organized by key.

### Substep 0.3 - Fetch Doc Fields ({len(all_doc_types)} document types)
**Tools**: `fetch_doc_fields`

Call `fetch_doc_fields` to extract fields from documents in the eFolder.
Results are stored in `state["doc_fields"]` organized by key.

### Substep 0.4 - Build Loan Summary (URLA) + Loan Profile Detection
**Tools**: `build_loan_summary`

Call `build_loan_summary()` AFTER fetch_los_fields completes.
This builds a categorized snapshot from `state["los_fields"]` into `state["loan_summary"]`
and detects the **loan profile** stored in `state["loan_profile"]`.

The loan summary includes:
- **borrower**: name, SSN (masked), DOB, marital status
- **property**: address, city, state, zip, county
- **loan_terms**: type, purpose, amount, rate, LTV, appraised value
- **dates**: closing, lock expiration, appraisal received
- **vesting**: manner held, final vesting, occupancy
- **preflight**: CTC status, CD status, overage
- **closing**: conditions text, elective insurance
- **derived**: has_coborrower, is_note_llc, is_trust, loan_type, loan_purpose, LTV

**Loan Profile** (5 discriminators for rule modifiers):
- **loan_type**: Conventional, FHA, VA, USDA (from field 1172)
- **purpose**: Purchase, Refinance, CashOutRefi (from field 19)
- **state**: 2-letter property state code (from field 14)
- **trust**: boolean trust involvement (from CX.CLOSE.TRUST)
- **note_llc**: boolean Note Mortgage LLC origination (from LO/processor email)

The loan profile drives `rule_modifiers` on all subsequent substeps.

⚠️ Both loan_summary and loan_profile are IMMUTABLE — set once, never updated.

---

## LOS Fields Reference

| Field ID | Key | Name | Category |
|----------|-----|------|----------|
{field_table}

## Document Types

{doc_list}

---

## Step Completion

When ALL 4 substeps above are completed (0.1 through 0.4):
1. Call `save_step_report(step_name="STEP_00", status="completed", ...)`
2. Call `write_todo(step_id="STEP_00", status="completed")` to advance to the next step
3. Call `write_todo(step_id="{next_step_id}", status="in_progress")` to start {next_step_id} ({next_step_name})

⚠️ You MUST call write_todo to advance — do NOT produce a text-only response.
"""
    return plan
