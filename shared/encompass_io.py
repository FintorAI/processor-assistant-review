"""Encompass I/O operations using EncompassConnect.

This module provides read/write operations for Encompass loan fields
using the EncompassConnect client which handles authentication and
automatic token refresh.

"""

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# =============================================================================
# GUID HELPERS — defense against LLM hallucination
#
# Ported from LG-discOrch/tools/shared/encompass_io.py. These ensure that any
# value flowing into Encompass v3 API calls is actually shaped like a GUID;
# loan numbers, template strings, and other LLM-generated junk are rejected
# instead of producing 404s against /v3/loans/<garbage>. See UAT2 §42 in
# LG-discOrch for the bug that motivated this discipline.
# =============================================================================

# GUID regex pattern: 8-4-4-4-12 hexadecimal characters with optional braces
_GUID_PATTERN = re.compile(
    r"^[{]?[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}[}]?$"
)

# Template-string junk the LLM is known to pass as a "GUID" parameter.
PLACEHOLDER_PATTERNS = [
    "{loan_guid}", "loan_guid", "{loan_id}", "loan_id",
    "loan_guid_placeholder", "loan_guid_from_state",
    "{", "...", "<state", "placeholder",
]


def is_guid(value: Optional[str]) -> bool:
    """Return True if *value* looks like a real Encompass loan GUID."""
    if not value:
        return False
    return bool(_GUID_PATTERN.match(str(value).strip()))


def is_loan_number(value: Optional[str]) -> bool:
    """Return True if *value* looks like an Encompass loan number (8-12 digits)."""
    if not value:
        return False
    cleaned = str(value).strip()
    return cleaned.isdigit() and 8 <= len(cleaned) <= 12


def sanitize_guid(loan_id: Optional[str]) -> str:
    """Strip curly braces from a GUID. Encompass sometimes returns `{guid}`
    but the v3 API rejects them — this is the canonical place to clean."""
    if not loan_id:
        return ""
    sanitized = str(loan_id).strip().replace("{", "").replace("}", "")
    if sanitized != str(loan_id).strip():
        logger.debug("Sanitized GUID: '%s' -> '%s'", loan_id, sanitized)
    return sanitized


def get_loan_guid_from_state(
    state: Optional[Dict[str, Any]],
    loan_guid_param: str = "",
) -> Tuple[str, str]:
    """Canonical accessor for the loan GUID inside a tool.

    Always reads from `state["loan_id"]` (set by find_loan in Step 0) and
    validates it is actually a GUID. Any value an LLM passed as a parameter
    is IGNORED — but logged if it differs from state, which is useful when
    debugging hallucinations.

    Returns ``(guid, "")`` on success or ``("", error_message)`` on failure.
    """
    loan_guid_param = str(loan_guid_param).strip() if loan_guid_param else ""

    if not state or not isinstance(state, dict):
        return "", "No state available. State is required to get loan GUID (set by find_loan)."

    state_loan_id = state.get("loan_id", "")
    if state_loan_id and is_guid(str(state_loan_id)):
        if loan_guid_param and is_guid(loan_guid_param):
            param_sanitized = sanitize_guid(loan_guid_param)
            state_sanitized = sanitize_guid(str(state_loan_id))
            if param_sanitized != state_sanitized:
                logger.warning(
                    "[GUID] Parameter mismatch ignored! Param=%s... but state.loan_id=%s... — using state (authoritative).",
                    param_sanitized[:8], state_sanitized[:8],
                )
        return sanitize_guid(str(state_loan_id)), ""

    state_loan_guid = state.get("loan_guid", "")
    if state_loan_guid and is_guid(str(state_loan_guid)):
        return sanitize_guid(str(state_loan_guid)), ""

    return "", (
        f"Could not find valid loan GUID in state. "
        f"state.loan_id={state_loan_id!r}, state.loan_guid={state_loan_guid!r}. "
        f"Ensure find_loan was called first to set the loan_id in state."
    )

# ── Field-writes ledger ──────────────────────────────────────────────
# Module-level accumulator that write_fields() appends to on every
# successful call (real or dry-run).  Tools drain it via
# flush_field_writes_ledger() before returning their Command(update=...).
_FIELD_WRITES_LEDGER: List[Dict[str, Any]] = []


def flush_field_writes_ledger() -> List[Dict[str, Any]]:
    """Drain and return all accumulated field-write entries since the last flush."""
    global _FIELD_WRITES_LEDGER
    entries = _FIELD_WRITES_LEDGER[:]
    _FIELD_WRITES_LEDGER = []
    return entries


def get_field_writes_count() -> int:
    """Return the number of writes accumulated since the last flush."""
    return len(_FIELD_WRITES_LEDGER)


def _sync_state_cache(updates: Dict[str, Any], state: Optional[dict]) -> None:
    """Propagate written values back into state['los_fields'] so downstream
    tools see the updated value instead of the stale pre-write cache."""
    if not state or not updates:
        return
    los = state.get("los_fields")
    if not isinstance(los, dict):
        return

    fid_to_key: Dict[str, str] = {}
    for key, entry in los.items():
        if isinstance(entry, dict) and "field_id" in entry:
            fid_to_key[str(entry["field_id"]).strip()] = key

    for fid, val in updates.items():
        key = fid_to_key.get(str(fid).strip())
        if key is not None:
            los[key]["value"] = val
            logger.debug(f"State cache synced: los_fields[{key!r}] (field {fid}) = {val!r}")


