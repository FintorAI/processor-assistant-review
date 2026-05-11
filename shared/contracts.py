"""
Shared inter-agent contract types for the processor-assistant multi-agent system.

Every sub-agent deployment (review, integrations, computer-use) accepts the same
AgentInput and returns the same AgentOutput so the orchestrator and UI can call
any agent uniformly — both for full workflow runs and one-off action invocations.

Promote this file to a pip-installable package (processor_assistant_shared)
when sibling repos are fully operational.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ─────────────────────────────────────────────────────────────
# Enums
# ─────────────────────────────────────────────────────────────

class RunStatus(str, Enum):
    OK = "ok"
    FAILED = "failed"
    NEEDS_HITL = "needs_hitl"


class FlagSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


# ─────────────────────────────────────────────────────────────
# Sub-models
# ─────────────────────────────────────────────────────────────

class Flag(BaseModel):
    """A single issue detected during a substep."""
    code: str = Field(..., description="Machine-readable flag code, e.g. 'EMD_AMOUNT_MISMATCH'")
    severity: FlagSeverity
    substep_id: str = Field(..., description="Substep that raised this flag, e.g. '5.2'")
    substep_name: Optional[str] = Field(None, description="Human-readable substep name")
    message: str = Field(..., description="Human-readable description of the issue")
    payload: Optional[Dict[str, Any]] = Field(
        None, description="Structured data relevant to this flag (e.g. expected vs actual values)"
    )
    resolved: bool = Field(False, description="Set to True by HITL resolution")
    resolution_note: Optional[str] = Field(None, description="Processor note on resolution")


class FieldWrite(BaseModel):
    """A single LOS field update performed (or proposed) by a substep."""
    field_id: str = Field(..., description="Encompass field ID, e.g. 'CX.PROCESSOR.NAME'")
    field_name: Optional[str] = Field(None, description="Human-readable field name")
    old_value: Optional[Any] = Field(None, description="Value before write")
    new_value: Any = Field(..., description="Value written (or to be written)")
    committed: bool = Field(
        False,
        description="True if the write was actually sent to Encompass; False if staged/proposed"
    )
    substep_id: Optional[str] = Field(None, description="Substep that produced this write")


class ExternalResult(BaseModel):
    """Result from a third-party system call (Ocrolus, DU, LP, SMTP, etc.)."""
    system: str = Field(..., description="External system name, e.g. 'ocrolus', 'fannie_du', 'smtp'")
    action: str = Field(..., description="Action taken, e.g. 'order_appraisal', 'send_email'")
    status: str = Field(..., description="'success', 'failed', 'skipped'")
    result: Optional[Dict[str, Any]] = Field(None, description="Parsed response from the system")
    error: Optional[str] = Field(None, description="Error message if status is 'failed'")
    substep_id: Optional[str] = Field(None, description="Substep that produced this call")


class EfolderAction(BaseModel):
    """An action taken against the Encompass eFolder (UI automation)."""
    action: str = Field(..., description="'delete_bucket', 'mark_ready_for_uw', 'move_to_recycle', etc.")
    target: str = Field(..., description="Document type or bucket name acted upon")
    status: str = Field(..., description="'success', 'failed', 'skipped'")
    error: Optional[str] = Field(None, description="Error message if status is 'failed'")
    substep_id: Optional[str] = Field(None, description="Substep that produced this action")


# ─────────────────────────────────────────────────────────────
# Top-level Input / Output
# ─────────────────────────────────────────────────────────────

class AgentInput(BaseModel):
    """
    Universal input accepted by every sub-agent deployment.

    Workflow mode (orchestrator calling):
        - Pass loan_id (already resolved by the orchestrator) and optionally
          pre-fetched inputs to avoid redundant Encompass fetches.
        - Leave action=None to run the agent's full internal workflow.

    One-off mode (UI calling a single action directly):
        - Set action to the tool name, e.g. "order_appraisal".
        - The agent runs just that substep, fetching its own inputs from Encompass.
    """
    loan_id: str = Field(..., description="Encompass loan GUID (UUID), not the loan number")
    action: Optional[str] = Field(
        None,
        description="Tool name to run as a one-off action. None = run full internal workflow."
    )
    inputs: Optional[Dict[str, Any]] = Field(
        None,
        description="Pre-fetched inputs the orchestrator passes to avoid redundant Encompass fetches"
    )
    processor_name: Optional[str] = Field(
        None,
        description="Processor name — required for substeps that write CX.PROCESSOR.NAME"
    )
    env: Optional[str] = Field(
        "Prod",
        description="'Prod' or 'Test' — controls which Encompass environment is used"
    )


class AgentOutput(BaseModel):
    """
    Universal output returned by every sub-agent deployment.

    The orchestrator aggregates flags and field_writes from all sub-agents
    and presents them at the HITL review step.
    """
    loan_id: str
    action: Optional[str] = Field(None, description="Echoes the input action, or None for full run")
    status: RunStatus = Field(..., description="Overall run status")

    flags: List[Flag] = Field(
        default_factory=list,
        description="All flags raised during this run, regardless of severity"
    )
    field_writes: List[FieldWrite] = Field(
        default_factory=list,
        description="All LOS field writes performed or proposed during this run"
    )
    external_results: List[ExternalResult] = Field(
        default_factory=list,
        description="Results from third-party API calls (integrations agent)"
    )
    efolder_actions: List[EfolderAction] = Field(
        default_factory=list,
        description="eFolder UI actions taken (computer-use agent)"
    )
    errors: List[str] = Field(
        default_factory=list,
        description="Unrecoverable errors that caused status=failed"
    )
    summary: Optional[str] = Field(
        None,
        description="Human-readable run summary for the HITL review or UI display"
    )
