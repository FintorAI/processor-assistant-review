"""DocRepo S3 Storage Integration.

Helper functions for uploading documents and reports to DocRepo S3 storage,
which provides per-client S3 buckets for persistent storage and signed URLs
for UI access.

Supported file types:
- .json - application/json
- .md - text/markdown
- .pdf - application/pdf
- .jpg/.jpeg - image/jpeg
- .png - image/png
- .txt - text/plain

Usage:
    from shared.docrepo import upload_to_docrepo, get_docrepo_url

    # Upload a JSON report
    result = upload_to_docrepo(
        content=json.dumps(report),
        filename="step_1_report.json",
        client_id="docsOrchAgent"
    )
    
    # Upload a PDF document
    result = upload_to_docrepo(
        content=pdf_bytes,
        filename="loan_document.pdf",
        client_id="docsOrchAgent"
    )
    
    # Upload an image
    result = upload_to_docrepo(
        content=image_bytes,
        filename="screenshot.jpg",
        client_id="docsOrchAgent"
    )
    
    # Get signed URL
    url_result = get_docrepo_url(doc_id="step_1_report.json")
"""

import os
import base64
import logging
from pathlib import Path
from typing import Any, Dict, Optional, Union

import requests

logger = logging.getLogger(__name__)

# Default client ID for DocsOrch documents
DOCSORCH_CLIENT_ID = "docsOrchAgent"

# Content type mappings for file extensions
CONTENT_TYPE_MAP = {
    ".json": "application/json",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".pdf": "application/pdf",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".txt": "text/plain",
    ".csv": "text/csv",
    ".html": "text/html",
    ".xml": "application/xml",
}


def get_content_type(filename: str) -> str:
    """Get the MIME content type based on file extension.
    
    Args:
        filename: The filename with extension (e.g., "report.json")
        
    Returns:
        The MIME content type (e.g., "application/json")
    """
    ext = Path(filename).suffix.lower()
    return CONTENT_TYPE_MAP.get(ext, "application/octet-stream")


def get_file_extension(filename: str) -> str:
    """Get the file extension from a filename.
    
    Args:
        filename: The filename (e.g., "report.json")
        
    Returns:
        The extension with dot (e.g., ".json")
    """
    ext = Path(filename).suffix.lower()
    return ext if ext else ".bin"


def _sanitize_for_dynamodb(data: Any) -> Any:
    """Sanitize data for DynamoDB storage (convert floats to strings).
    
    DynamoDB doesn't support Python float types directly.
    This function recursively converts floats to strings to avoid errors.
    
    Args:
        data: Any Python data structure
        
    Returns:
        Sanitized data with floats converted to strings
    """
    if isinstance(data, float):
        # Convert float to string to avoid DynamoDB float error
        return str(data)
    elif isinstance(data, dict):
        return {k: _sanitize_for_dynamodb(v) for k, v in data.items()}
    elif isinstance(data, list):
        return [_sanitize_for_dynamodb(item) for item in data]
    else:
        return data


def _get_docrepo_config() -> dict[str, str]:
    """Get DocRepo configuration from environment."""
    return {
        "auth_token": os.getenv("DOCREPO_AUTH_TOKEN", "esfuse-token"),
        "put_api_base": os.getenv("DOCREPO_PUT_API_BASE", "https://ekrhupxp1d.execute-api.us-west-1.amazonaws.com/prod"),
        "get_api_base": os.getenv("DOCREPO_GET_API_BASE", "https://m49lxh6q5d.execute-api.us-west-1.amazonaws.com/prod"),
        "create_api_base": os.getenv("DOCREPO_CREATE_API_BASE", "https://dnobbdlzyb.execute-api.us-west-1.amazonaws.com/prod"),
    }