def _record_writes(updates: Dict[str, Any], state: Optional[dict], dry_run: bool) -> None:
    """Append each field in *updates* to the module-level ledger."""
    substep = (state or {}).get("current_substep", "?")
    ts = datetime.now(timezone.utc).isoformat()
    for fid, val in updates.items():
        _FIELD_WRITES_LEDGER.append({
            "field_id": fid,
            "value": val,
            "substep": substep,
            "dry_run": dry_run,
            "timestamp": ts,
        })
    _sync_state_cache(updates, state)

try:
    from encompass_client import get_encompass_client
except ImportError:
    get_encompass_client = None
    logger.warning("encompass_client not available — Encompass I/O will fail at runtime")


def read_field(loan_id: str, field_id: str, context: str = "", state: dict = None) -> Optional[Any]:
    """Read a single field from a loan.
    
    Args:
        loan_id: Encompass loan GUID
        field_id: Field ID to read
        context: Optional logging context
        state: Optional state dict to determine environment
        
    Returns:
        Field value, or None if empty/not found
    """
    result = read_fields(loan_id, [field_id], context, state=state)
    return result.get(field_id)


def _parse_invalid_field_ids(error_body: str) -> set:
    """Parse Encompass 400 error body and return the set of invalid field ID strings.

    Error format example:
        {"errors": [{"summary": "contract[10]", "details": "Invalid field id: '1182'"}]}
    """
    import re
    return set(re.findall(r"Invalid field id: '([^']+)'", error_body))


# Patterns that name the offending field/value inside an Encompass 400 error body,
# mapped to a short human-readable reason. Covers the failure modes seen in prod:
#   - Invalid field id: '1182'                         (unknown field id)
#   - Invalid custom field 'CX.NONDEL.INV.APPROVAL'.   (custom field not in instance)
#   - Invalid value for custom field 'CUST50FV'. ...   (bad value/format)
#   - Cannot update readonly field with id: 1872       (read-only field)
#   - changes[0]...customFields[(fieldName == 'CUST50FV')].value  (serialization path)
_FIELD_ERROR_PATTERNS = [
    (re.compile(r"Invalid field id:\s*'([^']+)'", re.I), "invalid field id"),
    (re.compile(r"readonly field with id:\s*([^\s\"',}\]]+)", re.I), "read-only field (not writable via field API)"),
    (re.compile(r"Invalid value for custom field\s*'([^']+)'", re.I), "invalid value/format"),
    (re.compile(r"Invalid custom field\s*'([^']+)'", re.I), "custom field not defined in this Encompass instance"),
    (re.compile(r"fieldName\s*==\s*'([^']+)'", re.I), "invalid value/format"),
]


def _parse_field_errors(error_body: str) -> Dict[str, str]:
    """Return ``{field_id_or_name: reason}`` for every offending field named in
    a 400 error body. Empty dict if the body does not identify a specific field."""
    found: Dict[str, str] = {}
    body = error_body or ""
    for pattern, reason in _FIELD_ERROR_PATTERNS:
        for match in pattern.findall(body):
            fid = (match or "").strip()
            if fid and fid not in found:
                found[fid] = reason
    return found


def humanize_write_error(error_str: str) -> str:
    """Turn a raw Encompass APIError string into a concise, readable message.

    Encompass wraps failures as ``Field write failed (status 400): {json}``.
    This extracts the human ``details``/``summary`` so flags surface what went
    wrong instead of a raw JSON blob.
    """
    if not error_str:
        return ""
    match = re.search(r"\{.*\}", error_str, re.S)
    if match:
        try:
            body = json.loads(match.group(0))
            errors = body.get("errors")
            if isinstance(errors, list) and errors:
                parts = []
                for e in errors:
                    if isinstance(e, dict):
                        detail = e.get("details") or e.get("summary")
                        if detail:
                            parts.append(str(detail))
                if parts:
                    return "; ".join(parts)
            summary = body.get("summary")
            details = body.get("details")
            if summary or details:
                return " — ".join(p for p in (summary, details) if p)
        except (ValueError, TypeError):
            pass
    return error_str


def read_fields(loan_id: str, field_ids: List[str], context: str = "", state: dict = None) -> Dict[str, Any]:
    """Read multiple fields from a loan using EncompassConnect.

    On a 400 "Invalid field id" response, automatically removes the offending
    IDs and retries once so that a single bad field ID does not wipe out the
    entire batch. Invalid IDs are logged as warnings.

    Args:
        loan_id: Encompass loan GUID
        field_ids: List of field IDs to read
        context: Optional logging context
        state: Optional state dict to determine environment

    Returns:
        Dictionary mapping field_id to value (None if empty/not found)
    """
    if not field_ids:
        return {}

    ctx = f"{context} " if context else ""

    def _normalize(result: dict, ids: list) -> dict:
        normalized = {}
        for fid in ids:
            value = result.get(fid)
            normalized[fid] = value if (value is not None and str(value).strip() not in ("", "//")) else None
        return normalized

    client = get_encompass_client(state=state)

    try:
        logger.debug(f"{ctx}Reading {len(field_ids)} fields from loan {loan_id[:8]}...")
        result = client.get_field(loan_id, field_ids)
        return _normalize(result, field_ids)

    except Exception as e:
        error_str = str(e)

        # Detect 400 with invalid field IDs — retry without the bad ones
        if "400" in error_str and "Invalid field id" in error_str:
            bad_ids = _parse_invalid_field_ids(error_str)
            valid_ids = [fid for fid in field_ids if fid not in bad_ids]
            logger.warning(
                f"{ctx}400 Bad Request — removing {len(bad_ids)} invalid field ID(s): "
                f"{sorted(bad_ids)}. Retrying with {len(valid_ids)} valid fields."
            )
            if not valid_ids:
                logger.error(f"{ctx}All field IDs were invalid — returning empty result.")
                return {fid: None for fid in field_ids}

            try:
                result = client.get_field(loan_id, valid_ids)
                normalized = _normalize(result, valid_ids)
                # Fill in None for the removed bad IDs
                for fid in bad_ids:
                    normalized[fid] = None
                return normalized
            except Exception as e2:
                logger.error(f"{ctx}Retry also failed: {e2}")
                raise e2

        logger.error(f"{ctx}Error reading fields: {e}")
        raise


