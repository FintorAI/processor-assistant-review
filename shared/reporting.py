"""Shared Reporting Tools - Step report generation and storage.

This module provides tools for saving detailed step reports to state.step_fullReports
and DocRepo for UI access and audit trail.

NOTE: step_fullReports is separate from step_reports (which write_todos uses) to avoid
concurrent update conflicts.

Usage:
    from shared.reporting import save_step_report
    
    # After completing a step, save the report
    save_step_report(
        step_name="STEP_01",
        status="completed",
        summary="Extracted 45 fields from 12 documents",
        details={...}
    )
"""

import json
import logging
from datetime import datetime, timezone
from typing import Annotated, Any, Dict, List, Optional, Union

from langchain_core.tools import tool, InjectedToolCallId
from langchain_core.messages import ToolMessage
from langgraph.types import Command

from .docrepo import upload_markdown_to_docrepo, upload_json_to_docrepo, DOCSORCH_CLIENT_ID

logger = logging.getLogger(__name__)


@tool
def save_step_report(
    tool_call_id: Annotated[str, InjectedToolCallId],
    step_name: str,
    status: str,
    summary: str,
    details: Optional[Dict[str, Any]] = None,
    issues: Optional[List[Dict[str, Any]]] = None,
    metrics: Optional[Dict[str, Any]] = None,
) -> Command:
    """Save a detailed step report to state.step_fullReports for UI access and audit trail.
    
    Call this AFTER completing each step to save a summary of results.
    Reports are saved to:
    1. state.step_fullReports - For detailed reports (separate from step_reports)
    2. DocRepo S3 - For persistent storage and signed URLs
    
    NOTE: Uses step_fullReports instead of step_reports to avoid conflicts with write_todos.
    
    Args:
        step_name: Step identifier (e.g., "STEP_00", "STEP_01", "step_0", "step_1")
        status: Step status ("completed", "partial", "failed", "skipped")
        summary: Brief summary of what was done
        details: Optional detailed results dict
        issues: Optional list of issues found (each with field_id, message, severity)
        metrics: Optional metrics dict (e.g., fields_extracted, documents_processed)
        
    Returns:
        Command updating step_fullReports state with the report
    """
    logger.info(f"[REPORT] Saving report for {step_name}: {status}")
    
    # Normalize step name to consistent format
    step_key = step_name.upper().replace("-", "_")
    if not step_key.startswith("STEP_"):
        # Handle "step_0" -> "STEP_00" or "step0" -> "STEP_00"
        step_num = step_key.replace("STEP", "").replace("_", "")
        if step_num.isdigit():
            step_key = f"STEP_{int(step_num):02d}"
    
    timestamp = datetime.now(timezone.utc).isoformat()
    
    # Build the report
    report = {
        "step_name": step_key,
        "status": status,
        "summary": summary,
        "timestamp": timestamp,
        "details": details or {},
        "issues": issues or [],
        "metrics": metrics or {},
    }
    
    # Generate markdown for human-readable report
    markdown_lines = [
        f"# {step_key} Report",
        f"",
        f"**Status:** {status}",
        f"**Timestamp:** {timestamp}",
        f"",
        f"## Summary",
        f"{summary}",
        f"",
    ]
    
    # Add metrics if provided
    if metrics:
        markdown_lines.extend([
            f"## Metrics",
            f"",
        ])
        for key, value in metrics.items():
            markdown_lines.append(f"- **{key}:** {value}")
        markdown_lines.append("")
    
    # Add issues if any
    if issues:
        markdown_lines.extend([
            f"## Issues ({len(issues)})",
            f"",
        ])
        for issue in issues:
            severity = issue.get("severity", "info")
            message = issue.get("message", "No message")
            field_id = issue.get("field_id", "")
            field_str = f" (Field: {field_id})" if field_id else ""
            markdown_lines.append(f"- [{severity.upper()}]{field_str} {message}")
        markdown_lines.append("")
    
    # Add details summary if provided
    if details:
        markdown_lines.extend([
            f"## Details",
            f"",
            f"```json",
            json.dumps(details, indent=2, default=str)[:2000],  # Truncate for readability
            f"```",
            f"",
        ])
    
    markdown_content = "\n".join(markdown_lines)
    report["markdown"] = markdown_content
    
    # Upload to DocRepo for persistent storage
    docrepo_result = None
    try:
        # Upload markdown version
        md_result = upload_markdown_to_docrepo(
            markdown_content=markdown_content,
            filename=f"{step_key}_report_{timestamp[:10]}.md",
            client_id=DOCSORCH_CLIENT_ID,
            data_object={
                "step_name": step_key,
                "status": status,
                "timestamp": timestamp,
            },
        )
        
        if md_result.get("success"):
            report["docrepo_url"] = md_result.get("url")
            report["docrepo_doc_id"] = md_result.get("doc_id")
            logger.info(f"[REPORT] Uploaded to DocRepo: {md_result.get('doc_id')}")
        
        docrepo_result = md_result
    except Exception as e:
        logger.warning(f"[REPORT] DocRepo upload failed: {e}")
        report["docrepo_error"] = str(e)
    
    # Build result message
    result = {
        "success": True,
        "step_name": step_key,
        "status": status,
        "summary": summary,
        "issues_count": len(issues) if issues else 0,
        "docrepo_uploaded": bool(docrepo_result and docrepo_result.get("success")),
        "message": f"Report saved for {step_key}: {status}",
    }
    
    logger.info(f"[REPORT] Report saved: {step_key} - {status}")
    
    # Update step_fullReports in state (separate from step_reports which write_todos uses)
    return Command(
        update={
            "step_fullReports": {
                step_key: report,
            },
            "messages": [ToolMessage(content=json.dumps(result), tool_call_id=tool_call_id)]
        }
    )


@tool
def get_step_report(
    tool_call_id: Annotated[str, InjectedToolCallId],
    step_name: str,
) -> Command:
    """Get a previously saved step report.
    
    Note: This tool returns a placeholder. The actual report is in state.step_reports.
    Use this to remind the agent to check state.step_reports[step_name].
    
    Args:
        step_name: Step identifier (e.g., "STEP_00", "STEP_01")
        
    Returns:
        Command with instructions to check state
    """
    step_key = step_name.upper().replace("-", "_")
    if not step_key.startswith("STEP_"):
        step_num = step_key.replace("STEP", "").replace("_", "")
        if step_num.isdigit():
            step_key = f"STEP_{int(step_num):02d}"
    
    result = {
        "success": True,
        "step_name": step_key,
        "message": f"Check state.step_reports['{step_key}'] for the report",
        "note": "Step reports are stored in state.step_reports dict",
    }
    
    return Command(
        update={
            "messages": [ToolMessage(content=json.dumps(result), tool_call_id=tool_call_id)]
        }
    )