def create_docrepo_bucket(client_id: str = DOCSORCH_CLIENT_ID) -> dict[str, Any]:
    """Create an S3 bucket for a client in docRepo.
    
    This is idempotent - if the bucket already exists, it returns success.
    
    Args:
        client_id: The client identifier (default: discOrchAgent)
        
    Returns:
        Dictionary containing:
        - bucket_created: Boolean indicating if bucket was newly created
        - bucket_exists: Boolean indicating if bucket exists after call
        - client_id: The client ID used
        - bucket_name: The S3 bucket name
        - message: Status message
    """
    config = _get_docrepo_config()
    
    try:
        logger.info(f"[DOCREPO] Creating bucket for client: {client_id}")
        
        headers = {
            "Authorization": f"Bearer {config['auth_token']}",
            "Content-Type": "application/json"
        }
        
        response = requests.post(
            f"{config['create_api_base']}/create-bucket",
            json={"clientId": client_id},
            headers=headers,
            timeout=30
        )
        
        if response.status_code == 200:
            result = response.json()
            bucket_name = result.get("bucketName", "")
            was_created = result.get("created", False)
            
            if was_created:
                logger.info(f"[DOCREPO] Bucket created - {bucket_name}")
            else:
                logger.info(f"[DOCREPO] Bucket already exists - {bucket_name}")
            
            return {
                "bucket_created": was_created,
                "bucket_exists": True,
                "client_id": result.get("clientId", client_id),
                "bucket_name": bucket_name,
                "message": result.get("message", "Bucket ready")
            }
        else:
            logger.error(f"[DOCREPO] Bucket creation failed - Status {response.status_code}: {response.text}")
            return {
                "bucket_created": False,
                "bucket_exists": False,
                "client_id": client_id,
                "error": f"Failed with status {response.status_code}",
                "message": response.text
            }
            
    except Exception as e:
        logger.error(f"[DOCREPO] Exception creating bucket: {e}")
        return {
            "bucket_created": False,
            "bucket_exists": False,
            "client_id": client_id,
            "error": str(e),
            "message": f"Failed to create bucket: {str(e)}"
        }