def write_field(loan_id: str, field_id: str, value: Any, state: dict = None) -> bool:
    """Write a single field to a loan using the fieldWriter API.
    
    Delegates to write_fields() which uses the Encompass v3 fieldWriter POST
    endpoint (/encompass/v3/loans/{loan_id}/fieldWriter). This endpoint
    properly translates field IDs (including virtual fields like L427,
    vendor fields like VEND.X*, and custom fields like CX.*) to their
    correct model paths.
    
    NOTE: Do NOT use the PATCH API (client.write_field) for field IDs.
    The PATCH endpoint expects JSON schema property names, not field IDs,
    and will reject virtual/vendor/custom field IDs with "Invalid field name".
    
    Args:
        loan_id: Encompass loan GUID
        field_id: Field ID to write
        value: Value to write
        state: Optional state dict to determine environment
        
    Returns:
        True if successful
    """
    return write_fields(loan_id, {field_id: value}, state=state)


def write_fields(loan_id: str, updates: Dict[str, Any], state: dict = None) -> bool:
    """Write multiple fields to a loan using EncompassConnect.

    Uses EncompassConnect.write_fields() which handles:
    - Automatic token refresh on 401 errors
    - Credential-based re-authentication

    When dry_run is enabled (via registry), logs what would be written
    and returns True without touching Encompass.

    Args:
        loan_id: Encompass loan GUID
        updates: Dictionary mapping field IDs to values
        state: Optional state dict to determine environment

    Returns:
        True if successful
    """
    written, bad_fields = write_fields_resilient(loan_id, updates, state=state)
    if bad_fields:
        logger.warning(
            f"write_fields: skipped {len(bad_fields)} rejected field(s): "
            + ", ".join(f"{fid} ({reason})" for fid, reason in sorted(bad_fields.items()))
        )
    return True


def write_fields_resilient(
    loan_id: str,
    updates: Dict[str, Any],
    state: dict = None,
) -> "tuple[Dict[str, Any], Dict[str, str]]":
    """Write fields to Encompass, resiliently skipping any field that the API rejects.

    A single bad field (unknown id, undefined custom field, read-only field, or
    bad value/format) makes Encompass reject the **entire** batch with a 400. To
    keep one bad field from dropping all the good ones, this:

      1. Attempts the batch. On a 400 that *names* the offending field(s) (see
         ``_parse_field_errors``), removes them and retries — looping until the
         write succeeds or no further offenders can be identified.
      2. If a 400 cannot be attributed to a named field, falls back to writing
         each remaining field **individually** to isolate the culprit, so the
         good fields still save and the bad one is identified precisely.

    Non-field errors (e.g. 409 loan locked, auth failures) are re-raised so the
    caller can surface them as-is.

    Args:
        loan_id: Encompass loan GUID
        updates: Dictionary mapping field IDs to values
        state: Optional state dict to determine environment

    Returns:
        Tuple of ``(actually_written: dict, bad_fields: dict)`` where
        ``bad_fields`` maps each rejected field ID/name to a short reason string
        (empty dict on full success).
    """
    if not updates:
        return {}, {}

    # Check dry_run from registry singleton
    dry_run = False
    try:
        from output.registry import DEV_MODE
        dry_run = getattr(DEV_MODE, "dry_run", False)
    except Exception:
        pass

    if dry_run:
        substep = (state or {}).get("current_substep", "?")
        logger.info(f"[DRY-RUN:{substep}] Would write {len(updates)} fields to {loan_id[:8]}: {updates}")
        _record_writes(updates, state, dry_run=True)
        return dict(updates), {}

    client = get_encompass_client(state=state)

    remaining: Dict[str, Any] = dict(updates)
    bad_fields: Dict[str, str] = {}

    # ── Phase 1: batch write, iteratively stripping named offenders ──────────
    while remaining:
        try:
            client.write_fields(loan_id, remaining)
            logger.info(f"Wrote {len(remaining)} fields to loan {loan_id[:8]}")
            _record_writes(remaining, state, dry_run=False)
            return dict(remaining), bad_fields
        except Exception as e:
            error_str = str(e)
            if "400" not in error_str:
                # Not a field-level rejection (lock/auth/etc.) — surface as-is.
                logger.error(f"Error writing fields: {e}")
                raise

            offenders = _parse_field_errors(error_str)
            to_remove = {k for k in list(remaining) if k in offenders}
            if to_remove:
                for k in to_remove:
                    bad_fields[k] = offenders[k]
                    remaining.pop(k, None)
                logger.warning(
                    f"400 on write — removing {len(to_remove)} rejected field(s): "
                    + ", ".join(f"{k} ({bad_fields[k]})" for k in sorted(to_remove))
                    + f". Retrying with {len(remaining)} field(s)."
                )
                continue
            # Could not attribute the 400 to a named field — isolate per-field.
            logger.warning(
                "400 on write could not be attributed to a named field — "
                f"isolating {len(remaining)} field(s) individually."
            )
            break

    # ── Phase 2: per-field isolation (only when culprit was not named) ───────
    if remaining:
        written: Dict[str, Any] = {}
        for fid, val in remaining.items():
            try:
                client.write_fields(loan_id, {fid: val})
                written[fid] = val
            except Exception as e2:
                err2 = str(e2)
                if "400" not in err2 and "Invalid" not in err2 and "readonly" not in err2.lower():
                    # A non-field error mid-isolation (e.g. lock) — record what we wrote and raise.
                    if written:
                        _record_writes(written, state, dry_run=False)
                    logger.error(f"Error writing field {fid}: {e2}")
                    raise
                bad_fields[fid] = humanize_write_error(err2)
                logger.warning(f"Field {fid} rejected and skipped: {bad_fields[fid]}")
        if written:
            logger.info(f"Isolation: wrote {len(written)} field(s) to loan {loan_id[:8]}")
            _record_writes(written, state, dry_run=False)
        return written, bad_fields

    # Everything was stripped as a named offender — nothing written.
    return {}, bad_fields


