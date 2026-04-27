"""Shared utilities for workflow agent tools.

Provides Encompass I/O, eFolder client, field utilities, LLM call,
constants, and other business logic shared across all generated tools.

NOTE: Some imports may fail if dependencies are not installed.
Individual modules can still be imported directly.
"""

SHARED_AVAILABLE = True

# Safe imports — these are the most commonly used utilities
try:
    from .encompass_io import read_field, read_fields, write_field, write_fields
except ImportError:
    pass

try:
    from .efolder_client import EfolderClient, ExtractionRequest, ExtractionResult
except ImportError:
    pass

try:
    from .constants import LoanType, LoanPurpose, FieldIds
except ImportError:
    pass

try:
    from .llm_call import llm_call, llm_json_call, llm_structured_call, llm_classify, llm_compare, LLMResult
except ImportError:
    pass