def upload_to_docrepo(
    content: Union[bytes, str],
    filename: Optional[str] = None,
    doc_id: Optional[str] = None,
    data_object: Optional[Dict[str, Any]] = None,
    client_id: str = DOCSORCH_CLIENT_ID,
    content_type: Optional[str] = None,
    file_extension: Optional[str] = None,
) -> dict[str, Any]:
    """Upload content to docRepo S3 storage.
    
    This uploads documents to per-client S3 buckets for persistent storage
    and generates signed URLs for UI access. If the bucket doesn't exist,
    it will be created automatically.
    
    Supports automatic content type detection for:
    - .json - application/json
    - .md - text/markdown
    - .pdf - application/pdf
    - .jpg/.jpeg - image/jpeg
    - .png - image/png
    - .txt - text/plain
    
    Args:
        content: The content to upload (bytes or string)
        filename: The filename with extension (e.g., "report.json", "doc.pdf")
                  If provided, content_type and file_extension are auto-detected.
        doc_id: The document identifier (legacy, use filename instead)
        data_object: Optional structured metadata to store with the document
        client_id: The client identifier (default: docsOrchAgent)
        content_type: MIME type (auto-detected from filename if not provided)
        file_extension: File extension (auto-detected from filename if not provided)
        
    Returns:
        Dictionary containing:
        - success: Boolean indicating success
        - s3_uploaded: Boolean indicating success (legacy compatibility)
        - url: The signed URL for the uploaded document (if available)
        - client_id: The client ID used
        - doc_id: The document ID used
        - content_type: The MIME content type used
        - message: Status message
        - (error if failed)
    """
    config = _get_docrepo_config()
    
    if not config["auth_token"]:
        logger.warning("[DOCREPO] No auth token configured, skipping S3 upload")
        return {
            "success": False,
            "s3_uploaded": False,
            "message": "DocRepo auth token not configured in environment"
        }
    
    # Use filename as doc_id if doc_id not provided
    if filename and not doc_id:
        doc_id = filename
    elif not doc_id:
        doc_id = "unknown_document"
    
    # Auto-detect content type and file extension from filename
    if filename:
        if not content_type:
            content_type = get_content_type(filename)
        if not file_extension:
            file_extension = get_file_extension(filename)
    else:
        # Default fallbacks
        content_type = content_type or "application/octet-stream"
        file_extension = file_extension or ".bin"
    
    try:
        # Convert string to bytes if needed
        if isinstance(content, str):
            content_bytes = content.encode('utf-8')
        else:
            content_bytes = content
        
        # Encode content as base64
        content_base64 = base64.b64encode(content_bytes).decode('utf-8')
        
        # Strip extension from doc_id if it already has the correct extension
        # This prevents double extensions like "report.json.json"
        # We always pass fileExtension separately so the backend knows what to use
        if file_extension and doc_id.lower().endswith(file_extension.lower()):
            # Remove the extension from doc_id (e.g., "report.json" -> "report")
            doc_id = doc_id[:-len(file_extension)]
        
        # Prepare payload - always pass fileExtension explicitly
        # The backend defaults to .pdf if not provided, causing .json.pdf issues
        payload = {
            "clientId": client_id,
            "docId": doc_id,
            "content_base64": content_base64,
            "contentType": content_type,  # Include content type for proper S3 metadata
            "fileExtension": file_extension,  # Always pass to override backend default of .pdf
        }
        
        # Add data object if provided (sanitize floats for DynamoDB)
        if data_object:
            payload["dataObject"] = _sanitize_for_dynamodb(data_object)
        
        # Upload to docRepo
        logger.info(f"[DOCREPO] Uploading to S3 - Client: {client_id}, Doc: {doc_id}, Type: {content_type}")
        
        headers = {
            "Authorization": f"Bearer {config['auth_token']}",
            "Content-Type": "application/json"
        }
        
        response = requests.post(
            f"{config['put_api_base']}/put",
            json=payload,
            headers=headers,
            timeout=60
        )
        
        if response.status_code == 200:
            result = response.json()
            logger.info(f"[DOCREPO] Success - Uploaded {content_type} to S3 for client {client_id}")
            
            # Try to get signed URL
            url = result.get("url")
            if not url:
                url_result = get_docrepo_url(doc_id, client_id)
                url = url_result.get("url")
            
            return {
                "success": True,
                "s3_uploaded": True,
                "url": url,
                "client_id": client_id,
                "doc_id": doc_id,
                "content_type": content_type,
                "file_extension": file_extension,
                "message": result.get("message", "Uploaded"),
                "data_object_stored": result.get("dataObjectStored", False),
            }
        elif response.status_code == 400 and "No S3 bucket" in response.text:
            # Bucket doesn't exist - create it and retry
            logger.info(f"[DOCREPO] Bucket doesn't exist, creating it for client {client_id}")
            bucket_result = create_docrepo_bucket(client_id)
            
            if not bucket_result.get("bucket_exists"):
                logger.error(f"[DOCREPO] Failed to create bucket: {bucket_result.get('message')}")
                return {
                    "success": False,
                    "s3_uploaded": False,
                    "client_id": client_id,
                    "doc_id": doc_id,
                    "error": "Failed to create bucket",
                    "message": bucket_result.get("message", "Bucket creation failed")
                }
            
            # Retry upload after creating bucket
            logger.info(f"[DOCREPO] Retrying upload after bucket creation")
            response = requests.post(
                f"{config['put_api_base']}/put",
                json=payload,
                headers=headers,
                timeout=60
            )
            
            if response.status_code == 200:
                result = response.json()
                logger.info(f"[DOCREPO] Success - Uploaded {content_type} to S3 for client {client_id} (after bucket creation)")
                
                # Try to get signed URL
                url = result.get("url")
                if not url:
                    url_result = get_docrepo_url(doc_id, client_id)
                    url = url_result.get("url")
                
                return {
                    "success": True,
                    "s3_uploaded": True,
                    "url": url,
                    "client_id": client_id,
                    "doc_id": doc_id,
                    "content_type": content_type,
                    "file_extension": file_extension,
                    "message": result.get("message", "Uploaded"),
                    "data_object_stored": result.get("dataObjectStored", False),
                    "bucket_created": True,
                }
            else:
                logger.error(f"[DOCREPO] Upload failed after bucket creation - Status {response.status_code}: {response.text}")
                return {
                    "success": False,
                    "s3_uploaded": False,
                    "client_id": client_id,
                    "doc_id": doc_id,
                    "error": f"Upload failed with status {response.status_code}",
                    "message": response.text
                }
        else:
            logger.error(f"[DOCREPO] Upload failed - Status {response.status_code}: {response.text}")
            return {
                "success": False,
                "s3_uploaded": False,
                "client_id": client_id,
                "doc_id": doc_id,
                "error": f"Upload failed with status {response.status_code}",
                "message": response.text
            }
            
    except Exception as e:
        logger.error(f"[DOCREPO] Exception during upload: {e}")
        return {
            "success": False,
            "s3_uploaded": False,
            "client_id": client_id,
            "doc_id": doc_id,
            "error": str(e),
            "message": f"Failed to upload to S3: {str(e)}"
        }