def get_loan_summary(loan_id: str) -> Dict[str, Any]:
    """Get a summary of key loan information.
    
    Args:
        loan_id: Encompass loan GUID
        
    Returns:
        Dictionary with key loan fields
    """
    from .constants import FieldIds
    
    field_ids = [
        FieldIds.BORROWER_FIRST_NAME,
        FieldIds.BORROWER_LAST_NAME,
        FieldIds.LOAN_TYPE,
        FieldIds.LOAN_PURPOSE,
        FieldIds.LOAN_AMOUNT,
        FieldIds.APPRAISED_VALUE,
        FieldIds.PURCHASE_PRICE,
        FieldIds.LTV,
        FieldIds.CLTV,
        FieldIds.INTEREST_RATE,
        FieldIds.LOAN_TERM,
        FieldIds.PROPERTY_STATE,
        FieldIds.PROPERTY_TYPE,
        FieldIds.OCCUPANCY_TYPE,
        FieldIds.APPLICATION_DATE,
        FieldIds.LOAN_STATUS,
    ]
    
    values = read_fields(loan_id, field_ids)
    
    def parse_float(val):
        if val is None:
            return None
        try:
            return float(str(val).replace(",", "").replace("$", "").replace("%", ""))
        except (ValueError, TypeError):
            return None
    
    def parse_int(val):
        if val is None:
            return None
        try:
            return int(float(str(val).replace(",", "")))
        except (ValueError, TypeError):
            return None
    
    return {
        "loan_id": loan_id,
        "borrower_first_name": values.get(FieldIds.BORROWER_FIRST_NAME),
        "borrower_last_name": values.get(FieldIds.BORROWER_LAST_NAME),
        "loan_type": values.get(FieldIds.LOAN_TYPE),
        "loan_purpose": values.get(FieldIds.LOAN_PURPOSE),
        "loan_amount": parse_float(values.get(FieldIds.LOAN_AMOUNT)),
        "appraised_value": parse_float(values.get(FieldIds.APPRAISED_VALUE)),
        "purchase_price": parse_float(values.get(FieldIds.PURCHASE_PRICE)),
        "ltv": parse_float(values.get(FieldIds.LTV)),
        "cltv": parse_float(values.get(FieldIds.CLTV)),
        "interest_rate": parse_float(values.get(FieldIds.INTEREST_RATE)),
        "loan_term": parse_int(values.get(FieldIds.LOAN_TERM)),
        "property_state": values.get(FieldIds.PROPERTY_STATE),
        "property_type": values.get(FieldIds.PROPERTY_TYPE),
        "occupancy_type": values.get(FieldIds.OCCUPANCY_TYPE),
        "application_date": values.get(FieldIds.APPLICATION_DATE),
        "loan_status": values.get(FieldIds.LOAN_STATUS),
    }


def get_loan_type(loan_id: str) -> Optional[str]:
    """Get loan type for a loan.
    
    Args:
        loan_id: Encompass loan GUID
        
    Returns:
        Loan type string, or None if not set
    """
    from .constants import FieldIds
    return read_field(loan_id, FieldIds.LOAN_TYPE)


