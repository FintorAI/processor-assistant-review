"""Underwriting Conditions Utilities.

This module provides functions for managing underwriting conditions in Encompass,
including listing existing conditions and creating new ones.

Uses Encompass API v1 underwriting conditions endpoints:
- GET /encompass/v1/loans/{LoanGuid}/conditions/underwriting
- POST /encompass/v1/loans/{LoanGuid}/conditions/underwriting
"""

import logging
from typing import Any, Dict, List, Union

logger = logging.getLogger(__name__)

# encompass_client is provided by the runtime environment (baseCopilotAgent tools layer)
try:
    from encompass_client import (
        get_underwriting_conditions,
        create_underwriting_condition,
    )
except ImportError:
    try:
        from tools.encompass_client import (
            get_underwriting_conditions,
            create_underwriting_condition,
        )
    except ImportError:
        get_underwriting_conditions = None
        create_underwriting_condition = None
        logger.debug("encompass_client not available — conditions module will fail at runtime")


def get_loan_conditions(loan_id: str, filter_ptf: bool = False, state: dict = None) -> Dict[str, Any]:
    """Get all underwriting conditions for a loan.
    
    Queries the underwriting conditions endpoint in Encompass.
    Optionally filters for PTF (Prior To Funding) conditions only.
    Conditions with an empty priorTo are treated as PTF.
    
    Args:
        loan_id: The Encompass loan GUID
        filter_ptf: If True, only return conditions where priorTo contains "Funding"
        
    Returns:
        Dictionary containing:
        - conditions: List of condition dictionaries
        - count: Total number of conditions
        - ptf_count: Number of PTF conditions
        
    Example:
        >>> result = get_loan_conditions("ae9dd6e2-...")
        >>> print(f"Found {result['count']} conditions")
        >>> for cond in result['conditions']:
        ...     print(f"  - {cond['title']} ({cond['category']})")
    """
    logger.info(f"[CONDITIONS] Fetching conditions for loan {loan_id[:8]}...")
    
    try:
        conditions = get_underwriting_conditions(loan_id, state=state)
        logger.info(f"[CONDITIONS] Underwriting endpoint returned {len(conditions)} conditions")

        ptf_conditions = []
        all_conditions = []
        
        for cond in conditions:
            normalized = {
                "id": cond.get("id", ""),
                "title": cond.get("title", ""),
                "description": cond.get("description", ""),
                "category": cond.get("category", ""),
                "priorTo": cond.get("priorTo", ""),
                "status": cond.get("statusDescription", cond.get("status", "Added")),
                "source": cond.get("source", ""),
                "addedDate": cond.get("addedDate", cond.get("createdDate", "")),
                "clearedDate": cond.get("clearedDate", ""),
            }
            all_conditions.append(normalized)
            
            prior_to = str(cond.get("priorTo", "")).strip().lower()
            # Treat as PTF if explicitly marked "Funding", or if priorTo is
            # empty/unset (UW conditions without a priorTo are assumed PTF
            # unless explicitly assigned to another phase like CTC or Docs).
            is_ptf = "funding" in prior_to or not prior_to
            if is_ptf:
                ptf_conditions.append(normalized)
        
        result_conditions = ptf_conditions if filter_ptf else all_conditions
        
        result = {
            "loan_id": loan_id,
            "conditions": result_conditions,
            "count": len(result_conditions),
            "total_count": len(all_conditions),
            "ptf_count": len(ptf_conditions),
        }
        
        logger.info(f"[CONDITIONS] Found {len(result_conditions)} conditions ({len(ptf_conditions)} PTF)")
        return result
        
    except PermissionError as e:
        logger.warning(f"[CONDITIONS] Permission error: {e}")
        return {
            "loan_id": loan_id,
            "conditions": [],
            "count": 0,
            "total_count": 0,
            "ptf_count": 0,
            "error": str(e),
        }
    except Exception as e:
        logger.error(f"[CONDITIONS] Error fetching conditions: {e}")
        return {
            "loan_id": loan_id,
            "conditions": [],
            "count": 0,
            "total_count": 0,
            "ptf_count": 0,
            "error": str(e),
        }