def upload_json_to_docrepo(
    data: Union[dict, list],
    filename: str,
    client_id: str = DOCSORCH_CLIENT_ID,
    data_object: Optional[Dict[str, Any]] = None,
) -> dict[str, Any]:
    """Upload JSON data to docRepo.
    
    Convenience function for uploading JSON files.
    
    Args:
        data: Dictionary or list to serialize as JSON
        filename: Filename with .json extension (e.g., "report.json")
        client_id: The client identifier
        data_object: Optional metadata
        
    Returns:
        Upload result with success status and URL
    """
    import json
    content = json.dumps(data, indent=2, default=str)
    return upload_to_docrepo(
        content=content,
        filename=filename,
        client_id=client_id,
        data_object=data_object,
    )


def upload_markdown_to_docrepo(
    markdown_content: str,
    filename: str,
    client_id: str = DOCSORCH_CLIENT_ID,
    data_object: Optional[Dict[str, Any]] = None,
) -> dict[str, Any]:
    """Upload Markdown content to docRepo.
    
    Convenience function for uploading .md files.
    
    Args:
        markdown_content: The markdown text
        filename: Filename with .md extension (e.g., "report.md")
        client_id: The client identifier
        data_object: Optional metadata
        
    Returns:
        Upload result with success status and URL
    """
    return upload_to_docrepo(
        content=markdown_content,
        filename=filename,
        client_id=client_id,
        data_object=data_object,
    )


def upload_pdf_to_docrepo(
    pdf_bytes: bytes,
    filename: str,
    client_id: str = DOCSORCH_CLIENT_ID,
    data_object: Optional[Dict[str, Any]] = None,
) -> dict[str, Any]:
    """Upload PDF document to docRepo.
    
    Convenience function for uploading .pdf files.
    
    Args:
        pdf_bytes: The PDF file bytes
        filename: Filename with .pdf extension (e.g., "document.pdf")
        client_id: The client identifier
        data_object: Optional metadata
        
    Returns:
        Upload result with success status and URL
    """
    return upload_to_docrepo(
        content=pdf_bytes,
        filename=filename,
        client_id=client_id,
        data_object=data_object,
    )


def upload_image_to_docrepo(
    image_bytes: bytes,
    filename: str,
    client_id: str = DOCSORCH_CLIENT_ID,
    data_object: Optional[Dict[str, Any]] = None,
) -> dict[str, Any]:
    """Upload image to docRepo.
    
    Convenience function for uploading image files (.jpg, .jpeg, .png, .gif, .webp).
    
    Args:
        image_bytes: The image file bytes
        filename: Filename with image extension (e.g., "screenshot.jpg", "photo.png")
        client_id: The client identifier
        data_object: Optional metadata
        
    Returns:
        Upload result with success status and URL
    """
    return upload_to_docrepo(
        content=image_bytes,
        filename=filename,
        client_id=client_id,
        data_object=data_object,
    )


