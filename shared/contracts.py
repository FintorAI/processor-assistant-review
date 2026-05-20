"""
Shared inter-agent contract types for the processor-assistant multi-agent system.

Every sub-agent deployment (review, integrations, computer-use) accepts the same
AgentInput and returns the same AgentOutput so the orchestrator and UI can call
any agent uniformly — both for full workflow runs and one-off action invocations.

Promote this file to a pip-installable package (processor_assistant_shared)
when sibling repos are fully operational.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, model_validator


# GUID regex: 8-4-4-4-12 hex chars, optional curly braces.
# Imported from LG-discOrch/tools/shared/encompass_io.py — kept duplicated
# here so the orchestrator can validate inputs without pulling in sub-agent
# code.
_GUID_PATTERN = re.compile(
    r"^[{]?[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}[}]?$"
)


def _looks_like_guid(value: Optional[str]) -> bool:
    if not value:
        return False
    return bool(_GUID_PATTERN.match(str(value).strip()))


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

    Identifier convention (mirrors LG-discOrch / LG-docsOrch):
        - `loan_number` is what callers actually have on hand (human-readable,
          e.g. "2509946673"). It is the PRIMARY input.
        - `loan_id` is the Encompass loan GUID
          (e.g. "abc12345-def6-7890-abcd-ef0123456789"). It is OPTIONAL on
          input — the orchestrator resolves it via substep 0.1 (`find_loan`)
          and forwards it to every subsequent sub-agent so they don't repeat
          the lookup.

    At least one of `loan_number` / `loan_id` must be provided. If only
    `loan_id` is provided and it doesn't look like a GUID, it is treated as
    a loan_number — this preserves back-compat with older callers that
    passed loan numbers in the `loan_id` slot.

    Workflow mode (orchestrator calling):
        - Pass loan_number (and loan_id once resolved) plus optional
          pre-fetched inputs to avoid redundant Encompass fetches.
        - Leave action=None to run the agent's full internal workflow.

    One-off mode (UI calling a single action directly):
        - Set action to the tool name, e.g. "order_appraisal".
        - The agent runs just that substep, fetching its own inputs from
          Encompass.
    """
    loan_number: Optional[str] = Field(
        None,
        description="Human-readable Encompass loan number (e.g. '2509946673'). Primary input."
    )
    loan_id: Optional[str] = Field(
        None,
        description=(
            "Encompass loan GUID. Populated by the orchestrator after find_loan "
            "resolves it. Optional on inbound input from the UI."
        ),
    )
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

    @model_validator(mode="after")
    def _coerce_identifiers(self) -> "AgentInput":
        # Back-compat: if only loan_id is set and it doesn't look like a GUID,
        # the caller almost certainly handed us a loan number in the loan_id
        # slot. Move it to loan_number and clear loan_id so find_loan resolves
        # a real GUID rather than the tool blindly trusting the value.
        if self.loan_id and not self.loan_number and not _looks_like_guid(self.loan_id):
            self.loan_number = str(self.loan_id).strip()
            self.loan_id = None

        # If the supplied loan_id is a GUID, sanitize curly braces (Encompass
        # sometimes returns `{guid}` but its API rejects them).
        if self.loan_id and _looks_like_guid(self.loan_id):
            self.loan_id = str(self.loan_id).strip().replace("{", "").replace("}", "")

        if not self.loan_number and not self.loan_id:
            raise ValueError(
                "AgentInput requires at least one of loan_number or loan_id."
            )
        return self


class AgentOutput(BaseModel):
    """
    Universal output returned by every sub-agent deployment.

    The orchestrator aggregates flags and field_writes from all sub-agents
    and presents them at the HITL review step.

    Identifier echo:
        - `loan_id` is the resolved Encompass GUID. Sub-agents SHOULD populate
          this once find_loan has resolved it (the orchestrator reads it back
          and stashes it in state so downstream substeps don't repeat lookup).
        - `loan_number` echoes the human-readable identifier for log/UI use.
    """
    loan_id: Optional[str] = Field(
        None,
        description="Resolved Encompass loan GUID. Optional — may be unset if find_loan failed.",
    )
    loan_number: Optional[str] = Field(
        None,
        description="Human-readable loan number echoed back for log/UI use.",
    )
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
