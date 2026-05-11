#!/usr/bin/env python3
"""
EfolderConnect Direct Process Client

A reusable client for extracting document fields from loan efolders.
Handles caching, automatic retries, and polling for async processing.

Usage:
    from efolder_client import EfolderClient, ExtractionRequest
    
    # client_id is derived from ENCOMPASS_ENV (AWM-prod or AWM-test)
    request = ExtractionRequest(
        loan_number="2512953182",
        document_types=["Credit Report", "1003 URLA", "Title Report"],
    )
    
    client = EfolderClient()
    result = client.extract_documents(request)
    
    if result.all_completed:
        for doc in result.documents:
            print(f"{doc['doc_type']}: {doc['extracted_fields']}")
"""

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError


# =============================================================================
# CONFIGURATION - Load from environment variables
# =============================================================================

API_BASE_URL = os.getenv("EFOLDER_API_BASE_URL", "https://1doxzxvey2.execute-api.us-west-1.amazonaws.com/prod")
API_TOKEN = os.getenv("EFOLDER_API_TOKEN", "esfuse-token")  # Load from env, fallback to default

# Path to conditions config — resolved relative to this file so it works
# whether efolder_client is imported from output/ or shared/.
_CONDITIONS_PATH = Path(__file__).parent.parent / "output" / "config" / "required_docs_conditions.json"


def get_document_types_for_loan(
    loan_type: Optional[str] = None,
    loan_purpose: Optional[str] = None,
) -> List[str]:
    """
    Return the document list for a given loan type and purpose, loaded from
    required_docs_conditions.json.

    Matching rules (mirrors the original extract_sequential.select_condition logic):
    - 'Refinance' and 'Cash-Out Refinance' are treated as equivalent.
    - Falls through to the `fallback: true` entry if no specific match is found.
    - Currently we have a single unified fallback, so all loan types get the
      same 22-document list. When loan-type-specific rows are added to the
      conditions file in the future, they will be picked up automatically here.

    Args:
        loan_type:    e.g. "Conventional", "FHA", "VA", "USDA" (case-insensitive)
        loan_purpose: e.g. "Purchase", "Refinance", "Cash-Out Refinance"

    Returns:
        List of document type strings to pass to ExtractionRequest.
    """
    try:
        with open(_CONDITIONS_PATH) as f:
            cfg = json.load(f)
    except FileNotFoundError:
        # Graceful fallback — return hardcoded list if config file is missing
        return list(ALL_DOCUMENT_TYPES)

    conditions = cfg.get("conditions", [])

    _lt = (loan_type or "").strip().lower()
    _lp = (loan_purpose or "").strip().lower()
    # Treat both refi flavours as equivalent
    if _lp in ("cash-out refinance", "cash out refinance"):
        _lp = "refinance"

    fallback_entry = None
    for entry in conditions:
        cond = entry.get("condition", {})

        if cond.get("fallback"):
            fallback_entry = entry
            continue

        cond_lt = (cond.get("loan_type") or "").strip().lower()
        cond_lp = (cond.get("loan_purpose") or "").strip().lower()
        if cond_lp in ("cash-out refinance", "cash out refinance"):
            cond_lp = "refinance"

        if cond_lt == _lt and cond_lp == _lp:
            return entry.get("document_list", [])

    # No specific match — use fallback
    if fallback_entry:
        return fallback_entry.get("document_list", [])

    return []