def get_docrepo_url(
    doc_id: str,
    client_id: str = DOCSORCH_CLIENT_ID,
) -> dict[str, Any]:
    """Get a signed URL for a document from docRepo S3.
    
    Args:
        doc_id: The document identifier
        client_id: The client identifier (default: discOrchAgent)
        
    Returns:
        Dictionary containing:
        - success: Boolean indicating if URL was retrieved
        - url: The presigned URL (valid for ~5 minutes)
        - expires_in_seconds: How long the URL is valid
        - data_object: The metadata stored with the document (if any)
    """
    config = _get_docrepo_config()
    
    try:
        headers = {
            "Authorization": f"Bearer {config['auth_token']}"
        }
        
        response = requests.get(
            f"{config['get_api_base']}/doc",
            params={"clientId": client_id, "docId": doc_id},
            headers=headers,
            timeout=30
        )
        
        if response.status_code == 200:
            result = response.json()
            logger.info(f"[DOCREPO] Retrieved signed URL for client {client_id}, doc {doc_id}")
            return {
                "success": True,
                "url": result.get("url"),
                "expires_in_seconds": result.get("expiresInSeconds", 300),
                "has_data_object": result.get("hasDataObject", False),
                "data_object": result.get("dataObject"),
            }
        else:
            logger.error(f"[DOCREPO] Failed to get URL - Status {response.status_code}")
            return {
                "success": False,
                "error": f"Status {response.status_code}",
                "message": response.text
            }
            
    except Exception as e:
        logger.error(f"[DOCREPO] Exception getting URL: {e}")
        return {
            "success": False,
            "error": str(e)
        }