def read_employment(
    loan_id: str,
    application_id: str = None,
    applicant_type: str = "borrower",
    state: dict = None,
) -> List[Dict[str, Any]]:
    """Fetch and normalise employment records from the Encompass v3 API.

    Calls:
        GET /encompass/v3/loans/{loanId}/applications/{applicationId}/{applicantType}/employment

    Normalises each record into a consistent shape aligned with the existing
    BE01xx/BE02xx field key names used throughout the tools layer::

        {
          "id":                   str,
          "current":              bool,   # currentEmploymentIndicator
          "employer_name":        str,
          "employer_phone":       str,    # phoneNumber
          "employer_street":      str,    # urla2020StreetAddress or addressStreetLine1
          "employer_unit_type":   str,
          "employer_unit_number": str,
          "employer_city":        str,    # addressCity
          "employer_state":       str,    # addressState
          "employer_zip":         str,    # addressPostalCode
          "position_title":       str,    # positionDescription
          "date_hired":           str,    # employmentStartDate
          "date_terminated":      str,    # endDate
          "years_in_job":         int,    # timeOnJobTermYears
          "months_in_job":        int,    # timeOnJobTermMonths
          "years_in_line_of_work":int,    # timeInLineOfWorkYears
          "monthly_base_pay":     float,  # basePayAmount
          "monthly_income":       float,  # monthlyIncomeAmount
          "self_employed":        bool,   # selfEmployedIndicator
          "_raw":                 dict,   # original API record
        }

    Raises:
        LookupError: if the API returns "collection does not exist"
            (no employment rows created yet in Encompass).

    Args:
        loan_id: Encompass loan GUID
        application_id: Auto-resolved if omitted
        applicant_type: "borrower" (default) or "coborrower"
        state: Optional state dict (passed to the HTTP client)
    """
    try:
        from encompass_client import get_employment
    except ImportError:
        logger.warning("encompass_client not available — read_employment will fail at runtime")
        return []

    records = get_employment(
        loan_id,
        application_id=application_id,
        applicant_type=applicant_type,
        state=state,
    )

    normalised = []
    for r in records:
        street = r.get("urla2020StreetAddress") or r.get("addressStreetLine1") or ""
        normalised.append({
            "id":                    r.get("id", ""),
            "current":               bool(r.get("currentEmploymentIndicator", False)),
            "employer_name":         (r.get("employerName") or "").strip(),
            "employer_phone":        (r.get("phoneNumber") or "").strip(),
            "employer_street":       street.strip(),
            "employer_unit_type":    (r.get("unitType") or "").strip(),
            "employer_unit_number":  (r.get("unitNumber") or "").strip(),
            "employer_city":         (r.get("addressCity") or "").strip(),
            "employer_state":        (r.get("addressState") or "").strip(),
            "employer_zip":          (r.get("addressPostalCode") or "").strip(),
            "position_title":        (r.get("positionDescription") or "").strip(),
            "date_hired":            (r.get("employmentStartDate") or "").strip(),
            "date_terminated":       (r.get("endDate") or "").strip(),
            "years_in_job":          r.get("timeOnJobTermYears"),
            "months_in_job":         r.get("timeOnJobTermMonths"),
            "years_in_line_of_work": r.get("timeInLineOfWorkYears"),
            "monthly_base_pay":      r.get("basePayAmount"),
            "monthly_income":        r.get("monthlyIncomeAmount"),
            "self_employed":         bool(r.get("selfEmployedIndicator", False)),
            "_raw":                  r,
        })

    logger.info(
        f"[ENCOMPASS] read_employment: {len(normalised)} {applicant_type} record(s) for loan {loan_id[:8]}"
    )
    return normalised


def read_other_assets(
    loan_id: str,
    application_id: str = None,
    state: dict = None,
) -> List[Dict[str, Any]]:
    """Fetch other assets for a loan from the Encompass v3 API.

    GET /encompass/v3/loans/{loanId}/applications/{applicationId}/otherAssets

    Each record shape (confirmed from test loan 2604964148)::

        {
          "id":               str,
          "borrowerType":     "Borrower" | "CoBorrower",
          "assetType":        "EarnestMoney" | "GiftFunds" | "LifeInsurance" | ...,
          "cashOrMarketValue": float,
          "altId":            str,
        }

    Returns empty list if collection does not exist (no rows yet).
    """
    try:
        from encompass_client import get_other_assets
    except ImportError:
        logger.warning("encompass_client not available — read_other_assets will fail at runtime")
        return []

    try:
        records = get_other_assets(loan_id, application_id=application_id, state=state)
    except LookupError:
        logger.info(f"[ENCOMPASS] read_other_assets: collection does not exist for loan {loan_id[:8]}")
        return []

    logger.info(f"[ENCOMPASS] read_other_assets: {len(records)} record(s) for loan {loan_id[:8]}")
    return records


def read_gifts_grants(
    loan_id: str,
    application_id: str = None,
    state: dict = None,
) -> List[Dict[str, Any]]:
    """Fetch and normalise all gifts and grants for a loan from the Encompass v3 API.

    GET /encompass/v3/loans/{loanId}/applications/{applicationId}/giftsGrants

    Each returned item shape (confirmed from test loan 2604964148)::

        {
          "id":                str,
          "asset_type":        str,   # "Grant" | "GiftOfCash" | "GiftOfEquity" | ...
          "source":            str,   # "FederalAgency" | "Relative" | "Employer" | ...
          "amount":            float,
          "owner":             str,   # "Borrower" | "CoBorrower" | "Both"
          "deposited":         bool,  # True if already in borrower account
        }

    Returns empty list if collection does not exist (no rows yet).
    """
    try:
        from encompass_client import get_gifts_grants
    except ImportError:
        logger.warning("encompass_client not available — read_gifts_grants will fail at runtime")
        return []

    try:
        records = get_gifts_grants(loan_id, application_id=application_id, state=state)
    except LookupError:
        logger.info(f"[ENCOMPASS] read_gifts_grants: collection does not exist for loan {loan_id[:8]}")
        return []

    normalized = []
    for r in records:
        normalized.append({
            "id":         r.get("id", ""),
            "asset_type": r.get("assetType", ""),
            "source":     r.get("source", ""),
            "amount":     r.get("amount") or 0.0,
            "owner":      r.get("owner", "Borrower"),
            "deposited":  r.get("depositedIndicator", False),
        })

    logger.info(f"[ENCOMPASS] read_gifts_grants: {len(normalized)} record(s) for loan {loan_id[:8]}")
    return normalized