def get_extraction_mode_for_loan(
    loan_type: Optional[str] = None,
    loan_purpose: Optional[str] = None,
) -> Dict[str, str]:
    """
    Return the per-document extraction_mode overrides for a given loan type/purpose.
    Keys are document type strings; values are "all" or "best".
    Documents not listed here default to "best".
    """
    try:
        with open(_CONDITIONS_PATH) as f:
            cfg = json.load(f)
    except FileNotFoundError:
        return {}

    conditions = cfg.get("conditions", [])

    _lt = (loan_type or "").strip().lower()
    _lp = (loan_purpose or "").strip().lower()
    if _lp in ("cash-out refinance", "cash out refinance"):
        _lp = "refinance"

    fallback_mode = {}
    for entry in conditions:
        cond = entry.get("condition", {})

        if cond.get("fallback"):
            fallback_mode = {
                k: v for k, v in entry.get("extraction_mode", {}).items()
                if not k.startswith("_")
            }
            continue

        cond_lt = (cond.get("loan_type") or "").strip().lower()
        cond_lp = (cond.get("loan_purpose") or "").strip().lower()
        if cond_lp in ("cash-out refinance", "cash out refinance"):
            cond_lp = "refinance"

        if cond_lt == _lt and cond_lp == _lp:
            return {
                k: v for k, v in entry.get("extraction_mode", {}).items()
                if not k.startswith("_")
            }

    return fallback_mode


# Convenience constant — the full unified list (matches fallback condition).
# Use get_document_types_for_loan() in production code for future-proofing.
ALL_DOCUMENT_TYPES: List[str] = get_document_types_for_loan()


# =============================================================================
# DATA CLASSES
# =============================================================================

def _get_default_client_id() -> str:
    """Get default client_id based on ENCOMPASS_ENV environment variable."""
    env = os.getenv("ENCOMPASS_ENV", "TEST").upper()
    return "AWM-prod" if env == "PROD" else "AWM-test"


@dataclass
class ExtractionRequest:
    """
    Request configuration for document extraction.
    
    Attributes:
        loan_number: Encompass loan number (get this from state.loan_number)
        client_id: Client identifier - derived from ENCOMPASS_ENV (AWM-prod or AWM-test)
        document_types: List of document types to extract (get this from state.required_documents)
        environment: Encompass environment - "prod" or "test" (derived from client_id)
        selection_mode: "All" (get ALL attachments in folder) or "Best" (best match only)
        use_cache: Whether to check DynamoDB cache first (default: True)
        max_retries: Maximum number of polling retries (default: 3)
        retry_interval_seconds: Seconds to wait between retries (default: 30 = 30 seconds)
    """
    # === GET THESE FROM STATE ===
    loan_number: str                          # state.loan_number or state.loan_id_number
    client_id: Optional[str] = None           # Derived from ENCOMPASS_ENV if not set
    document_types: List[str] = field(default_factory=list)  # REQUIRED — always set from config, never hardcoded
    
    # === OPTIONAL CONFIGURATION ===
    environment: Optional[str] = None          # Auto-derived from client_id if not set
    selection_mode: str = "All"                # "All" = ALL attachments in folder, "Best" = best match only
    use_cache: bool = True
    max_retries: int = 0                       # 0 = don't wait for pending, just return cached + flag pending
    retry_interval_seconds: int = 30          # 30 seconds (only used if max_retries > 0)
    override_not_found: bool = True           # If True, retry lookup for docs previously marked "not_found"
    
    def __post_init__(self):
        """Derive client_id and environment from ENCOMPASS_ENV if not explicitly set."""
        # Set client_id from environment if not provided
        if self.client_id is None:
            self.client_id = _get_default_client_id()
        
        # Derive environment from client_id
        if self.environment is None:
            if "-prod" in self.client_id.lower():
                self.environment = "prod"
            elif "-test" in self.client_id.lower():
                self.environment = "test"
            else:
                self.environment = "test"  # Default to TEST


@dataclass
class ExtractionResult:
    """
    Result of document extraction.
    
    Attributes:
        success: Whether the overall operation succeeded
        all_completed: Whether all requested documents are completed (no pending)
        documents: List of document results with extracted fields
        pending: List of document types still pending
        failed: List of document types that failed
        summary: Summary statistics
        loan_id: Encompass loan GUID
        loan_number: Encompass loan number
    """
    success: bool
    all_completed: bool
    documents: List[Dict[str, Any]]
    pending: List[str]
    failed: List[str]
    summary: Dict[str, Any]
    loan_id: str = ""
    loan_number: str = ""
    raw_response: Optional[Dict[str, Any]] = None