def download_from_docrepo(
    doc_id: str,
    client_id: str = DOCSORCH_CLIENT_ID,
    save_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Download a document from DocRepo S3 to local file or memory.
    
    This function:
    1. Gets a signed URL for the document
    2. Downloads the content from S3
    3. Optionally saves to a local file path
    4. Returns the content and metadata
    
    Args:
        doc_id: The document identifier (e.g., "credit_report_12345.pdf")
        client_id: The client identifier (default: docsOrchAgent)
        save_path: Optional path to save the file locally. If None, content is 
                   returned in memory only.
        
    Returns:
        Dictionary containing:
        - success: Boolean indicating if download succeeded
        - content: The file content as bytes (if no save_path provided)
        - content_length: Size of downloaded content in bytes
        - save_path: Path where file was saved (if save_path was provided)
        - doc_id: The document identifier
        - client_id: The client identifier
        - data_object: The metadata stored with the document (if any)
        - error: Error message (if failed)
    """
    config = _get_docrepo_config()
    
    # Step 1: Get signed URL
    url_result = get_docrepo_url(doc_id, client_id)
    
    if not url_result.get("success"):
        logger.error(f"[DOCREPO] Failed to get URL for download: {url_result.get('error')}")
        return {
            "success": False,
            "doc_id": doc_id,
            "client_id": client_id,
            "error": f"Failed to get signed URL: {url_result.get('error', 'Unknown error')}",
        }
    
    signed_url = url_result.get("url")
    if not signed_url:
        return {
            "success": False,
            "doc_id": doc_id,
            "client_id": client_id,
            "error": "No signed URL returned",
        }
    
    # Step 2: Download content from S3
    try:
        logger.info(f"[DOCREPO] Downloading {doc_id} from client {client_id}")
        
        response = requests.get(signed_url, timeout=120)
        
        if response.status_code != 200:
            logger.error(f"[DOCREPO] Download failed - Status {response.status_code}")
            return {
                "success": False,
                "doc_id": doc_id,
                "client_id": client_id,
                "error": f"Download failed with status {response.status_code}",
            }
        
        content = response.content
        content_length = len(content)
        
        logger.info(f"[DOCREPO] Downloaded {content_length} bytes for {doc_id}")
        
        # Step 3: Save to file if path provided
        if save_path:
            # Ensure parent directory exists
            save_dir = Path(save_path).parent
            save_dir.mkdir(parents=True, exist_ok=True)
            
            with open(save_path, "wb") as f:
                f.write(content)
            
            logger.info(f"[DOCREPO] Saved to {save_path}")
            
            return {
                "success": True,
                "doc_id": doc_id,
                "client_id": client_id,
                "content_length": content_length,
                "save_path": save_path,
                "data_object": url_result.get("data_object"),
            }
        else:
            # Return content in memory
            return {
                "success": True,
                "doc_id": doc_id,
                "client_id": client_id,
                "content": content,
                "content_length": content_length,
                "data_object": url_result.get("data_object"),
            }
            
    except Exception as e:
        logger.error(f"[DOCREPO] Exception during download: {e}")
        return {
            "success": False,
            "doc_id": doc_id,
            "client_id": client_id,
            "error": str(e),
        }


def download_documents_from_docrepo(
    document_list: list[Dict[str, str]],
    output_dir: str,
    client_id: str = DOCSORCH_CLIENT_ID,
) -> Dict[str, Any]:
    """Download multiple documents from DocRepo to a local directory.
    
    This is a batch download function for downloading multiple documents at once.
    Each document is saved with its doc_type as the filename.
    
    Args:
        document_list: List of dicts with keys:
            - doc_type: Document type name (e.g., "Credit Report")
            - docrepo_key: DocRepo doc_id key (e.g., "credit_report_12345.pdf")
        output_dir: Directory to save downloaded files
        client_id: The client identifier
        
    Returns:
        Dictionary containing:
        - success: Boolean indicating if all downloads succeeded
        - downloaded: List of successfully downloaded doc_types
        - failed: List of failed doc_types with errors
        - files: Dict mapping doc_type to local file path
        - total: Total documents attempted
        - download_count: Number successfully downloaded
        - fail_count: Number that failed
    """
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    
    downloaded = []
    failed = []
    files = {}
    
    for doc_info in document_list:
        doc_type = doc_info.get("doc_type", "unknown")
        docrepo_key = doc_info.get("docrepo_key")
        
        if not docrepo_key:
            failed.append({
                "doc_type": doc_type,
                "error": "No docrepo_key provided",
            })
            continue
        
        # Generate local filename
        safe_doc_type = doc_type.replace(" ", "_").replace("/", "_").replace("'", "")
        # Preserve original extension or default to .pdf
        ext = Path(docrepo_key).suffix or ".pdf"
        local_filename = f"{safe_doc_type}{ext}"
        save_path = str(output_path / local_filename)
        
        # Download
        result = download_from_docrepo(
            doc_id=docrepo_key,
            client_id=client_id,
            save_path=save_path,
        )
        
        if result.get("success"):
            downloaded.append(doc_type)
            files[doc_type] = {
                "path": save_path,
                "size": result.get("content_length", 0),
                "docrepo_key": docrepo_key,
                "data_object": result.get("data_object"),
            }
            logger.info(f"[DOCREPO] Downloaded {doc_type} to {save_path}")
        else:
            failed.append({
                "doc_type": doc_type,
                "docrepo_key": docrepo_key,
                "error": result.get("error", "Unknown error"),
            })
            logger.warning(f"[DOCREPO] Failed to download {doc_type}: {result.get('error')}")
    
    return {
        "success": len(failed) == 0,
        "downloaded": downloaded,
        "failed": failed,
        "files": files,
        "total": len(document_list),
        "download_count": len(downloaded),
        "fail_count": len(failed),
    }

