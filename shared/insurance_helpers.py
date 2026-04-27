"""Shared helper functions for insurance verification tools (Steps 5.3-5.5).

Contains utility functions used across dwelling_verification, hoi_verification,
and flood_verification modules.
"""

import re
from datetime import datetime
from typing import Any, Optional

_DATE_FORMATS = (
    "%m/%d/%Y %I:%M:%S %p",
    "%m/%d/%Y %I:%M %p",
    "%m/%d/%Y %H:%M:%S",
    "%m/%d/%Y %H:%M",
    "%m/%d/%Y",
    "%m/%d/%y",
    "%m-%d-%Y",
    "%Y-%m-%dT%H:%M:%S.%f",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M:%SZ",
    "%Y-%m-%d",
    "%Y/%m/%d",
)


def _parse_date(date_str: str) -> Optional[datetime]:
    """Parse date string to datetime object.

    Handles all formats produced by Encompass LOS and document extraction,
    including US dates with AM/PM timestamps (e.g. ``1/27/2026 2:23 PM``).
    """
    if not date_str:
        return None

    clean = re.sub(r"[+-]\d{2}:\d{2}$", "", str(date_str).strip())
    clean = clean.rstrip("Z")

    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(clean, fmt)
        except (ValueError, TypeError):
            continue

    return None


def _is_same_month(date1: datetime, date2: datetime) -> bool:
    """Check if two dates are in the same month and year."""
    if not date1 or not date2:
        return False
    return date1.year == date2.year and date1.month == date2.month


def _is_manufactured_home(property_type: str) -> bool:
    """Check if property type is manufactured home."""
    if not property_type:
        return False
    prop_upper = property_type.upper()
    return prop_upper in ["MH", "MANUFACTURED", "MANUFACTUREDHOME", "MANUFACTURED HOME", "MOBILE", "MOBILEHOME"]


def _is_condo(property_type: str) -> bool:
    """Check if property type is condo."""
    if not property_type:
        return False
    prop_upper = property_type.upper()
    return prop_upper in ["CONDO", "CONDOMINIUM"]


def _is_conventional_loan(loan_type: str) -> bool:
    """Check if loan type is conventional."""
    if not loan_type:
        return False
    return "CONVENTIONAL" in loan_type.upper() or loan_type.upper() in ["CONV", "FNMA", "FHLMC"]


def _safe_float(value, default: float = 0.0) -> float:
    """Safely convert any value to float.
    
    Handles strings with currency symbols ($), commas, and whitespace.
    Returns default if conversion fails or value is None/empty.
    
    Examples:
        _safe_float("$35,000")    -> 35000.0
        _safe_float("329")        -> 329.0
        _safe_float(394718)       -> 394718.0
        _safe_float(None)         -> 0.0
        _safe_float("")           -> 0.0
        _safe_float("N/A")       -> 0.0
    """
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return float(value)
    try:
        if isinstance(value, str):
            cleaned = value.replace("$", "").replace(",", "").replace("%", "").strip()
            if not cleaned:
                return default
            return float(cleaned)
        return float(value)
    except (ValueError, TypeError):
        return default


def _get_float_value(data: dict, key: str, default: float = 0.0) -> float:
    """Safely get a float value from a dictionary."""
    value = data.get(key)
    return _safe_float(value, default)