def read_vods(loan_id: str, state: dict = None) -> List[Dict[str, Any]]:
    """Fetch and normalise all VOD entries for a loan from the Encompass v3 API.

    Each element in the returned list represents one **account row** (a single
    account type / account number) rather than one depository institution.
    This makes downstream comparison with per-copy bank-statement extractions
    straightforward.

    Two on-the-wire schemas are supported transparently:

      * **Legacy VOD** — ``depInstitution`` / ``for`` with an
        ``accountInformation[]`` list (``accountType``, ``accountInNameOf``,
        ``accountNumber``, ``cashOrMarketValue``).
      * **URLA-2020 ("2a Assets")** — ``holderName`` / ``owner`` with an
        ``items[]`` list (``type``, ``depositoryAccountName``,
        ``accountIdentifier``, ``urla2020CashOrMarketValueAmount``). Empty
        placeholder items are skipped.

    Both normalise to the identical row shape below.

    Returned item shape::

        {
          "vod_id":           str,   # Encompass VOD object ID
          "vod_index":        int,   # 1-based index within the loan
          "institution_name": str,   # depository name  (e.g. "PNC Bank")
          "borrower_type":    str,   # "BorrowerOnly" | "CoBorrowerOnly" | ...
          "account_type":     str,   # "CheckingAccount" | "SavingsAccount" | ...
          "account_holder":   str,   # name on account
          "account_number":   str,   # may be masked (e.g. "****2286")
          "balance":          float, # Cash or market value
        }

    Args:
        loan_id: Encompass loan GUID
        state: Optional state dict (passed to the underlying HTTP client)

    Returns:
        List of normalised account-row dicts (empty list if no VODs)
    """
    try:
        from encompass_client import get_vods
    except ImportError:
        logger.warning("encompass_client not available — read_vods will fail at runtime")
        return []

    raw_vods = get_vods(loan_id, state=state)
    rows: List[Dict[str, Any]] = []

    for vod in raw_vods:
        vod_id    = vod.get("id", "")
        vod_index = vod.get("vodIndex", 0)
        # Institution + owner differ between the legacy VOD schema and the
        # URLA-2020 ("2a Assets") schema:
        #   legacy   → depInstitution / for
        #   URLA-2020→ holderName     / owner
        institution = (vod.get("depInstitution") or vod.get("holderName") or "").strip()
        borrower_type = vod.get("for") or vod.get("owner") or ""

        # Account rows live under different keys per schema. The legacy form uses
        # ``accountInformation``; the URLA-2020 form uses ``items``. Prefer
        # whichever is populated so both layouts normalise to the same row shape.
        legacy_accounts = vod.get("accountInformation") or []
        urla_items = vod.get("items") or []

        if legacy_accounts:
            for acct in legacy_accounts:
                acct_type   = acct.get("accountType", "")
                acct_holder = (acct.get("accountInNameOf") or "").strip()
                acct_num    = (acct.get("accountNumber") or "").strip()
                try:
                    balance = float(acct.get("cashOrMarketValue") or 0)
                except (TypeError, ValueError):
                    balance = 0.0

                rows.append({
                    "vod_id":           vod_id,
                    "vod_index":        vod_index,
                    "institution_name": institution,
                    "borrower_type":    borrower_type,
                    "account_type":     acct_type,
                    "account_holder":   acct_holder,
                    "account_number":   acct_num,
                    "balance":          balance,
                })
        else:
            for item in urla_items:
                acct_type   = item.get("type", "")
                acct_holder = (item.get("depositoryAccountName") or "").strip()
                acct_num    = (item.get("accountIdentifier") or "").strip()
                raw_val     = item.get("urla2020CashOrMarketValueAmount")

                # URLA-2020 VODs pad the items array with empty placeholder rows
                # (only itemNumber + depositoryAccountGuid). Skip any row with no
                # account type, no account number, and no value.
                if not acct_type and not acct_num and raw_val in (None, ""):
                    continue

                try:
                    balance = float(raw_val or 0)
                except (TypeError, ValueError):
                    balance = 0.0

                rows.append({
                    "vod_id":           vod_id,
                    "vod_index":        vod_index,
                    "institution_name": institution,
                    "borrower_type":    borrower_type,
                    "account_type":     acct_type,
                    "account_holder":   acct_holder,
                    "account_number":   acct_num,
                    "balance":          balance,
                })

    logger.info(f"[ENCOMPASS] read_vods: {len(raw_vods)} VOD object(s) → {len(rows)} account row(s)")
    return rows


