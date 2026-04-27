"""Shared constants for Disclosure Orchestrator.

This module contains field mappings, loan type constants, and MVP critical fields.
"""

from typing import Dict, List


# =============================================================================
# LOAN TYPE CONSTANTS
# =============================================================================

class LoanType:
    """Loan type constants."""
    CONVENTIONAL = "Conventional"
    FHA = "FHA"
    VA = "VA"
    USDA = "USDA"
    
    # MVP: Only Conventional is supported
    MVP_SUPPORTED = [CONVENTIONAL]
    
    @classmethod
    def is_mvp_supported(cls, loan_type: str) -> bool:
        """Check if a loan type is supported in MVP."""
        return loan_type in cls.MVP_SUPPORTED


class LoanPurpose:
    """Loan purpose constants."""
    PURCHASE = "Purchase"
    REFINANCE = "Refinance"
    CASHOUT = "CashOut"
    CONSTRUCTION = "Construction"


class PropertyState:
    """Property state constants."""
    # MVP: Only NV and CA are supported
    MVP_SUPPORTED = ["NV", "CA"]
    
    @classmethod
    def is_mvp_supported(cls, state: str) -> bool:
        """Check if a state is supported in MVP."""
        if state is None:
            return False
        return state.upper() in cls.MVP_SUPPORTED


# =============================================================================
# MVP EXCLUSIONS
# =============================================================================

class MVPExclusions:
    """States and loan types excluded from MVP automation."""
    
    # Texas has special state rules
    EXCLUDED_STATES = ["TX"]
    
    # Non-Conventional loans require manual processing
    EXCLUDED_LOAN_TYPES = ["FHA", "VA", "USDA"]
    
    @classmethod
    def is_excluded_state(cls, state: str) -> bool:
        """Check if state is excluded from MVP."""
        if state is None:
            return False
        return state.upper() in cls.EXCLUDED_STATES
    
    @classmethod
    def is_excluded_loan_type(cls, loan_type: str) -> bool:
        """Check if loan type is excluded from MVP."""
        if loan_type is None:
            return True
        return loan_type in cls.EXCLUDED_LOAN_TYPES


# =============================================================================
# ENCOMPASS FIELD ID MAPPINGS
# =============================================================================

class FieldIds:
    """Common Encompass field IDs."""
    
    # Borrower
    BORROWER_FIRST_NAME = "4000"
    BORROWER_LAST_NAME = "4002"
    BORROWER_SSN = "65"
    BORROWER_EMAIL = "1240"
    BORROWER_DOB = "1402"
    BORROWER_PHONE = "66"
    
    # Co-Borrower
    COBORROWER_FIRST_NAME = "4004"
    COBORROWER_LAST_NAME = "4006"
    COBORROWER_SSN = "97"
    
    # Property
    PROPERTY_ADDRESS = "11"
    PROPERTY_CITY = "12"
    PROPERTY_STATE = "14"
    PROPERTY_ZIP = "15"
    PROPERTY_COUNTY = "13"
    
    # Loan Terms
    LOAN_AMOUNT = "1109"
    INTEREST_RATE = "3"
    LOAN_TERM = "4"
    LOAN_TYPE = "1172"
    LOAN_PURPOSE = "19"
    AMORTIZATION_TYPE = "608"
    
    # Values
    APPRAISED_VALUE = "356"
    PURCHASE_PRICE = "136"
    LTV = "353"
    CLTV = "976"
    
    # Property Type
    PROPERTY_TYPE = "1041"
    OCCUPANCY_TYPE = "1811"
    NUMBER_OF_UNITS = "16"
    
    # Dates
    APPLICATION_DATE = "745"
    LE_DATE_ISSUED = "LE1.X1"
    CLOSING_DATE = "748"
    CD_DATE_ISSUED = "CD1.X1"
    DISBURSEMENT_DATE = "2553"
    
    # Lock
    LOCK_DATE = "761"
    LOCK_EXPIRATION = "762"
    RATE_LOCKED = "2400"
    
    # Status
    LOAN_STATUS = "1393"
    CURRENT_MILESTONE = "1987"
    
    # Contacts
    SETTLEMENT_AGENT = "VEND.X263"
    TITLE_COMPANY = "411"
    
    # LO Info
    LO_NAME = "317"
    LO_NMLS_ID = "3238"
    LO_COMPANY_NAME = "3331"
    LO_COMPANY_NMLS = "3330"


# =============================================================================
# DISCLOSURE CRITICAL FIELDS (~20 fields for MVP)
# =============================================================================

