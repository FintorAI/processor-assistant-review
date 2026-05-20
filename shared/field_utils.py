"""Field Mapping Utilities for DocsOrch.

This module provides helper functions for safely extracting values from field_mappings
dictionaries, which can contain either simple values or structured dicts with metadata.

Also provides utilities for:
- Extracting values from loan entity files using JSON paths
- Reading field values directly from Encompass API using EncompassConnect
"""

import json
import glob
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger(__name__)


# =============================================================================
# LOAN ID RESOLUTION - ALWAYS PREFER STATE OVER LLM PARAMETER
# =============================================================================

def resolve_loan_id(
    state: Dict[str, Any],
    param_loan_id: Optional[str] = None,
    step_label: str = "",
) -> Optional[str]:
    """Resolve loan_id from state, ignoring LLM-provided parameter.

    The LLM can hallucinate wrong GUIDs. This function ALWAYS prefers the
    loan_id stored in state (set by find_loan in Step 0) over any value the
    LLM passes as a tool parameter.

    GUID validation:
        Both ``state["loan_id"]`` and ``param_loan_id`` are checked against
        the canonical GUID regex (see shared.encompass_io.is_guid). A loan
        NUMBER masquerading as a GUID (e.g. "2605926537", which the
        orchestrator may have passed through unresolved) is treated as
        ``not present`` rather than blindly returned. This is the orchestrator
        boundary that previously let "2605926537" propagate as a GUID and
        produce 404s against ``/v3/loans/<loan_number>``.

    Args:
        state: The workflow state dict (from InjectedState)
        param_loan_id: The loan_id parameter the LLM passed (used only as
                       last-resort fallback, with a warning)
        step_label: Optional label for log messages (e.g., "STEP5.3")

    Returns:
        A real Encompass loan GUID, or None if none is available.
    """
    # Local import to avoid hard module-load coupling.
    try:
        from shared.encompass_io import is_guid, sanitize_guid  # type: ignore
    except Exception:  # pragma: no cover — defensive
        is_guid = lambda v: bool(v)  # noqa: E731
        sanitize_guid = lambda v: str(v).strip() if v else ""  # noqa: E731

    prefix = f"[{step_label}] " if step_label else ""

    state_loan_id = state.get("loan_id") if state else None

    if state_loan_id and is_guid(str(state_loan_id)):
        if param_loan_id and is_guid(str(param_loan_id)) and str(param_loan_id) != str(state_loan_id):
            logger.warning(
                f"{prefix}LOAN_ID MISMATCH: LLM passed '{str(param_loan_id)[:8]}...' "
                f"but state has '{str(state_loan_id)[:8]}...'. Using state value."
            )
        return sanitize_guid(str(state_loan_id))

    if state_loan_id and not is_guid(str(state_loan_id)):
        # State has a value but it is not a GUID — likely a raw loan_number
        # the orchestrator passed through before find_loan resolved it.
        # Do NOT promote it; let the caller surface a "run find_loan first"
        # error instead of letting a non-GUID hit the Encompass API.
        logger.warning(
            f"{prefix}state.loan_id={state_loan_id!r} is not a GUID — ignoring. "
            f"find_loan must run first to populate a real GUID."
        )

    if param_loan_id and is_guid(str(param_loan_id)):
        logger.warning(
            f"{prefix}loan_id not found in state, falling back to LLM parameter: "
            f"'{str(param_loan_id)[:8]}...'. This may be incorrect."
        )
        return sanitize_guid(str(param_loan_id))

    logger.error(f"{prefix}No valid loan GUID found in state or parameters. Run find_loan first.")
    return None


# =============================================================================
# LOAN FIELD SUMMARY ACCESS HELPERS
# =============================================================================

def lfs_value(loan_field_summary: Dict[str, Any], category: str, field_key: str, default=None):
    """Get a field value from loan_field_summary by category and key.
    
    loan_field_summary fields are stored as: {category: {field_key: {"value": ..., "source": ...}}}
    This is the canonical way to read fields populated by Step 0 (extract_loan_fields).
    
    Args:
        loan_field_summary: The loan_field_summary dict from state
        category: Category name (e.g., "property", "preflight", "closing_conditions")
        field_key: Field key within category (e.g., "property_type", "preflight_mortgage_type")
        default: Default value if not found
        
    Returns:
        The field value, or default if not found
        
    Example:
        >>> lfs = state.get("loan_field_summary", {})
        >>> loan_type = lfs_value(lfs, "closing_conditions", "closing_cond_loan_type")
        >>> property_state = lfs_value(lfs, "property", "property_state")
    """
    field = loan_field_summary.get(category, {}).get(field_key, {})
    if isinstance(field, dict):
        return field.get("value", default)
    return default