def add_vods(
    loan_id: str,
    accounts: List[Dict[str, Any]],
    state: dict = None,
) -> Dict[str, Any]:
    """Create new VOD entries (URLA-2020 / 2a Assets) on a loan application.

    Thin wrapper over ``encompass_client.add_vod_accounts``. Only *adds* new
    depository rows — existing VOD entries are never modified. Each ``accounts``
    dict uses the normalised row shape produced by ``read_vods``
    (``institution_name``, ``account_type``, ``account_number``,
    ``account_holder``, ``balance``).

    Returns ``{"success": bool, "added": [...], "error"?: str}``.
    """
    try:
        from encompass_client import add_vod_accounts
    except ImportError:
        logger.warning("encompass_client not available — add_vods will fail at runtime")
        return {"success": False, "error": "encompass_client not available", "added": []}

    if not accounts:
        return {"success": True, "added": []}

    result = add_vod_accounts(loan_id, accounts, state=state)
    logger.info(
        f"[ENCOMPASS] add_vods: requested {len(accounts)} → added "
        f"{len(result.get('added', []))} for loan {loan_id[:8]}"
    )
    return result


def update_vods(
    loan_id: str,
    completions: List[Dict[str, Any]],
    state: dict = None,
) -> Dict[str, Any]:
    """Complete blank subfields on existing VOD entries (checklist 08 #10).

    Thin wrapper over ``encompass_client.update_vod_accounts``. Only fills empty
    fields on an existing depository item (account type, cash/market value,
    account number, account holder) — never overwrites a populated value. Each
    ``completions`` dict carries ``vod_id``, ``account_number`` (to locate the
    item), and an ``updates`` map using the normalised ``read_vods`` keys.

    Returns ``{"success": bool, "updated": [...], "skipped": [...], "error"?: str}``.
    """
    try:
        from encompass_client import update_vod_accounts
    except ImportError:
        logger.warning("encompass_client not available — update_vods will fail at runtime")
        return {"success": False, "error": "encompass_client not available", "updated": [], "skipped": []}

    if not completions:
        return {"success": True, "updated": [], "skipped": []}

    result = update_vod_accounts(loan_id, completions, state=state)
    logger.info(
        f"[ENCOMPASS] update_vods: requested {len(completions)} → updated "
        f"{len(result.get('updated', []))} for loan {loan_id[:8]}"
    )
    return result


def merge_duplicate_vods(loan_id: str, state: dict = None) -> Dict[str, Any]:
    """Merge existing duplicate same-institution VOD entries into one (video 7 feedback).

        Thin wrapper over ``encompass_client.merge_duplicate_vods``. If Encompass
        has 2+ separate VOD entries for the same institution + owner, combines
        their account rows into the first entry's items (``PATCH .../vods?action=update``)
        and deletes the surplus entries (``PATCH .../vods?action=delete``) — both
        confirmed from the "V3 Manage VODs" API reference and live-tested
        successfully against prod loan 2607973377 (2026-07-22). Not yet wired to
        run automatically from a review tool — currently only called manually /
        ad hoc; see the wrapped function's docstring for request-shape details.

    Returns ``{"success": bool, "merged": [...], "skipped": [...], "error"?: str}``.
    """
    try:
        from encompass_client import merge_duplicate_vods as _merge_duplicate_vods
    except ImportError:
        logger.warning("encompass_client not available — merge_duplicate_vods will fail at runtime")
        return {"success": False, "error": "encompass_client not available", "merged": [], "skipped": []}

    result = _merge_duplicate_vods(loan_id, state=state)
    logger.info(
        f"[ENCOMPASS] merge_duplicate_vods: merged {len(result.get('merged', []))} group(s), "
        f"skipped {len(result.get('skipped', []))} for loan {loan_id[:8]}"
    )
    return result


def update_vols(
    loan_id: str,
    completions: List[Dict[str, Any]],
    state: dict = None,
) -> Dict[str, Any]:
    """Complete blank sub-fields on existing VOL (2c liability) rows (checklist 03 #8).

    Thin wrapper over ``encompass_client.update_vol_accounts``. Only fills empty
    scalar fields on an existing liability (unpaid balance, monthly payment,
    credit limit, account number) from a matched credit-report tradeline — never
    overwrites a populated value and never creates a missing liability. Each
    ``completions`` dict carries ``vol_id`` and an ``updates`` map using the
    normalised ``read_vols`` keys.

    Returns ``{"success": bool, "updated": [...], "skipped": [...], "error"?: str}``.
    """
    try:
        from encompass_client import update_vol_accounts
    except ImportError:
        logger.warning("encompass_client not available — update_vols will fail at runtime")
        return {"success": False, "error": "encompass_client not available", "updated": [], "skipped": []}

    if not completions:
        return {"success": True, "updated": [], "skipped": []}

    result = update_vol_accounts(loan_id, completions, state=state)
    logger.info(
        f"[ENCOMPASS] update_vols: requested {len(completions)} → updated "
        f"{len(result.get('updated', []))} for loan {loan_id[:8]}"
    )
    return result