# =============================================================================
# EFOLDER CLIENT
# =============================================================================

class EfolderClient:
    """
    Client for interacting with the EfolderConnect Direct Process API.
    
    Handles:
    - Sending extraction requests
    - Automatic polling for async processing
    - Retry logic with configurable intervals
    - Result aggregation
    """
    
    def __init__(
        self,
        api_url: str = API_BASE_URL,
        token: str = API_TOKEN,
        timeout: int = 60,
    ):
        """
        Initialize the client.
        
        Args:
            api_url: Base URL for the EfolderConnect API
            token: Authorization token
            timeout: HTTP request timeout in seconds
        """
        self.api_url = api_url.rstrip('/')
        self.token = token
        self.timeout = timeout
    
    def extract_documents(self, request: ExtractionRequest) -> ExtractionResult:
        """
        Extract document fields from a loan's efolder.
        
        This method:
        1. Sends POST /efolder/direct with useCache=True
        2. If status 200 (all cached), returns immediately
        3. If status 202 (some pending), waits then uses GET /efolder to check
        4. Retries up to max_retries times with retry_interval_seconds between attempts
        5. Returns final result with all completed documents and any pending/failed
        
        Args:
            request: ExtractionRequest configuration
            
        Returns:
            ExtractionResult with documents and their extracted fields
        """
        all_documents = {}
        pending_types = set(request.document_types)
        failed_types = set()
        loan_id = ""
        loan_number = request.loan_number
        
        # Step 1: Initial POST to trigger extraction (if needed) and get cached results
        response = self._call_api(
            loan_number=request.loan_number,
            client_id=request.client_id,
            document_types=list(pending_types),
            environment=request.environment,
            selection_mode=request.selection_mode,
            use_cache=request.use_cache,
            override_not_found=request.override_not_found,
        )
        
        if not response.get('success'):
            # API call failed
            return ExtractionResult(
                success=False,
                all_completed=False,
                documents=[],
                pending=list(pending_types),
                failed=[],
                summary={'error': response.get('error', 'API call failed')},
                raw_response=response,
            )
        
        body = response.get('body', {})
        status = response.get('status', 0)
        loan_id = body.get('loanId', '')
        
        # Process initial response
        self._process_documents(body.get('documents', []), all_documents, pending_types, failed_types)
        
        # If status 200 (all cached), we're done immediately
        if status == 200 or not pending_types:
            print(f"✅ All {len(all_documents)} documents retrieved from cache")
            return self._build_result(request, all_documents, pending_types, failed_types, loan_id, loan_number)
        
        # Status 202: some documents are processing in background
        # Wait and use GET /efolder to poll for results
        print(f"⏳ {len(all_documents)} cached, {len(pending_types)} processing in background...")
        print(f"   📄 Processing: {', '.join(sorted(pending_types))}")
        
        for attempt in range(request.max_retries):
            print(f"   Waiting {request.retry_interval_seconds} seconds before checking... (attempt {attempt + 1}/{request.max_retries})")
            time.sleep(request.retry_interval_seconds)
            
            # Use GET /efolder to check cached results (faster than POST)
            get_response = self.get_documents(loan_number, include_fields=True)
            
            if get_response.get('error'):
                print(f"   ⚠️ GET failed: {get_response.get('error')}")
                continue
            
            # Check which documents are now complete
            for doc in get_response.get('documents', []):
                doc_type = doc.get('DocType', '')
                doc_status = doc.get('Status', '')
                
                if doc_type not in pending_types:
                    continue  # Already have this one
                
                if doc_status in ['completed', 'stored_no_extraction']:
                    # Convert GET format to POST format for consistency
                    all_documents[doc_type] = {
                        'doc_type': doc_type,
                        'status': 'success' if doc_status == 'completed' else doc_status,
                        'source': 'cache',
                        'extracted_fields': doc.get('ExtractedFields', {}),
                        'extracted_fields_count': doc.get('ExtractedFieldsCount', 0),
                        'document_title': doc.get('DocumentTitle', ''),
                        'attachment_id': doc.get('AttachmentID', ''),
                        'file_size': doc.get('FileSizeBytes', 0),
                        'docrepo_location': doc.get('DocRepoLocation', doc.get('docrepo_location', '')),
                        'docrepo_bucket': doc.get('DocRepoBucket', doc.get('S3Bucket', doc.get('docrepo_bucket', ''))),
                        'docrepo_client_id': doc.get('DocRepoClientId', doc.get('ClientId', doc.get('docrepo_client_id', ''))),
                        's3_key': doc.get('S3Key', doc.get('s3_key', '')),
                    }
                    pending_types.discard(doc_type)
                    
                elif doc_status == 'not_found':
                    all_documents[doc_type] = {
                        'doc_type': doc_type,
                        'status': 'not_found',
                        'source': 'cache',
                        'extracted_fields': {},
                        'error': doc.get('FailureReason', 'Document not found'),
                    }
                    pending_types.discard(doc_type)
                    failed_types.add(doc_type)
                    
                elif doc_status in ['error-dl', 'error-ext', 'error-sch', 'failed']:
                    all_documents[doc_type] = {
                        'doc_type': doc_type,
                        'status': 'failed',
                        'source': 'cache',
                        'extracted_fields': {},
                        'error': doc.get('FailureReason', 'Extraction failed'),
                    }
                    pending_types.discard(doc_type)
                    failed_types.add(doc_type)
            
            # Check if all done
            if not pending_types:
                print(f"✅ All documents complete after {attempt + 1} poll(s)")
                break
            else:
                print(f"   Still waiting for: {list(pending_types)}")

        if pending_types:
            print(f"⚠️ {len(pending_types)} doc(s) still pending after {request.max_retries} polls: {sorted(pending_types)}")
            for doc_type in list(pending_types):
                all_documents[doc_type] = {
                    'doc_type': doc_type,
                    'status': 'pending',
                    'source': 'timeout',
                    'extracted_fields': {},
                }

        return self._build_result(request, all_documents, pending_types, failed_types, loan_id, loan_number)
    
    def _process_documents(
        self,
        documents: List[Dict[str, Any]],
        all_documents: Dict[str, Dict[str, Any]],
        pending_types: set,
        failed_types: set,
    ) -> None:
        """Process documents from API response."""
        for doc in documents:
            doc_type = doc.get('doc_type', '')
            doc_status = doc.get('status', '')
            
            if doc_status in ['success', 'completed', 'stored_no_extraction']:
                all_documents[doc_type] = doc
                pending_types.discard(doc_type)
                
            elif doc_status == 'not_found':
                all_documents[doc_type] = doc
                pending_types.discard(doc_type)
                failed_types.add(doc_type)
                
            elif doc_status == 'failed':
                failed_types.add(doc_type)
                pending_types.discard(doc_type)
                all_documents[doc_type] = doc
    
    def _build_result(
        self,
        request: ExtractionRequest,
        all_documents: Dict[str, Dict[str, Any]],
        pending_types: set,
        failed_types: set,
        loan_id: str,
        loan_number: str,
    ) -> ExtractionResult:
        """Build final ExtractionResult."""
        return ExtractionResult(
            success=True,
            all_completed=len(pending_types) == 0,
            documents=list(all_documents.values()),
            pending=list(pending_types),
            failed=list(failed_types),
            summary={
                'total_requested': len(request.document_types),
                'completed': len([d for d in all_documents.values() if d.get('status') in ['success', 'completed', 'stored_no_extraction']]),
                'not_found': len([d for d in all_documents.values() if d.get('status') == 'not_found']),
                'pending': len(pending_types),
                'failed': len(failed_types),
            },
            loan_id=loan_id,
            loan_number=loan_number,
        )
    
    def _call_api(
        self,
        loan_number: str,
        client_id: str,
        document_types: List[str],
        environment: str,
        selection_mode: str,
        use_cache: bool,
        override_not_found: bool = False,
    ) -> Dict[str, Any]:
        """
        Call the EfolderConnect Direct Process API.
        
        Args:
            loan_number: Loan number to process
            client_id: Client identifier
            document_types: List of document types to extract
            environment: Encompass environment (prod/test)
            selection_mode: "All" (all attachments) or "Best" (best match only)
            use_cache: Whether to use DynamoDB cache
            override_not_found: If True, retry lookup for docs previously marked "not_found"
            
        Returns:
            Dict with status, body, success flag
        """
        url = f"{self.api_url}/efolder/direct"
        
        payload = {
            'clientId': client_id,
            'environment': environment,
            'selectionMode': selection_mode,
            'useCache': use_cache,
            'useLlm': True,
            'loanNumber': loan_number,
            'documentTypes': document_types,
            'overrideNotFound': override_not_found,
        }
        
        headers = {
            'Authorization': f'Bearer {self.token}',
            'Content-Type': 'application/json',
        }
        
        # Log request details for debugging
        print(f"[EFOLDER_API] POST {url}")
        print(f"[EFOLDER_API] clientId={client_id}, env={environment}, loanNumber={loan_number}, docs={len(document_types)}")
        
        try:
            req = Request(
                url,
                data=json.dumps(payload).encode('utf-8'),
                headers=headers,
                method='POST',
            )
            
            with urlopen(req, timeout=self.timeout) as response:
                response_body = response.read().decode('utf-8')
                return {
                    'status': response.status,
                    'body': json.loads(response_body) if response_body else {},
                    'success': True,
                }
                
        except HTTPError as e:
            error_body = e.read().decode('utf-8') if e.fp else ''
            parsed_body = json.loads(error_body) if error_body else {}
            
            # Enhanced error logging for debugging
            print(f"[EFOLDER_API] ❌ HTTP Error {e.code}: {e.reason}")
            if parsed_body.get('message'):
                print(f"[EFOLDER_API] Message: {parsed_body.get('message')}")
            if e.code == 404:
                print(f"[EFOLDER_API] 404 typically means loan not found in {environment} environment")
                print(f"[EFOLDER_API] Check if loanNumber={loan_number} exists in {environment.upper()} Encompass")
            
            return {
                'status': e.code,
                'body': parsed_body,
                'success': False,
                'error': str(e),
            }
        except URLError as e:
            return {
                'status': 0,
                'body': {},
                'success': False,
                'error': str(e),
            }
        except Exception as e:
            return {
                'status': 0,
                'body': {},
                'success': False,
                'error': str(e),
            }
    
    def get_documents(self, loan_number: str, include_fields: bool = True) -> Dict[str, Any]:
        """
        Query cached documents for a loan from DynamoDB.
        
        Args:
            loan_number: Loan number to query
            include_fields: Whether to include ExtractedFields in response
            
        Returns:
            Dict with documents and metadata
        """
        url = f"{self.api_url}/efolder?loanNumber={loan_number}"
        if include_fields:
            url += "&includeFields=true"
        
        headers = {
            'Authorization': f'Bearer {self.token}',
            'Content-Type': 'application/json',
        }
        
        try:
            req = Request(url, headers=headers, method='GET')
            
            with urlopen(req, timeout=self.timeout) as response:
                response_body = response.read().decode('utf-8')
                return json.loads(response_body) if response_body else {}
                
        except Exception as e:
            return {'error': str(e)}


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def flag_pending_documents(pending: List[str], failed: List[str]) -> None:
    """
    Placeholder for flagging pending/failed documents.
    
    TODO: Implement your flagging logic here. This could:
    - Log to a monitoring system
    - Send alerts
    - Update state with issues
    - Create tickets
    
    Args:
        pending: List of document types still pending
        failed: List of document types that failed
    """
    if pending:
        print(f"⚠️  PENDING DOCUMENTS ({len(pending)}): {pending}")
        # TODO: Add your flagging logic for pending documents
        pass
    
    if failed:
        print(f"❌ FAILED DOCUMENTS ({len(failed)}): {failed}")
        # TODO: Add your flagging logic for failed documents
        pass


