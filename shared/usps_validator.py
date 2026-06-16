"""USPS Address Validation API v3 Integration.

Full implementation of USPS Address API v3.
Provides address standardization, validation, and deliverability checks.

API Documentation: https://www.usps.com/business/web-tools-apis/
OAuth2 Authentication: Client Credentials flow

Environment Variables Required (any of these pairs):
- USPS_KEY / USPS_SECRET (preferred)
- USPS_CLIENT_ID / USPS_CLIENT_SECRET (alternate)
"""

import os
import logging
from typing import Dict, List, Optional, Union
from dataclasses import dataclass
from datetime import datetime, timedelta
import requests

logger = logging.getLogger(__name__)


@dataclass
class USPSAddressResult:
    """Result from USPS address validation."""
    success: bool
    error: Optional[str] = None
    standardized_address: Optional[Dict[str, str]] = None
    delivery_point: Optional[str] = None
    carrier_route: Optional[str] = None
    dpv_confirmation: Optional[str] = None  # Y=confirmed, N=not confirmed, D=missing secondary
    dpv_cmra: Optional[str] = None  # Y=CMRA (mail drop), N=not CMRA
    business: Optional[str] = None  # Y=business, N=residential
    central_delivery_point: Optional[str] = None
    vacant: Optional[str] = None
    warnings: Optional[List[str]] = None