def get_loan_entity_field(
    state: Dict[str, Any],
    field_key: str,
    required: bool = False,
    default: Any = None,
    tool_name: str = None,
    substep: str = None
) -> tuple:
    """Safely get a field from loan_field_summary in state with automatic flag raising.
    
    Searches across all categories in loan_field_summary for the given field_key.
    
    Args:
        state: The agent state dictionary
        field_key: The field key to look up (e.g., "property_type", "closing_cond_loan_type")
        required: If True, creates a flag when field is missing
        default: Default value if field not found
        tool_name: Name of the calling tool (for flag details)
        substep: Current substep (for flag details)
        
    Returns:
        Tuple of (value, flag_or_none):
        - value: The field value or default if not found
        - flag: A flag dict if field was required but missing, None otherwise
    """
    lfs = state.get("loan_field_summary", {})
    
    if not lfs:
        if required:
            flag = {
                "step": substep.split(".")[0] if substep and "." in substep else "0",
                "substep": substep or "unknown",
                "field_name": field_key,
                "title": f"Missing loan_field_summary in state",
                "details": (
                    f"Tool '{tool_name or 'unknown'}' requires loan_field_summary but it's not in state. "
                    f"Required field: {field_key}. Run extract_loan_fields (Step 0) first."
                ),
                "suggestion": "Verify extract_loan_fields ran successfully",
                "severity": "error",
                "category": "missing_field",
            }
            logger.warning(f"[FIELD_ACCESS] loan_field_summary missing from state, required field: {field_key}")
            return default, flag
        return default, None
    
    # Search across all categories for the field_key
    value = None
    for category_name, category_data in lfs.items():
        if category_name.startswith("_"):
            continue
        if isinstance(category_data, dict) and field_key in category_data:
            field_data = category_data[field_key]
            if isinstance(field_data, dict):
                value = field_data.get("value")
            else:
                value = field_data
            break
    
    if value is None and required:
        flag = {
            "step": substep.split(".")[0] if substep and "." in substep else "0",
            "substep": substep or "unknown",
            "field_name": field_key,
            "title": f"Missing required field: {field_key}",
            "details": (
                f"Tool '{tool_name or 'unknown'}' requires field '{field_key}' but it was not found "
                f"in loan_field_summary. This field may need to be added to field_extraction_config.json."
            ),
            "suggestion": f"Add '{field_key}' to field_extraction_config.json",
            "severity": "warning",
            "category": "missing_field",
        }
        logger.warning(f"[FIELD_ACCESS] Required field '{field_key}' not in loan_field_summary for tool '{tool_name}'")
        return default, flag
    
    return value if value is not None else default, None


def get_loan_entity_fields(
    state: Dict[str, Any],
    field_keys: List[str],
    required_fields: List[str] = None,
    tool_name: str = None,
    substep: str = None
) -> tuple:
    """Get multiple fields from loan_field_summary with automatic flag raising for required ones.
    
    Args:
        state: The agent state dictionary
        field_keys: List of field keys to retrieve
        required_fields: List of field keys that are required (subset of field_keys)
        tool_name: Name of the calling tool (for flag details)
        substep: Current substep (for flag details)
        
    Returns:
        Tuple of (values_dict, flags_list):
        - values_dict: Dictionary mapping field_key to value (None if missing)
        - flags_list: List of flags for missing required fields
    """
    if required_fields is None:
        required_fields = []
    
    values = {}
    flags = []
    
    for field_key in field_keys:
        is_required = field_key in required_fields
        value, flag = get_loan_entity_field(
            state, field_key,
            required=is_required,
            tool_name=tool_name,
            substep=substep
        )
        values[field_key] = value
        if flag:
            flags.append(flag)
    
    return values, flags


def get_field_value(field_mappings: Dict[str, Any], field_id: str, default: Any = None) -> Any:
    """Safely extract value from field_mappings.
    
    field_mappings can contain either:
    - Simple values: {"1405": "1234.56"}
    - Dict with metadata: {"1405": {"value": "1234.56", "primary_document": "...", ...}}
    
    Args:
        field_mappings: The field mappings dictionary
        field_id: The Encompass field ID to look up
        default: Default value if field not found
        
    Returns:
        The extracted value (simple or from 'value' key in dict)
    """
    if not field_mappings:
        return default
        
    value = field_mappings.get(field_id, default)
    
    if isinstance(value, dict):
        # New format: {"value": ..., "primary_document": ..., ...}
        return value.get("value", default)
    
    return value


def get_field_float(field_mappings: Dict[str, Any], field_id: str, default: float = 0.0) -> float:
    """Safely extract a float value from field_mappings.
    
    Args:
        field_mappings: The field mappings dictionary
        field_id: The Encompass field ID to look up
        default: Default value if field not found or not convertible
        
    Returns:
        The field value as a float
    """
    value = get_field_value(field_mappings, field_id, default)
    
    if value is None or value == "":
        return default
        
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


def get_field_string(field_mappings: Dict[str, Any], field_id: str, default: str = "") -> str:
    """Safely extract a string value from field_mappings.
    
    Args:
        field_mappings: The field mappings dictionary
        field_id: The Encompass field ID to look up
        default: Default value if field not found
        
    Returns:
        The field value as a string
    """
    value = get_field_value(field_mappings, field_id, default)
    
    if value is None:
        return default
        
    return str(value)


def get_field_int(field_mappings: Dict[str, Any], field_id: str, default: int = 0) -> int:
    """Safely extract an integer value from field_mappings.
    
    Args:
        field_mappings: The field mappings dictionary
        field_id: The Encompass field ID to look up
        default: Default value if field not found or not convertible
        
    Returns:
        The field value as an integer
    """
    value = get_field_value(field_mappings, field_id, default)
    
    if value is None or value == "":
        return default
        
    try:
        return int(float(value))  # Handle "123.0" strings
    except (ValueError, TypeError):
        return default


def get_field_bool(field_mappings: Dict[str, Any], field_id: str, default: bool = False) -> bool:
    """Safely extract a boolean value from field_mappings.
    
    Handles various representations: "Y"/"N", "Yes"/"No", "true"/"false", 1/0
    
    Args:
        field_mappings: The field mappings dictionary
        field_id: The Encompass field ID to look up
        default: Default value if field not found
        
    Returns:
        The field value as a boolean
    """
    value = get_field_value(field_mappings, field_id, None)
    
    if value is None:
        return default
    
    if isinstance(value, bool):
        return value
        
    if isinstance(value, (int, float)):
        return bool(value)
        
    if isinstance(value, str):
        value_lower = value.lower().strip()
        if value_lower in ("y", "yes", "true", "1"):
            return True
        if value_lower in ("n", "no", "false", "0", ""):
            return False
            
    return default