def extract_field_values(documents: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """
    Extract field values from documents into a simple dict structure.
    
    Args:
        documents: List of document results from ExtractionResult.documents
        
    Returns:
        Dict mapping doc_type -> extracted_fields
    """
    result = {}
    for doc in documents:
        doc_type = doc.get('doc_type', '')
        if doc_type:
            result[doc_type] = doc.get('extracted_fields', {})
    return result


# =============================================================================
# EXAMPLE USAGE
# =============================================================================

if __name__ == '__main__':
    # Example: Create extraction request from state
    # In your agent, you would get these values from state
    
    request = ExtractionRequest(
        # === GET THESE FROM STATE ===
        loan_number="2512953182",              # state.loan_number
        # client_id derived from ENCOMPASS_ENV (AWM-prod or AWM-test)
        document_types=ALL_DOCUMENT_TYPES,      # Use ALL_DOCUMENT_TYPES constant or state.required_documents
        
        # === OPTIONAL CONFIGURATION ===
        use_cache=True,
        max_retries=3,
        retry_interval_seconds=30,  # 30 seconds
    )
    
    # Create client and extract documents
    client = EfolderClient()
    
    print("=" * 70)
    print("📄 EFOLDER DOCUMENT EXTRACTION")
    print("=" * 70)
    print(f"Loan Number: {request.loan_number}")
    print(f"Client: {request.client_id}")
    print(f"Environment: {request.environment}")
    print(f"Document Types: {len(request.document_types)}")
    print(f"Max Retries: {request.max_retries}")
    print(f"Retry Interval: {request.retry_interval_seconds}s")
    print("=" * 70)
    
    # Extract documents with automatic retry/polling
    result = client.extract_documents(request)
    
    print("\n" + "=" * 70)
    print("📊 EXTRACTION RESULTS")
    print("=" * 70)
    print(f"Success: {result.success}")
    print(f"All Completed: {result.all_completed}")
    print(f"Loan ID: {result.loan_id}")
    print(f"Loan Number: {result.loan_number}")
    print(f"\nSummary: {result.summary}")
    
    # Show completed documents
    completed_docs = [d for d in result.documents if d.get('status') in ['success', 'completed', 'stored_no_extraction']]
    if completed_docs:
        print(f"\n✅ COMPLETED DOCUMENTS ({len(completed_docs)}):")
        for doc in completed_docs:
            fields_count = doc.get('extracted_fields_count', len(doc.get('extracted_fields', {})))
            print(f"   • {doc.get('doc_type')}: {fields_count} fields ({doc.get('source', 'N/A')})")
    
    # Show pending/failed
    if result.pending:
        print(f"\n⏳ PENDING ({len(result.pending)}): {result.pending}")
    
    if result.failed:
        print(f"\n❌ FAILED ({len(result.failed)}): {result.failed}")
    
    # Flag any issues
    if result.pending or result.failed:
        print("\n" + "-" * 70)
        flag_pending_documents(result.pending, result.failed)
    
    # Example: Get field values as simple dict
    print("\n" + "=" * 70)
    print("📋 EXTRACTED FIELD VALUES (sample)")
    print("=" * 70)
    field_values = extract_field_values(result.documents)
    for doc_type, fields in list(field_values.items())[:3]:
        non_empty = {k: v for k, v in fields.items() if v not in [None, '', [], {}]}
        print(f"\n{doc_type}:")
        for k, v in list(non_empty.items())[:5]:
            val_str = str(v)[:60].replace('\n', ' ')
            print(f"   • {k}: {val_str}")
        if len(non_empty) > 5:
            print(f"   ... and {len(non_empty) - 5} more")