DISCLOSURE_CRITICAL_FIELDS: Dict[str, List[Dict[str, str]]] = {
    "borrower": [
        {"id": "4000", "name": "Borrower First Name"},
        {"id": "4002", "name": "Borrower Last Name"},
        {"id": "65", "name": "Borrower SSN"},
        {"id": "1402", "name": "Borrower Email"},
    ],
    "property": [
        {"id": "11", "name": "Property Street Address"},
        {"id": "12", "name": "Property City"},
        {"id": "14", "name": "Property State"},
        {"id": "15", "name": "Property Zip"},
    ],
    "loan": [
        {"id": "1109", "name": "Loan Amount"},
        {"id": "3", "name": "Interest Rate"},
        {"id": "4", "name": "Loan Term"},
        {"id": "1172", "name": "Loan Type"},
        {"id": "19", "name": "Loan Purpose"},
        {"id": "353", "name": "LTV"},
    ],
    "property_value": [
        {"id": "356", "name": "Appraised Value"},
        {"id": "136", "name": "Purchase Price"},
    ],
    "contacts": [
        {"id": "VEND.X263", "name": "Settlement Agent Name"},
        {"id": "411", "name": "Title Company Name"},
    ],
    "dates": [
        {"id": "CD1.X1", "name": "CD Date Issued"},
        {"id": "748", "name": "Estimated Closing Date"},
    ],
}


def get_all_critical_field_ids() -> List[str]:
    """Get all critical field IDs as a flat list."""
    field_ids = []
    for category_fields in DISCLOSURE_CRITICAL_FIELDS.values():
        for field in category_fields:
            field_ids.append(field["id"])
    return field_ids


def get_field_name(field_id: str) -> str:
    """Get the name of a critical field by ID."""
    for category_fields in DISCLOSURE_CRITICAL_FIELDS.values():
        for field in category_fields:
            if field["id"] == field_id:
                return field["name"]
    return field_id


# =============================================================================
# CRITICAL MISMATCH FIELDS - Per SOP: Missing these is a critical issue
# =============================================================================

CRITICAL_MISMATCH_FIELDS = {
    "borrower_phone": "66",       # Home Phone - Critical if missing
    "borrower_email": "1240",     # Email Address - Critical if missing
}

# Backwards-compatible alias
HARD_STOP_FIELDS = CRITICAL_MISMATCH_FIELDS


# =============================================================================
# FORM FIELD DEFINITIONS
# =============================================================================

CRITICAL_FIELDS = {
    "borrower_first_name": "4000",
    "borrower_last_name": "4002",
    "property_address": "11",
    "property_city": "12",
    "property_state": "14",
    "property_zip": "15",
    "loan_amount": "1109",
    "loan_purpose": "19",
    "application_date": "745",
    "lo_nmls_id": "3238",
}


# =============================================================================
# ATR/QM FIELDS
# =============================================================================

class ATRQMFields:
    """Encompass field IDs for ATR/QM Management."""
    
    LOAN_FEATURES_FLAG = "ATRQM.X1"
    POINTS_FEES_FLAG = "ATRQM.X2"
    PRICE_LIMIT_FLAG = "ATRQM.X3"
    ATR_QM_ELIGIBILITY = "ATRQM.X4"
    POINTS_FEES_LIMIT = "ATRQM.X10"
    POINTS_FEES_ACTUAL = "ATRQM.X11"
    POINTS_FEES_TEST_RESULT = "ATRQM.X12"
    QM_TYPE = "ATRQM.X20"


# =============================================================================
# TRID DATE FIELDS
# =============================================================================

TRID_FIELDS = {
    "application_date": "745",
    "le_date_issued": "LE1.X1",
    "le_sent_date": "3152",
    "lock_date": "761",
    "lock_expiration": "762",
    "rate_locked": "2400",
}


# =============================================================================
# FLAG STATUS VALUES
# =============================================================================

class FlagStatus:
    """Possible flag status values."""
    
    GREEN = "GREEN"
    YELLOW = "YELLOW"
    RED = "RED"
    UNKNOWN = "UNKNOWN"
    
    @classmethod
    def parse(cls, value) -> str:
        """Parse a flag value to status."""
        if value is None:
            return cls.UNKNOWN
        
        value_str = str(value).upper().strip()
        
        if value_str in ["GREEN", "PASS", "OK", "Y", "YES"]:
            return cls.GREEN
        elif value_str in ["YELLOW", "CAUTION", "WARN"]:
            return cls.YELLOW
        elif value_str in ["RED", "FAIL", "NO", "N"]:
            return cls.RED
        
        return cls.UNKNOWN


# =============================================================================
# STATUS CONSTANTS
# =============================================================================

VALID_STATUSES_FOR_DISCLOSURE = [
    "Active",
    "Application",
    "Processing",
    "Submitted",
    "Loan Submitted",
]

CRITICAL_STATUSES = [
    "Loan Originated",
    "Funded", 
    "Closed",
    "Denied",
    "Withdrawn",
    "Suspended",
    "Cancelled",
]

# G8: Minimum days for closing date
MINIMUM_CLOSING_DAYS = 15