def get_field_metadata(field_mappings: Dict[str, Any], field_id: str) -> Optional[Dict[str, Any]]:
    """Get the full metadata dict for a field if available.
    
    Args:
        field_mappings: The field mappings dictionary
        field_id: The Encompass field ID to look up
        
    Returns:
        The full metadata dict if present, None otherwise
    """
    if not field_mappings:
        return None
        
    value = field_mappings.get(field_id)
    
    if isinstance(value, dict):
        return value
    
    return None


# =============================================================================
# LOAN ENTITY FILE UTILITIES
# =============================================================================

def get_latest_loan_entity_file(loan_id: str = None) -> Optional[Path]:
    """Get the most recent loan entity file from tmp folder.
    
    Args:
        loan_id: Optional loan GUID. If provided, only returns files matching this loan.
                 If None, returns the most recent loan entity file.
        
    Returns:
        Path to the most recent loan entity JSON file, or None if not found
        
    Example:
        >>> get_latest_loan_entity_file("ae9dd6e2")
        Path("tmp/loan_entity_ae9dd6e2_1734779400.json")
        
        >>> get_latest_loan_entity_file()  # Get any recent file
        Path("tmp/loan_entity_12345678_1734779400.json")
    """
    # Check local tmp directory
    agent_tmp = Path(__file__).parent.parent.parent / "tmp"
    system_tmp = Path("/tmp")
    
    # Try both locations
    for tmp_dir in [agent_tmp, system_tmp]:
        if not tmp_dir.exists():
            continue
        
        # Build search pattern
        if loan_id:
            # Match specific loan (first 8 chars of GUID)
            pattern = f"loan_entity_{loan_id[:8]}_*.json"
        else:
            pattern = "loan_entity_*.json"
        
        # Find matching files
        files = list(tmp_dir.glob(pattern))
        
        if files:
            # Return most recent file (by modification time)
            return max(files, key=lambda p: p.stat().st_mtime)
    
    return None