def create_condition(
    loan_id: str,
    title: str,
    description: str = "",
    category: str = "Credit",
    prior_to: str = "Funding",
    state: dict = None,
) -> Dict[str, Any]:
    """Create a new underwriting condition on a loan.
    
    Args:
        loan_id: The Encompass loan GUID
        title: Condition title (short description)
        description: Detailed condition description
        category: Condition category (Credit, Income, Assets, Property, Legal)
        prior_to: When condition must be satisfied (default: "Funding" for PTF)
        
    Returns:
        Dictionary with creation result:
        - success: Boolean indicating success
        - condition_id: ID of created condition (if successful)
        - message: Human-readable message
        
    Example:
        >>> result = create_condition(
        ...     loan_id="ae9dd6e2-...",
        ...     title="Verify Employment",
        ...     description="Verify current employment status",
        ...     category="Income"
        ... )
        >>> if result['success']:
        ...     print(f"Created: {result['condition_id']}")
    """
    logger.info(f"[CONDITIONS] Creating condition for loan {loan_id[:8]}: {title}")
    
    try:
        condition_data = {
            "title": title,
            "description": description,
            "category": category,
            "priorTo": prior_to,
            "forAllApplications": True,
        }
        
        result = create_underwriting_condition(loan_id, condition_data)
        condition_id = result.get("id", "")
        
        logger.info(f"[CONDITIONS] Created condition: {condition_id}")
        return {
            "success": True,
            "loan_id": loan_id,
            "condition_id": condition_id,
            "title": title,
            "message": f"Successfully created condition: {title}",
        }
        
    except PermissionError as e:
        logger.warning(f"[CONDITIONS] Permission error: {e}")
        return {
            "success": False,
            "loan_id": loan_id,
            "error": str(e),
            "message": "Permission denied",
        }
    except Exception as e:
        logger.error(f"[CONDITIONS] Error creating condition: {e}")
        return {
            "success": False,
            "loan_id": loan_id,
            "error": str(e),
            "message": f"Failed to create condition: {str(e)}",
        }


def create_conditions_batch(
    loan_id: str,
    conditions: List[Dict[str, str]],
    state: dict = None,
) -> Dict[str, Any]:
    """Create multiple underwriting conditions.
    
    Args:
        loan_id: The Encompass loan GUID
        conditions: List of condition dictionaries, each with:
            - title: Condition title (required)
            - description: Condition description
            - category: Condition category (default: "Credit")
            - prior_to: When condition must be satisfied (default: "Funding")
            
    Returns:
        Dictionary with batch creation results:
        - success: Boolean indicating all succeeded
        - created_count: Number successfully created
        - created: List of created condition info
        - failed: List of failed conditions with errors
        
    Example:
        >>> conditions = [
        ...     {"title": "Verify Employment", "category": "Income"},
        ...     {"title": "Verify Assets", "category": "Assets"},
        ... ]
        >>> result = create_conditions_batch("ae9dd6e2-...", conditions)
        >>> print(f"Created {result['created_count']} conditions")
    """
    logger.info(f"[CONDITIONS] Creating {len(conditions)} conditions for loan {loan_id[:8]}")
    
    created = []
    failed = []
    
    for cond in conditions:
        result = create_condition(
            loan_id=loan_id,
            title=cond.get("title", ""),
            description=cond.get("description", ""),
            category=cond.get("category", "Credit"),
            prior_to=cond.get("prior_to", "Funding"),
            state=state,
        )
        
        if result.get("success"):
            created.append({
                "condition_id": result.get("condition_id"),
                "title": cond.get("title"),
            })
        else:
            failed.append({
                "title": cond.get("title"),
                "error": result.get("error"),
            })
    
    return {
        "success": len(failed) == 0,
        "loan_id": loan_id,
        "created_count": len(created),
        "requested_count": len(conditions),
        "created": created,
        "failed": failed,
        "message": f"Created {len(created)}/{len(conditions)} conditions",
    }