def read_vols(loan_id: str, state: dict = None) -> List[Dict[str, Any]]:
    """Fetch all VOL (Verification of Liabilities) records from the Encompass v3 API.

    Each element represents one liability row as returned by:
        GET /encompass/v3/loans/{loanId}/applications/{applicationId}/vols

    Returned item shape::

        {
          "vol_id":               str,    # Encompass VOL object ID
          "holder_name":          str,    # creditor / institution name
          "liability_type":       str,    # e.g. "Revolving", "Installment"
          "owner":                str,    # "Borrower" | "CoBorrower" | "Both"
          "account_number":       str,    # may be masked
          "monthly_payment":      float,  # monthlyPaymentAmount
          "unpaid_balance":       float,  # unpaidBalanceAmount
          "credit_limit":         float,  # creditLimit (revolving only)
          "exclude_monthly_pay":  bool,   # "Exclude Monthly Payment" column
          "payoff_included":      bool,   # "To Be Paid Off" column
          "remaining_months":     int,    # remainingTermMonths
        }

    Raises:
        LookupError: if the VOL collection does not exist yet in Encompass.
    """
    try:
        from encompass_client import get_vols
    except ImportError:
        logger.warning("encompass_client not available — read_vols will fail at runtime")
        return []

    raw = get_vols(loan_id, state=state)  # may raise LookupError
    rows: List[Dict[str, Any]] = []

    for vol in raw:
        try:
            monthly = float(vol.get("monthlyPaymentAmount") or 0)
        except (TypeError, ValueError):
            monthly = 0.0
        try:
            balance = float(vol.get("unpaidBalanceAmount") or 0)
        except (TypeError, ValueError):
            balance = 0.0
        try:
            limit = float(vol.get("creditLimit") or 0)
        except (TypeError, ValueError):
            limit = 0.0
        try:
            remaining = int(vol.get("remainingTermMonths") or 0)
        except (TypeError, ValueError):
            remaining = 0

        rows.append({
            "vol_id":              vol.get("id", ""),
            "holder_name":         (vol.get("holderName") or "").strip(),
            "liability_type":      vol.get("liabilityType", ""),
            "owner":               vol.get("owner", ""),
            "account_number":      (vol.get("accountIdentifier") or "").strip(),
            "monthly_payment":     monthly,
            "unpaid_balance":      balance,
            "credit_limit":        limit,
            "exclude_monthly_pay": bool(vol.get("excludedFromTotalMonthlyPaymentIndicator", False)),
            "payoff_included":     bool(vol.get("payoffIncludedIndicator", False)),
            "remaining_months":    remaining,
        })

    return rows


def read_reo_properties(loan_id: str, state: dict = None) -> List[Dict[str, Any]]:
    """Fetch REO (Real Estate Owned) properties from the Encompass v3 API (Section 3).

    Uses:
        GET /encompass/v3/loans/{loanId}/applications/{applicationId}/reoProperties

    Returned item shape::

        {
          "reo_id":              str,   # Encompass object ID
          "street_address":      str,
          "city":                str,
          "state":               str,
          "postal_code":         str,
          "owner":               str,   # "Borrower" | "CoBorrower" | "Both"
          "disposition_status":  str,   # "Retain" | "Sold" | "PendingSale" | etc.
        }

    Returns empty list if no REO rows exist or collection not created.
    """
    try:
        from encompass_client import get_reo_properties
    except ImportError:
        logger.warning("encompass_client not available — read_reo_properties will fail at runtime")
        return []

    try:
        raw = get_reo_properties(loan_id, state=state)
    except LookupError:
        return []

    rows: List[Dict[str, Any]] = []
    for item in raw:
        rows.append({
            "reo_id":             item.get("id", ""),
            "street_address":     (item.get("streetAddress") or item.get("urla2020StreetAddress") or "").strip(),
            "city":               (item.get("city") or "").strip(),
            "state":              (item.get("state") or item.get("addressState") or "").strip(),
            "postal_code":        (item.get("postalCode") or item.get("addressPostalCode") or "").strip(),
            "owner":              item.get("owner", ""),
            "disposition_status": item.get("dispositionStatusType", ""),
        })

    return rows


def read_other_liabilities(loan_id: str, state: dict = None) -> List[Dict[str, Any]]:
    """Fetch Other Liabilities (Section 2d) from the Encompass v3 API.

    Uses:
        GET /encompass/v3/loans/{loanId}/applications/{applicationId}/otherLiabilities

    Each element represents one other-liability row.

    Returned item shape::

        {
          "liability_id":     str,    # Encompass object ID
          "liability_type":   str,    # e.g. "JobRelatedExpenses", "Alimony", etc.
          "monthly_payment":  float,  # monthlyPaymentAmount
          "owner":            str,    # "Borrower" | "CoBorrower" | "Both"
          "description":      str,    # free-text description if present
        }

    Returns empty list if no rows exist (both empty collection and LookupError).
    """
    try:
        from encompass_client import get_other_liabilities
    except ImportError:
        logger.warning("encompass_client not available — read_other_liabilities will fail at runtime")
        return []

    try:
        raw = get_other_liabilities(loan_id, state=state)
    except LookupError:
        return []

    rows: List[Dict[str, Any]] = []
    for item in raw:
        try:
            monthly = float(item.get("monthlyPaymentAmount") or 0)
        except (TypeError, ValueError):
            monthly = 0.0

        rows.append({
            "liability_id":    item.get("id", ""),
            "liability_type":  item.get("liabilityType", ""),
            "monthly_payment": monthly,
            "owner":           item.get("owner", ""),
            "description":     (item.get("description") or item.get("holderName") or "").strip(),
        })

    return rows