class USPSAddressValidator:
    """USPS Address Validation API v3 client.
    
    Handles OAuth2 authentication and address validation requests.
    Gracefully handles missing credentials with clear error logging.
    """
    
    # USPS API URLs (note: 'apis' not 'api')
    BASE_URL = "https://apis.usps.com"
    TOKEN_URL = f"{BASE_URL}/oauth2/v3/token"
    ADDRESS_URL = f"{BASE_URL}/addresses/v3/address"
    CITY_STATE_URL = f"{BASE_URL}/addresses/v3/city-state"
    ZIPCODE_URL = f"{BASE_URL}/addresses/v3/zipcode"
    
    def __init__(self, client_id: Optional[str] = None, client_secret: Optional[str] = None):
        """Initialize USPS validator.
        
        Args:
            client_id: USPS OAuth2 client ID (or from env USPS_KEY/USPS_CLIENT_ID)
            client_secret: USPS OAuth2 client secret (or from env USPS_SECRET/USPS_CLIENT_SECRET)
        """
        # Support multiple env var names for flexibility
        self.client_id = (
            client_id or 
            os.getenv("USPS_KEY") or 
            os.getenv("USPS_CLIENT_ID")
        )
        self.client_secret = (
            client_secret or 
            os.getenv("USPS_SECRET") or 
            os.getenv("UPSP_SECRET") or  # Handle common typo
            os.getenv("USPS_CLIENT_SECRET")
        )
        
        if not self.client_id or not self.client_secret:
            logger.warning("[USPS] Client credentials not set - USPS validation disabled")
            logger.warning("[USPS] Set USPS_KEY and USPS_SECRET environment variables")
            self.enabled = False
        else:
            self.enabled = True
            logger.info("[USPS] USPS validator initialized with credentials")
        
        self._access_token = None
        self._token_expires_at = None
    
    def _get_access_token(self) -> Optional[str]:
        """Get OAuth2 access token (cached).
        
        Returns:
            Access token or None if credentials not set
        """
        if not self.enabled:
            logger.error("[USPS] Cannot get token - credentials not configured")
            return None
        
        # Return cached token if still valid
        if self._access_token and self._token_expires_at:
            if datetime.now() < self._token_expires_at:
                return self._access_token
        
        # Get new token
        try:
            logger.info("[USPS] Requesting new OAuth2 access token...")
            
            # USPS OAuth2 token request per spec (client_id/secret in body, not Basic Auth)
            response = requests.post(
                self.TOKEN_URL,
                data={
                    "grant_type": "client_credentials",
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                },
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                timeout=10,
            )
            
            response.raise_for_status()
            token_data = response.json()
            
            self._access_token = token_data.get("access_token")
            expires_in = token_data.get("expires_in", 3600)  # Default 1 hour
            self._token_expires_at = datetime.now() + timedelta(seconds=expires_in - 60)  # 60s buffer
            
            logger.info(f"[USPS] Access token obtained, expires in {expires_in}s")
            return self._access_token
            
        except Exception as e:
            logger.error(f"[USPS] Failed to get access token: {e}")
            return None
    
    def validate_address(
        self,
        street_address: str,
        city: Optional[str] = None,
        state: Optional[str] = None,
        zip_code: Optional[str] = None,
        secondary_address: Optional[str] = None,
        firm: Optional[str] = None,
        urbanization: Optional[str] = None,
        zip_plus4: Optional[str] = None,
    ) -> USPSAddressResult:
        """Validate and standardize an address.
        
        Args:
            street_address: Street address (required)
            city: City name
            state: 2-letter state code
            zip_code: 5-digit ZIP code
            secondary_address: Apartment, suite, etc.
            firm: Firm or company name
            urbanization: Puerto Rico urbanization
            zip_plus4: ZIP+4 code
            
        Returns:
            USPSAddressResult with validation details
        """
        if not self.enabled:
            logger.warning("[USPS] Returning placeholder result - credentials not configured")
            return USPSAddressResult(
                success=True,
                standardized_address={
                    "street": street_address.upper() if street_address else "",
                    "city": city.upper() if city else "",
                    "state": state.upper() if state else "",
                    "zip": zip_code or "",
                },
                error=None,
                warnings=["USPS API disabled - using placeholder validation"],
            )
        
        # Get access token
        token = self._get_access_token()
        if not token:
            return USPSAddressResult(
                success=False,
                error="Failed to obtain USPS access token"
            )
        
        # Build request payload
        payload = {
            "streetAddress": street_address,
        }
        
        if city:
            payload["city"] = city
        if state:
            payload["state"] = state
        if zip_code:
            payload["ZIPCode"] = zip_code
        if secondary_address:
            payload["secondaryAddress"] = secondary_address
        if firm:
            payload["firm"] = firm
        if urbanization:
            payload["urbanization"] = urbanization
        if zip_plus4:
            payload["ZIPPlus4"] = zip_plus4
        
        try:
            logger.info(f"[USPS] Validating address: {street_address}, {city}, {state} {zip_code}")
            
            # USPS Address API v3 uses GET with query parameters (not POST)
            response = requests.get(
                self.ADDRESS_URL,
                params=payload,  # Query parameters for GET request
                headers={
                    "Authorization": f"Bearer {token}",
                },
                timeout=10,
            )
            
            response.raise_for_status()
            data = response.json()
            
            # Parse response. USPS Addresses v3 splits the payload into two objects:
            #   data["address"]        — standardized street/city/state/ZIP
            #   data["additionalInfo"] — DPVConfirmation, deliveryPoint, carrierRoute,
            #                            vacant, DPVCMRA, business, centralDeliveryPoint
            # DPV (and the other delivery flags) MUST be read from additionalInfo, not address.
            address_data = data.get("address", {})
            additional_info = data.get("additionalInfo", {})
            
            standardized = {
                "street": address_data.get("streetAddress"),
                "secondary": address_data.get("secondaryAddress"),
                "city": address_data.get("city"),
                "state": address_data.get("state"),
                "zip": address_data.get("ZIPCode"),
                "zip_plus4": address_data.get("ZIPPlus4"),
            }
            
            # Remove None values
            standardized = {k: v for k, v in standardized.items() if v is not None}
            
            warnings = []
            
            # Check DPV confirmation (from additionalInfo)
            dpv = additional_info.get("DPVConfirmation")
            if dpv == "N":
                warnings.append("Address not confirmed by USPS")
            elif dpv == "D":
                warnings.append("Address missing secondary information (apt/suite)")
            
            # Check if vacant
            if additional_info.get("vacant") == "Y":
                warnings.append("Property is marked as vacant")
            
            # Check if CMRA (mail drop)
            if additional_info.get("DPVCMRA") == "Y":
                warnings.append("Address is a Commercial Mail Receiving Agency (CMRA)")
            
            logger.info(f"[USPS] Address validated - DPV: {dpv}")
            
            return USPSAddressResult(
                success=True,
                standardized_address=standardized,
                delivery_point=additional_info.get("deliveryPoint"),
                carrier_route=additional_info.get("carrierRoute"),
                dpv_confirmation=dpv,
                dpv_cmra=additional_info.get("DPVCMRA"),
                business=additional_info.get("business"),
                central_delivery_point=additional_info.get("centralDeliveryPoint"),
                vacant=additional_info.get("vacant"),
                warnings=warnings if warnings else None,
            )
            
        except requests.exceptions.HTTPError as e:
            error_msg = f"USPS API HTTP error: {e}"
            if e.response is not None:
                try:
                    error_data = e.response.json()
                    error_msg = f"USPS API error: {error_data.get('error', {}).get('message', str(e))}"
                except:
                    pass
            logger.error(f"[USPS] {error_msg}")
            return USPSAddressResult(success=False, error=error_msg)
            
        except Exception as e:
            logger.error(f"[USPS] Validation error: {e}")
            return USPSAddressResult(success=False, error=str(e))
    
    def lookup_city_state(self, zip_code: str) -> USPSAddressResult:
        """Look up city and state from ZIP code.
        
        Args:
            zip_code: 5-digit ZIP code
            
        Returns:
            USPSAddressResult with city/state info
        """
        if not self.enabled:
            logger.warning("[USPS] City/state lookup disabled - credentials not configured")
            return USPSAddressResult(
                success=False,
                error="USPS credentials not set"
            )
        
        token = self._get_access_token()
        if not token:
            return USPSAddressResult(
                success=False,
                error="Failed to obtain USPS access token"
            )
        
        try:
            response = requests.get(
                f"{self.CITY_STATE_URL}?ZIPCode={zip_code}",
                headers={
                    "Authorization": f"Bearer {token}",
                },
                timeout=10,
            )
            
            response.raise_for_status()
            data = response.json()
            
            return USPSAddressResult(
                success=True,
                standardized_address={
                    "city": data.get("city"),
                    "state": data.get("state"),
                    "zip": zip_code,
                }
            )
            
        except Exception as e:
            logger.error(f"[USPS] City/state lookup error: {e}")
            return USPSAddressResult(success=False, error=str(e))


# Singleton instance
_validator_instance = None


def get_usps_validator() -> USPSAddressValidator:
    """Get singleton USPS validator instance.
    
    Returns:
        USPSAddressValidator instance
    """
    global _validator_instance
    if _validator_instance is None:
        _validator_instance = USPSAddressValidator()
    return _validator_instance


def validate_address_sync(
    street_address: str,
    city: Optional[str] = None,
    state: Optional[str] = None,
    zip_code: Optional[str] = None,
    **kwargs
) -> USPSAddressResult:
    """Validate address (synchronous convenience function).
    
    Args:
        street_address: Street address (required)
        city: City name
        state: 2-letter state code
        zip_code: 5-digit ZIP code
        **kwargs: Additional optional parameters
        
    Returns:
        USPSAddressResult with validation details
    """
    validator = get_usps_validator()
    return validator.validate_address(
        street_address=street_address,
        city=city,
        state=state,
        zip_code=zip_code,
        **kwargs
    )