def load_loan_entity_from_tmp(loan_id: str = None) -> Optional[Dict[str, Any]]:
    """Load loan entity from tmp folder.
    
    Args:
        loan_id: Optional loan GUID. If provided, loads file for this loan.
                 If None, loads the most recent loan entity file.
        
    Returns:
        Loan entity dict, or None if file not found
        
    Example:
        >>> entity = load_loan_entity_from_tmp("ae9dd6e2")
        >>> print(entity["loanNumber"])
        "2512926224"
    """
    file_path = get_latest_loan_entity_file(loan_id)
    
    if not file_path:
        return None
    
    try:
        with open(file_path, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        return None


def extract_value_from_entity_path(entity: Dict[str, Any], path: str) -> Any:
    """Extract value from loan entity using JSON path notation.
    
    Supports:
    - Simple paths: "loanNumber"
    - Nested paths: "property.city"
    - Array indexing: "applications[0].borrower.firstName"
    - Array filtering: "contacts[?contactType=SELLER].name"
    
    Args:
        entity: The loan entity dictionary
        path: JSON path string
        
    Returns:
        The extracted value or None if not found
        
    Example:
        >>> extract_value_from_entity_path(entity, "property.city")
        "Germantown"
        
        >>> extract_value_from_entity_path(entity, "applications[0].borrower.firstName")
        "Maryouri"
        
        >>> extract_value_from_entity_path(entity, "contacts[?contactType=SETTLEMENT_AGENT].email")
        "crystal@ravents.com"
    """
    if not path or not entity:
        return None
    
    try:
        # Parse path into parts
        parts = []
        current_part = ""
        in_bracket = False
        
        for char in path:
            if char == '[':
                if current_part:
                    parts.append(('key', current_part))
                    current_part = ""
                in_bracket = True
            elif char == ']':
                if current_part:
                    # Check for conditions like [?contactType=SELLER]
                    if current_part.startswith('?'):
                        parts.append(('filter', current_part[1:]))
                    else:
                        parts.append(('index', int(current_part)))
                    current_part = ""
                in_bracket = False
            elif char == '.' and not in_bracket:
                if current_part:
                    parts.append(('key', current_part))
                    current_part = ""
            else:
                current_part += char
        
        if current_part:
            parts.append(('key', current_part))
        
        # Navigate through the path
        current = entity
        for part_type, part_value in parts:
            if current is None:
                return None
            
            if part_type == 'key':
                if isinstance(current, dict):
                    current = current.get(part_value)
                else:
                    return None
            elif part_type == 'index':
                if isinstance(current, list) and len(current) > part_value:
                    current = current[part_value]
                else:
                    return None
            elif part_type == 'filter':
                # Handle filters like [?contactType=SETTLEMENT_AGENT]
                if isinstance(current, list):
                    filter_parts = part_value.split('=')
                    if len(filter_parts) == 2:
                        filter_key, filter_val = filter_parts
                        matched = [item for item in current if item.get(filter_key) == filter_val]
                        current = matched[0] if matched else None
                    else:
                        return None
                else:
                    return None
        
        return current
    except (KeyError, IndexError, AttributeError, TypeError, ValueError):
        return None


def find_field_by_entity_path(
    entity_path: str,
    loan_id: str = None,
    loan_entity: Dict[str, Any] = None,
    state: Dict[str, Any] = None
) -> Any:
    """Find field value from loan entity using entity path.
    
    This is a convenience function that:
    1. Gets loan_id from state if not provided
    2. Loads loan entity from tmp folder (or uses provided entity)
    3. Extracts value using JSON path
    4. Returns the value
    
    Args:
        entity_path: JSON path to the field (e.g., "property.city", "applications[0].borrower.firstName")
        loan_id: Optional loan GUID. If not provided, attempts to get from state parameter.
        loan_entity: Optional loan entity dict (if already loaded, avoids file read)
        state: Optional state dict to extract loan_id from (uses state["loan_id"])
        
    Returns:
        The extracted field value, or None if not found
        
    Note:
        In tools with state access, you typically don't need to pass loan_id:
        >>> value = find_field_by_entity_path("property.city", state=state)
        
        Or if you have loan_id from state:
        >>> value = find_field_by_entity_path("property.city", loan_id=state.get("loan_id"))
        
    Example:
        >>> # Using loan_id (loads from tmp)
        >>> city = find_field_by_entity_path("property.city", loan_id="ae9dd6e2")
        >>> print(city)
        "Germantown"
        
        >>> # Using provided loan_entity
        >>> entity = load_loan_entity_from_tmp()
        >>> ssn = find_field_by_entity_path("applications[0].borrower.taxIdentificationIdentifier", loan_entity=entity)
        >>> print(ssn)
        "163-96-6483"
        
        >>> # Get settlement agent email with filtering
        >>> email = find_field_by_entity_path("contacts[?contactType=SETTLEMENT_AGENT].email", loan_id="ae9dd6e2")
        >>> print(email)
        "crystal@ravents.com"
        
        >>> # Get current employer
        >>> employer = find_field_by_entity_path("applications[0].borrower.employment[?currentEmploymentIndicator=true].employerName", loan_id="ae9dd6e2")
        >>> print(employer)
        "Advance Auto Parts"
        
        >>> # In a tool with state access (loan_id from state)
        >>> value = find_field_by_entity_path("property.city", state=state)
    """
    # Get loan_id from state if not provided
    if loan_id is None and state is not None:
        loan_id = state.get("loan_id")
    
    # Get loan entity if not provided
    if loan_entity is None:
        loan_entity = load_loan_entity_from_tmp(loan_id)
        
        if loan_entity is None:
            return None
    
    # Extract value using path
    return extract_value_from_entity_path(loan_entity, entity_path)


def find_multiple_fields_by_entity_paths(
    field_paths: Dict[str, str],
    loan_id: str = None,
    loan_entity: Dict[str, Any] = None,
    state: Dict[str, Any] = None
) -> Dict[str, Any]:
    """Find multiple field values from loan entity using entity paths.
    
    Efficiently loads loan entity once and extracts multiple fields.
    
    Args:
        field_paths: Dictionary mapping result keys to entity paths
                    Example: {"borrower_name": "applications[0].borrower.firstName"}
        loan_id: Optional loan GUID. If not provided, attempts to get from state.
        loan_entity: Optional loan entity dict (if already loaded)
        state: Optional state dict to extract loan_id from (uses state["loan_id"])
        
    Returns:
        Dictionary with extracted values
        
    Example:
        >>> paths = {
        ...     "borrower_first": "applications[0].borrower.firstName",
        ...     "borrower_last": "applications[0].borrower.lastName",
        ...     "property_city": "property.city",
        ...     "loan_amount": "baseLoanAmount"
        ... }
        >>> values = find_multiple_fields_by_entity_paths(paths, loan_id="ae9dd6e2")
        >>> print(values)
        {
            "borrower_first": "Maryouri",
            "borrower_last": "Arizaga Zegarra",
            "property_city": "Germantown",
            "loan_amount": 417302.0
        }
        
        >>> # In a tool with state access
        >>> values = find_multiple_fields_by_entity_paths(paths, state=state)
    """
    # Get loan_id from state if not provided
    if loan_id is None and state is not None:
        loan_id = state.get("loan_id")
    
    # Get loan entity if not provided
    if loan_entity is None:
        loan_entity = load_loan_entity_from_tmp(loan_id)
        
        if loan_entity is None:
            return {key: None for key in field_paths.keys()}
    
    # Extract all values
    result = {}
    for key, path in field_paths.items():
        result[key] = extract_value_from_entity_path(loan_entity, path)
    
    return result


# =============================================================================
# ENCOMPASS API FIELD RETRIEVAL (Using EncompassConnect)
# =============================================================================

def get_fields_from_encompass(
    field_ids: Union[List[str], str],
    loan_id: str = None,
    state: Dict[str, Any] = None,
    entity_paths: Dict[str, str] = None
) -> Dict[str, Any]:
    """Get field values from Encompass API with optional tmp file fallback.
    
    Uses EncompassConnect which handles automatic token refresh and authentication.
    Accepts single field ID or list of field IDs. Optionally falls back to tmp file
    if API call fails and entity_paths are provided.
    
    Args:
        field_ids: Single field ID or list of field IDs to retrieve
                   Examples: "4000" or ["4000", "4002", "1109"]
        loan_id: Optional loan GUID. If not provided, extracted from state.
        state: Optional state dict to extract loan_id from (uses state["loan_id"])
        entity_paths: Optional dict mapping field_id to entity_path for tmp file fallback
                      Example: {"4000": "applications[0].borrower.firstName"}
        
    Returns:
        Dictionary mapping field_id to value (None if empty/not found)
        If single field ID provided, returns dict with single entry
        
    Example:
        >>> # Simple: Get from API using state
        >>> fields = get_fields_from_encompass(["4000", "4002", "1109"], state=state)
        >>> print(fields)
        {"4000": "Maryouri", "4002": "Arizaga Zegarra", "1109": 417302.0}
        
        >>> # With fallback: Try API, fall back to tmp if fails
        >>> fields = get_fields_from_encompass(
        ...     field_ids=["4000", "4002"],
        ...     state=state,
        ...     entity_paths={
        ...         "4000": "applications[0].borrower.firstName",
        ...         "4002": "applications[0].borrower.lastName"
        ...     }
        ... )
        
        >>> # Single field (returns dict with one entry)
        >>> field = get_fields_from_encompass("4000", state=state)
        >>> print(field)
        {"4000": "Maryouri"}
        
        >>> # With custom fields
        >>> fields = get_fields_from_encompass([
        ...     "4000", "4002", "1109",
        ...     "CX.CTC", "CX.CDAPPROVED"
        ... ], state=state)
    """
    from .encompass_io import read_fields
    
    # ALWAYS prefer state.loan_id over explicit loan_id parameter
    # This prevents downstream callers from passing a hallucinated GUID
    loan_id = resolve_loan_id(state or {}, loan_id, step_label="ENCOMPASS_READ")
    
    if loan_id is None:
        logger.error("[ENCOMPASS] No loan_id provided and not in state")
        # Return empty dict with None values
        if isinstance(field_ids, str):
            return {field_ids: None}
        return {field_id: None for field_id in field_ids}
    
    # Handle single field ID
    if isinstance(field_ids, str):
        field_ids = [field_ids]
    
    # Try Encompass API first
    try:
        result = read_fields(loan_id, field_ids, state=state)
        
        # If entity_paths provided, try fallback for failed fields
        if entity_paths:
            for field_id in field_ids:
                if result.get(field_id) is None and field_id in entity_paths:
                    try:
                        # Try tmp file fallback
                        entity_path = entity_paths[field_id]
                        fallback_value = find_field_by_entity_path(
                            entity_path,
                            loan_id=loan_id
                        )
                        if fallback_value is not None:
                            result[field_id] = fallback_value
                            logger.debug(f"[ENCOMPASS] Used tmp fallback for field {field_id}")
                    except Exception as e:
                        logger.debug(f"[ENCOMPASS] Fallback failed for {field_id}: {e}")
        
        return result
        
    except Exception as e:
        logger.error(f"[ENCOMPASS] Error reading fields {field_ids}: {e}")
        
        # If API fails and entity_paths provided, try all fallbacks
        if entity_paths:
            logger.info("[ENCOMPASS] API failed, trying tmp file fallback for all fields")
            result = {}
            for field_id in field_ids:
                if field_id in entity_paths:
                    try:
                        entity_path = entity_paths[field_id]
                        value = find_field_by_entity_path(entity_path, loan_id=loan_id)
                        result[field_id] = value
                    except Exception as fallback_error:
                        logger.debug(f"[ENCOMPASS] Fallback failed for {field_id}: {fallback_error}")
                        result[field_id] = None
                else:
                    result[field_id] = None
            return result
        
        # No fallback available
        return {field_id: None for field_id in field_ids}


# =============================================================================
# STEP 2.4: BORROWER AKA VERIFICATION
# =============================================================================

# Encompass field ID for Borrower AKA
BORROWER_AKA_FIELD_ID = "1869"


def normalize_aka_name(name: str) -> str:
    """Normalize an AKA name for comparison.
    
    Converts to uppercase, removes extra whitespace, and strips punctuation.
    
    Args:
        name: The AKA name to normalize
        
    Returns:
        Normalized name string
    """
    if not name or not isinstance(name, str):
        return ""
    # Uppercase, strip, and normalize whitespace
    normalized = " ".join(name.upper().strip().split())
    return normalized


def parse_encompass_aka_field(aka_value: Any) -> List[str]:
    """Parse the Encompass AKA field value into a list of names.
    
    The Encompass field 1869 may contain AKA names as:
    - A comma-separated string: "JOHN DOE, J DOE, JOHNNY DOE"
    - A semicolon-separated string: "JOHN DOE; J DOE; JOHNNY DOE"
    - A newline-separated string
    - A list
    
    Args:
        aka_value: The raw value from Encompass field 1869
        
    Returns:
        List of normalized AKA names
    """
    if not aka_value:
        return []
    
    if isinstance(aka_value, list):
        return [normalize_aka_name(n) for n in aka_value if n]
    
    if isinstance(aka_value, str):
        # Try different separators
        names = []
        # First try semicolon
        if ";" in aka_value:
            names = [n.strip() for n in aka_value.split(";") if n.strip()]
        # Then try comma
        elif "," in aka_value:
            names = [n.strip() for n in aka_value.split(",") if n.strip()]
        # Then try newline
        elif "\n" in aka_value:
            names = [n.strip() for n in aka_value.split("\n") if n.strip()]
        else:
            # Single name
            names = [aka_value.strip()] if aka_value.strip() else []
        
        return [normalize_aka_name(n) for n in names if n]
    
    return []


def generate_name_variations(first: str, middle: str, last: str) -> List[str]:
    """Generate common name variations from full name parts.
    
    Creates variations using initials and abbreviations that are commonly
    used as AKA names. All variations are normalized to uppercase.
    
    Args:
        first: First name (e.g., "John")
        middle: Middle name (e.g., "Paul") - can be empty
        last: Last name (e.g., "Doe")
        
    Returns:
        List of unique name variations, normalized to uppercase.
        
    Example:
        >>> generate_name_variations("John", "Paul", "Doe")
        ['J DOE', 'J. DOE', 'JOHN D', 'JOHN D.', 'JP DOE', 'J.P. DOE', 
         'J P DOE', 'JOHN P DOE', 'JOHN P. DOE', 'J PAUL DOE', 'J. PAUL DOE',
         'JOHN PAUL D', 'JOHN PAUL D.']
    """
    variations = set()
    
    # Normalize inputs
    first = (first or "").strip().upper()
    middle = (middle or "").strip().upper()
    last = (last or "").strip().upper()
    
    if not first or not last:
        return []
    
    first_initial = first[0] if first else ""
    last_initial = last[0] if last else ""
    middle_initial = middle[0] if middle else ""
    
    # === Variations WITHOUT middle name ===
    # First initial + Last: J DOE, J. DOE
    if first_initial:
        variations.add(f"{first_initial} {last}")
        variations.add(f"{first_initial}. {last}")
    
    # First + Last initial: JOHN D, JOHN D.
    if last_initial:
        variations.add(f"{first} {last_initial}")
        variations.add(f"{first} {last_initial}.")
    
    # === Variations WITH middle name ===
    if middle:
        # First initial + Middle initial + Last: JP DOE, J.P. DOE, J P DOE
        if first_initial and middle_initial:
            variations.add(f"{first_initial}{middle_initial} {last}")
            variations.add(f"{first_initial}.{middle_initial}. {last}")
            variations.add(f"{first_initial} {middle_initial} {last}")
        
        # First + Middle initial + Last: JOHN P DOE, JOHN P. DOE
        if middle_initial:
            variations.add(f"{first} {middle_initial} {last}")
            variations.add(f"{first} {middle_initial}. {last}")
        
        # First initial + Middle + Last: J PAUL DOE, J. PAUL DOE
        if first_initial:
            variations.add(f"{first_initial} {middle} {last}")
            variations.add(f"{first_initial}. {middle} {last}")
        
        # First + Middle + Last initial: JOHN PAUL D, JOHN PAUL D.
        if last_initial:
            variations.add(f"{first} {middle} {last_initial}")
            variations.add(f"{first} {middle} {last_initial}.")
    
    # Normalize all variations (remove extra spaces, uppercase)
    normalized_variations = []
    for v in variations:
        normalized = " ".join(v.split())  # Normalize whitespace
        if normalized:
            normalized_variations.append(normalized)
    
    return sorted(list(set(normalized_variations)))


def verify_borrower_aka(
    loan_id: str,
    extracted_fields: Dict[str, Any],
    state: Dict[str, Any] = None,
) -> Dict[str, Any]:
    """Verify Borrower AKA names for Step 2.4.
    
    Compares AKA names from Encompass field 1869 against:
    - borrower_aka from credit report extraction
    - fraud_aka from fraud report extraction
    - id_aka from ID documents (Driver's License, Passport, etc.)
    - generated name variations (as fallback for unique additions)
    
    If any extracted AKAs are missing from Encompass, creates a flag
    with suggestion to complete the field.
    
    Args:
        loan_id: The loan GUID
        extracted_fields: Dictionary containing borrower_aka and fraud_aka
        state: Optional state dict (for loan_id fallback)
        
    Returns:
        Dictionary with verification result:
        {
            "success": True,
            "verification": "aka_names",
            "encompass_akas": [...],
            "credit_akas": [...],
            "fraud_akas": [...],
            "combined_akas": [...],
            "missing_akas": [...],
            "is_complete": bool,
            "needs_update": bool,
            "updated_value": str or None,
            "flag": {...} or None,
            "message": str
        }
    """
    # Get loan_id from state if not provided
    if loan_id is None and state is not None:
        loan_id = state.get("loan_id")
    
    if not loan_id:
        logger.error("[AKA] No loan_id provided for AKA verification")
        return {
            "success": False,
            "verification": "aka_names",
            "error": "No loan_id provided",
            "message": "❌ AKA verification failed: No loan_id"
        }
    
    # Get field 1869 from Encompass
    try:
        encompass_result = get_fields_from_encompass(
            field_ids=[BORROWER_AKA_FIELD_ID],
            loan_id=loan_id,
            state=state,
            entity_paths={BORROWER_AKA_FIELD_ID: "applications[0].borrower.aliasName"}
        )
        encompass_aka_raw = encompass_result.get(BORROWER_AKA_FIELD_ID)
    except Exception as e:
        logger.error(f"[AKA] Error reading Encompass field {BORROWER_AKA_FIELD_ID}: {e}")
        encompass_aka_raw = None
    
    # Parse Encompass AKA into list
    encompass_akas = parse_encompass_aka_field(encompass_aka_raw)
    encompass_akas_set = set(encompass_akas)
    
    logger.info(f"[AKA] Encompass field 1869 contains {len(encompass_akas)} AKAs: {encompass_akas}")
    
    # Get borrower_aka from credit report extraction
    credit_aka_data = extracted_fields.get("borrower_aka", {})
    if isinstance(credit_aka_data, dict):
        credit_akas_raw = credit_aka_data.get("value", [])
    else:
        credit_akas_raw = credit_aka_data if isinstance(credit_aka_data, list) else []
    
    credit_akas = [normalize_aka_name(n) for n in credit_akas_raw if n]
    logger.info(f"[AKA] Credit report AKAs: {credit_akas}")
    
    # Get fraud_aka from fraud report extraction
    fraud_aka_data = extracted_fields.get("fraud_aka", {})
    if isinstance(fraud_aka_data, dict):
        fraud_akas_raw = fraud_aka_data.get("value", [])
    else:
        fraud_akas_raw = fraud_aka_data if isinstance(fraud_aka_data, list) else []
    
    fraud_akas = [normalize_aka_name(n) for n in fraud_akas_raw if n]
    logger.info(f"[AKA] Fraud report AKAs: {fraud_akas}")
    
    # === Get name from ID documents (Driver's License, Passport, etc.) ===
    id_akas = []
    
    # Helper to extract value from field dict or raw value
    def get_field_value(field_data):
        if isinstance(field_data, dict):
            return field_data.get("value", "")
        return field_data if isinstance(field_data, str) else ""
    
    id_first_name = get_field_value(extracted_fields.get("borrower_first_name", ""))
    id_middle_name = get_field_value(extracted_fields.get("borrower_middle_name", ""))
    id_last_name = get_field_value(extracted_fields.get("borrower_last_name", ""))
    
    # === Also check per-document extractions from ID docs for full middle name ===
    # The merged extracted_fields may only have an initial (e.g. "A") if Passport was primary,
    # but Driver's License may have the full middle name (e.g. "A ASOMANING").
    id_doc_types = ["Driver's License", "Passport", "Permanent Resident Card", "SSN Card"]
    all_middle_names = set()
    if id_middle_name:
        all_middle_names.add(id_middle_name.strip().upper())
    
    doc_context = state.get("doc_context", {}) if state else {}
    extracted_entities = doc_context.get("extracted_entities", {})
    
    for doc_type in id_doc_types:
        doc_data = extracted_entities.get(doc_type, {})
        if doc_data:
            doc_first = doc_data.get("borrower_first_name", "")
            doc_middle = doc_data.get("borrower_middle_name", "")
            doc_last = doc_data.get("borrower_last_name", "")
            
            if doc_middle and isinstance(doc_middle, str) and doc_middle.strip():
                all_middle_names.add(doc_middle.strip().upper())
            
            # Build full name from each ID document directly
            if doc_first and doc_last:
                doc_full = normalize_aka_name(f"{doc_first} {doc_last}")
                if doc_full and doc_full not in id_akas:
                    id_akas.append(doc_full)
                if doc_middle:
                    doc_full_mid = normalize_aka_name(f"{doc_first} {doc_middle} {doc_last}")
                    if doc_full_mid and doc_full_mid not in id_akas:
                        id_akas.append(doc_full_mid)
    
    logger.info(f"[AKA] All middle names from ID docs: {all_middle_names}")
    
    # Use the longest middle name (most complete) for id_middle_name
    if all_middle_names:
        id_middle_name = max(all_middle_names, key=len)
        logger.info(f"[AKA] Using best middle name: '{id_middle_name}'")
    
    if id_first_name and id_last_name:
        # Add "FIRST LAST" variation
        id_full_name = normalize_aka_name(f"{id_first_name} {id_last_name}")
        if id_full_name and id_full_name not in id_akas:
            id_akas.append(id_full_name)
        # Add "FIRST MIDDLE LAST" variation if middle name exists
        if id_middle_name:
            id_full_with_middle = normalize_aka_name(f"{id_first_name} {id_middle_name} {id_last_name}")
            if id_full_with_middle and id_full_with_middle not in id_akas:
                id_akas.append(id_full_with_middle)
    
    logger.info(f"[AKA] ID document AKAs: {id_akas}")
    
    # === Get borrower name parts for generating variations ===
    # Prefer loan_field_summary (from Encompass entity fields 4000/4002) over
    # doc-extracted values — doc extraction (especially Credit Report) can merge
    # first+middle into one "first name" field, producing bogus AKA variations.
    borrower_first = ""
    borrower_last = ""
    borrower_middle = id_middle_name or ""
    
    if state:
        loan_field_summary = state.get("loan_field_summary", {})
        borrower_info = loan_field_summary.get("borrower_info", {})
        if isinstance(borrower_info, dict):
            # First name from Encompass field 4000 (single-word, authoritative)
            first_data = borrower_info.get("borrower_first_name", {})
            if isinstance(first_data, dict):
                borrower_first = (first_data.get("value") or "").strip()
            elif isinstance(first_data, str):
                borrower_first = first_data.strip()
            
            # Last name from Encompass field 4002
            last_data = borrower_info.get("borrower_last_name", {})
            if isinstance(last_data, dict):
                borrower_last = (last_data.get("value") or "").strip()
            elif isinstance(last_data, str):
                borrower_last = last_data.strip()
            
            # Middle name from loan_field_summary (fallback if not from ID docs)
            if not borrower_middle:
                middle_data = borrower_info.get("middle_name", {})
                if isinstance(middle_data, dict):
                    borrower_middle = (middle_data.get("value") or "").strip()
                elif isinstance(middle_data, str):
                    borrower_middle = middle_data.strip()
    
    # Fallback to doc-extracted values only if loan_field_summary had nothing
    if not borrower_first:
        borrower_first = id_first_name or ""
    if not borrower_last:
        borrower_last = id_last_name or ""
    
    # Safety: if first name still contains multiple words AND the extra word(s)
    # match the middle name, strip them to avoid doubled variations like
    # "BRANDON COREY COREY G." (Credit Report merging first+middle).
    if borrower_middle and borrower_first:
        first_upper = borrower_first.strip().upper()
        middle_upper = borrower_middle.strip().upper()
        first_parts = first_upper.split()
        if len(first_parts) > 1 and middle_upper in first_parts[1:]:
            borrower_first = first_parts[0]
            logger.info(
                f"[AKA] Stripped middle name '{middle_upper}' from first name "
                f"'{first_upper}' -> '{borrower_first}'"
            )
    
    logger.info(f"[AKA] Borrower name parts - First: '{borrower_first}', Middle: '{borrower_middle}', Last: '{borrower_last}'")
    
    # === NEW: Generate name variations ===
    generated_variations = []
    if borrower_first and borrower_last:
        generated_variations = generate_name_variations(borrower_first, borrower_middle, borrower_last)
        logger.info(f"[AKA] Generated {len(generated_variations)} name variations: {generated_variations}")
    
    # Combine AKAs from credit, fraud, and ID documents (unique)
    document_akas_set = set(credit_akas) | set(fraud_akas) | set(id_akas)
    
    # Filter generated variations - only keep those NOT already in document AKAs
    unique_variations = [v for v in generated_variations if v and v not in document_akas_set]
    logger.info(f"[AKA] Unique variations (not in other sources): {unique_variations}")
    
    # Combine all extracted AKAs (documents + unique variations)
    combined_akas_set = document_akas_set | set(unique_variations)
    combined_akas = sorted(list(combined_akas_set))
    
    logger.info(f"[AKA] Combined extracted AKAs: {combined_akas}")
    
    # Find missing AKAs (in extracted but not in Encompass)
    missing_akas = [aka for aka in combined_akas if aka and aka not in encompass_akas_set]
    
    # Determine if update is needed
    is_complete = len(missing_akas) == 0
    needs_update = not is_complete and len(combined_akas) > 0
    
    # Build result
    result = {
        "success": True,
        "verification": "aka_names",
        "field_id": BORROWER_AKA_FIELD_ID,
        "encompass_akas": encompass_akas,
        "credit_akas": credit_akas,
        "fraud_akas": fraud_akas,
        "id_akas": id_akas,
        "generated_variations": unique_variations,
        "combined_akas": combined_akas,
        "missing_akas": missing_akas,
        "is_complete": is_complete,
        "needs_update": needs_update,
        "updated_value": None,
        "flag": None,
        "write_field": None,
    }
    
    if is_complete:
        result["message"] = f"✅ AKA Names: Complete - all {len(combined_akas)} AKAs are in Encompass field 1869"
        result["suggestion"] = "pass"
        logger.info(f"[AKA] {result['message']}")
    else:
        # Build the full AKA list (Encompass + missing)
        full_aka_set = encompass_akas_set | set(combined_akas)
        full_akas = sorted(list(full_aka_set))
        # Format as semicolon-separated for Encompass
        updated_value = "; ".join(full_akas)
        
        result["updated_value"] = updated_value
        result["message"] = (
            f"❌ AKA Names: Incomplete - {len(missing_akas)} AKAs missing from Encompass. "
            f"Missing: {missing_akas}"
        )
        result["suggestion"] = "pass"  # Pass after update
        
        # Create flag for update
        result["flag"] = {
            "step": "2",
            "substep": "2.4",
            "field_name": "borrower_aka",
            "field_id": BORROWER_AKA_FIELD_ID,
            "title": "Borrower AKA Incomplete",
            "details": (
                f"AKA was not complete. Missing AKAs: {missing_akas}. "
                f"Encompass had: {encompass_akas}. "
                f"Credit report AKAs: {credit_akas}. "
                f"Fraud report AKAs: {fraud_akas}. "
                f"ID document AKAs: {id_akas}. "
                f"Generated variations: {unique_variations}. "
                f"Full list: {full_akas}"
            ),
            "suggestion": "AKA was not complete so it is completed - Pass",
            "severity": "info",
            "action": "overwrite",
            "category": "borrower_info",
            "verification": "aka_names",
        }
        
        # Prepare field to write (format matches write_borrower_vesting_info expectations)
        result["write_field"] = {
            "field_id": BORROWER_AKA_FIELD_ID,
            "field_name": "borrower_aka",
            "action": "OVERWRITE",
            "extracted_value": updated_value,
            "encompass_value": "; ".join(encompass_akas) if encompass_akas else "",
            "reason": "AKA list completed with missing names from credit report, fraud report, ID documents, and generated variations"
        }
        
        logger.info(f"[AKA] {result['message']}")
        logger.info(f"[AKA] Will update field {BORROWER_AKA_FIELD_ID} to: {updated_value}")
    
    return result




def is_property_in_flood_zone(state: Dict[str, Any]) -> tuple:
    """Check if property is in a flood zone requiring flood insurance.
    
    Checks for Flood Zone A or V which require flood insurance.
    
    Data sources (in priority order):
    1. Extracted fields from Flood Insurance document (Step 1)
    2. Loan entity specialFloodHazardAreaIndictor field
    
    Args:
        state: The agent state containing doc_context and loan_entity
        
    Returns:
        Tuple of (requires_flood_insurance: bool, flood_zone: str)
        - True if in Zone A or V (requires insurance)
        - False if in Zone X or other non-hazard zones
        - flood_zone string (e.g., "A", "V", "X", "AE", etc.)
    """
    flood_zone = ""
    
    # Priority 1: Check extracted fields from Flood Insurance document
    doc_context = state.get("doc_context", {})
    if doc_context:
        extracted_fields = doc_context.get("extracted_fields", {})
        if extracted_fields:
            flood_zone_data = extracted_fields.get("flood_zone", "")
            # Handle both dict format {"value": "A", ...} and string format "A"
            if isinstance(flood_zone_data, dict):
                flood_zone = flood_zone_data.get("value", "") or flood_zone_data.get("VALUE", "")
            else:
                flood_zone = flood_zone_data
    
    # Normalize flood zone
    flood_zone = str(flood_zone).strip().upper() if flood_zone else ""
    
    # Zones A and V require flood insurance
    # Zone X (or zones starting with X, B, C) do not require flood insurance
    requires_insurance = False
    if flood_zone:
        # Check if zone starts with A or V (includes AE, AH, AO, VE, etc.)
        if flood_zone.startswith("A") or flood_zone.startswith("V"):
            requires_insurance = True
    
    logger.info(f"[FLOOD_ZONE] Property flood zone: {flood_zone}, requires insurance: {requires_insurance}")
    
    return requires_insurance, flood_zone
