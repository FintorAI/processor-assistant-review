"""Re-export shim. Canonical implementation lives in the fintor-usps package."""
from fintor_usps import (  # noqa: F401
    USPSAddressValidator,
    USPSAddressResult,
    get_usps_validator,
    validate_address_sync,
    validate_address,
)
