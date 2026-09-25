"""Lightweight format validators for TaskTile manifest values.

Used by the fallback engine so a *present-but-wrong* value (e.g. a mis-OCR'd zip
`90005` or a co-borrower name absorbed into `middleName`) also triggers fallback,
not just an absent value. Keep these cheap and permissive — they gate "should we
fall back?", they are not authoritative business validation.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Optional

_ZIP_RE = re.compile(r"^\d{5}(-\d{4})?$")
_SSN_RE = re.compile(r"^\d{3}-?\d{2}-?\d{4}$")
# Government ID numbers (DL / state ID / passport / green-card A#/USCIS) are
# alphanumeric, ~5-17 chars, and may contain internal hyphens or spaces
# (e.g. green-card "219-909-413"). Must start and end alphanumeric.
_ID_NUMBER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 \-]{3,15}[A-Za-z0-9]$")


def is_present(value: Any) -> bool:
    """Non-empty after trimming; empty dict/list counts as absent."""
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip() != ""
    if isinstance(value, (list, dict)):
        return len(value) > 0
    return True


def is_zip(value: Any) -> bool:
    return isinstance(value, (str, int)) and bool(_ZIP_RE.match(str(value).strip()))


def is_ssn(value: Any) -> bool:
    return isinstance(value, (str, int)) and bool(_SSN_RE.match(str(value).strip()))


def is_id_number(value: Any) -> bool:
    return isinstance(value, str) and bool(_ID_NUMBER_RE.match(value.strip()))


def is_date(value: Any, formats: Optional[list] = None) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    for fmt in (formats or ["%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y", "%B %d, %Y"]):
        try:
            datetime.strptime(value.strip(), fmt)
            return True
        except ValueError:
            continue
    return False


# Registry so the bucket config / callers can name a validator by string.
VALIDATORS = {
    "present": is_present,
    "zip": is_zip,
    "ssn": is_ssn,
    "id_number": is_id_number,
    "date": is_date,
}


def get_validator(name: Optional[str]):
    """Return a validator callable by name, defaulting to ``is_present``."""
    if not name:
        return is_present
    return VALIDATORS.get(name, is_present)
