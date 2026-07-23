"""update_processing_submitted — Tool for substep 14.3: Processing/Submitted Milestone Update

Step 14 (STEP_14): Processor Workflow and Closing
Phase: FORM_UPDATES

Updates the "In Processing/Submitted" milestone worksheet via the v1
milestones API (verified against prod loans 2604964148 / 2607973377, 2026-07-23):

  GET   /encompass/v1/loans/{id}/milestones
  PATCH /encompass/v1/loans/{id}/milestones/{milestoneId}[?action=finish|unfinish]

  - Loan Processor assignment: PATCH loanAssociate = User "adesai" when the
    milestone has no Loan Processor user. (PATCH body must include startDate —
    handled by encompass_client.update_loan_milestone.)
  - "Finished" checkbox = the milestone's doneIndicator via ?action=finish —
    NOT field 1057 (standard schema labels 1057 "Borr Declarations E").
    Auto-finishing is HELD (FINISH_MODE="probe"): the tool attempts
    action=finish only to collect the server-side "missing required fields"
    validation (the same list the EC UI shows in the Go To Fields dialog),
    and immediately action=unfinish if the attempt unexpectedly succeeds.
    Missing fields are surfaced as a warning flag + state['manual_fields']
    rows so the dashboard Field Writes tab can render them for manual entry.

Notes from probing (2026-07-23):
  - v3 /loans/{id}/associates and v3 settings/milestones + businessRules
    endpoints return 403 for our API user — milestone-based v1 access is the
    working path, and the admin's required-fields config is not readable, so
    the finish-attempt probe is the only available source of the missing list.
  - encompassdocs documentAudits/opening (data audit) requires a plan code on
    the loan and audits disclosure readiness, not milestone required fields.
  - A loan open in the Encompass UI is locked (409 EBS-4360); writes and the
    probe are skipped with a warning flag when that happens.
"""
# FACTORY-LOCK: true

import json
import logging
import re
from datetime import datetime, timezone
from typing import Annotated

from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.prebuilt import InjectedState
from langgraph.types import Command

from ._helpers import _manual_field

logger = logging.getLogger(__name__)

MILESTONE_NAME = "In Processing/Submitted"

# Default processor to assign when the milestone has no Loan Processor user.
# Encompass user id confirmed from the live associates listing (Ash Desai).
DEFAULT_PROCESSOR_USER_ID = "adesai"
DEFAULT_PROCESSOR_NAME = "Ash Desai"

# "probe"  — attempt action=finish only to collect the missing-required-fields
#            validation; immediately unfinish if it succeeds. (Current mode —
#            auto-finish is held pending processor confirmation.)
# "finish" — actually finish the milestone (enable once Ash confirms).
# "off"    — never touch doneIndicator.
FINISH_MODE = "probe"

# Field-id-looking tokens in the finish-validation error text, e.g.
# 1532, FR0206, URLA.X107, CX.SOMETHING, TSUM.PropertyFormType
_FIELD_ID_RE = re.compile(
    r"\b(?:[A-Z]{2,6}\d{3,6}|URLA\.X\d+|[A-Z]{2,4}\.[A-Za-z0-9.]{2,40}|\d{3,5})\b"
)


def _parse_missing_fields(error_text: str) -> list[dict]:
    """Best-effort parse of the finish-validation error into field rows.

    The 400 details are free text; extract field-id-like tokens and, when the
    text pairs them with a description (``<id> - <description>`` or
    ``<id>: <description>``), capture that too. Returns
    ``[{"field_id": ..., "description": ...}, ...]`` (deduped, order kept).
    """
    rows: list[dict] = []
    seen: set[str] = set()
    if not error_text:
        return rows

    # Try line-oriented "<id> <sep> <description>" pairs first.
    for line in re.split(r"[\r\n;]+", error_text):
        m = re.match(
            r"\s*(?:Field\s+)?([A-Za-z0-9.]{1,40}?)\s*(?:[-–:]\s+|\s{2,})(.+?)\s*$", line
        )
        if m and _FIELD_ID_RE.fullmatch(m.group(1)):
            fid = m.group(1)
            if fid not in seen:
                seen.add(fid)
                rows.append({"field_id": fid, "description": m.group(2).strip()})

    if rows:
        return rows

    # Fallback: bare field-id tokens anywhere in the text.
    for fid in _FIELD_ID_RE.findall(error_text):
        if fid not in seen:
            seen.add(fid)
            rows.append({"field_id": fid, "description": ""})
    return rows


@tool
def update_processing_submitted(
    tool_call_id: Annotated[str, InjectedToolCallId],
    state: Annotated[dict, InjectedState],
) -> Command:
    """Update the In Processing/Submitted milestone worksheet: assign the Loan
    Processor (Ash Desai) when unassigned, and collect the milestone's missing
    required fields (via a finish-attempt probe) as manual-entry rows for the
    dashboard. The Finished checkbox is NOT left set — auto-finishing is held.

    Call this tool during STEP_14 (Processor Workflow and Closing) as substep 14.3.
    Flags: Loan Processor Assigned (info-overwrite), Loan Processor Already
           Assigned (info), Milestone Required Fields Missing (warning),
           Milestone Finish Pending Manual Confirmation (info),
           Milestone Not Found (warning), Loan Locked (warning)
    """
    loan_id = state.get("loan_id")
    if not loan_id:
        return Command(update={"messages": [ToolMessage(
            content=json.dumps({"error": "No loan_id in state. Run find_loan first."}),
            tool_call_id=tool_call_id,
        )]})

    logger.info(f"[UPDATE_PROCESSING_SUBMITTED] Starting for loan {str(loan_id)[:8]}...")

    from encompass_client import get_loan_milestones, update_loan_milestone

    flags: list = []
    manual_rows: list = []
    now = lambda: datetime.now(timezone.utc).isoformat()  # noqa: E731

    dry_run = False
    try:
        from output.registry import DEV_MODE
        dry_run = getattr(DEV_MODE, "dry_run", False)
    except Exception:
        pass

    milestones = get_loan_milestones(loan_id, state=state)
    milestone = next(
        (m for m in milestones
         if (m.get("milestoneName") or "").strip().lower() == MILESTONE_NAME.lower()),
        None,
    )

    if milestone is None:
        flags.append({
            "substep": "14.3",
            "title": "Milestone Not Found",
            "severity": "warning",
            "details": (
                f"No {MILESTONE_NAME!r} milestone exists on this loan "
                f"({len(milestones)} milestones listed)."
            ),
            "suggestion": "Verify the loan's milestone template in Encompass.",
            "resolved": False,
            "timestamp": now(),
        })
        result = {
            "success": False,
            "substep": "14.3",
            "tool": "update_processing_submitted",
            "message": f"{MILESTONE_NAME!r} milestone not found on loan",
            "flags_count": len(flags),
        }
        return Command(update={
            "messages": [ToolMessage(content=json.dumps(result), tool_call_id=tool_call_id)],
            "flags": flags,
        })

    milestone_id = milestone.get("id", "")
    associate = milestone.get("loanAssociate") or {}
    done_indicator = bool(milestone.get("doneIndicator"))
    has_processor = bool(associate.get("id")) and (
        (associate.get("roleName") or "").strip().lower() == "loan processor"
    )
    loan_locked = False

    # ── Loan Processor assignment ──────────────────────────────────────────
    assignment_performed = False
    if has_processor:
        flags.append({
            "substep": "14.3",
            "title": "Loan Processor Already Assigned",
            "severity": "info",
            "details": (
                f"{MILESTONE_NAME} milestone already has Loan Processor "
                f"{associate.get('name')!r} (id {associate.get('id')!r})."
            ),
            "suggestion": "No action needed.",
            "resolved": True,
            "timestamp": now(),
        })
    elif dry_run:
        flags.append({
            "substep": "14.3",
            "title": "Loan Processor Assignment Skipped (dry-run)",
            "severity": "info",
            "details": (
                f"dry_run enabled — would PATCH milestone {milestone_id[:8]} with "
                f"loanAssociate=User/{DEFAULT_PROCESSOR_USER_ID} ({DEFAULT_PROCESSOR_NAME}). "
                f"Current associate: {associate or '(none)'}."
            ),
            "suggestion": "Disable dry_run to perform the assignment.",
            "resolved": True,
            "timestamp": now(),
        })
    else:
        patch_result = update_loan_milestone(
            loan_id, milestone_id,
            {"loanAssociate": {"loanAssociateType": "User", "id": DEFAULT_PROCESSOR_USER_ID}},
            state=state,
        )
        if patch_result.get("success"):
            assignment_performed = True
            flags.append({
                "substep": "14.3",
                "title": "Loan Processor Assigned",
                "severity": "info-overwrite",
                "details": (
                    f"{MILESTONE_NAME} milestone loanAssociate set to "
                    f"{DEFAULT_PROCESSOR_NAME} (User id {DEFAULT_PROCESSOR_USER_ID!r}); "
                    f"previous associate: {associate or '(none)'}."
                ),
                "suggestion": "Confirm the assignment on the In Processing/Submitted worksheet.",
                "resolved": True,
                "timestamp": now(),
            })
        else:
            loan_locked = bool(patch_result.get("locked"))
            no_permission = patch_result.get("status_code") == 403
            flags.append({
                "substep": "14.3",
                "title": (
                    "Loan Locked — Milestone Writes Skipped" if loan_locked
                    else "API User Lacks Milestone Association Permission" if no_permission
                    else "Loan Processor Assignment Failed"
                ),
                "severity": "warning",
                "details": (
                    "The loan is open/locked in Encompass (409 EBS-4360) — processor "
                    "assignment could not be saved. Close the loan in the EC UI and re-run."
                    if loan_locked else
                    "Encompass returned 403 'User does not have permission to associate loan "
                    "team member' — the API user's persona needs the loan-team-member "
                    "association permission before this substep can assign the processor."
                    if no_permission else
                    f"Milestone PATCH failed: {patch_result.get('error')}"
                ),
                "suggestion": (
                    "Close the loan in Encompass, then re-run this substep." if loan_locked
                    else "Grant the API user's Encompass persona the associate/team-member "
                         "permission (or assign the processor manually)." if no_permission
                    else "Assign the Loan Processor manually on the milestone worksheet."
                ),
                "resolved": False,
                "timestamp": now(),
            })

    # ── Missing required fields (finish-attempt probe) ─────────────────────
    # The admin's per-milestone required-fields config is not readable with our
    # API credentials (v3 settings/milestones + businessRules → 403), so the
    # only source of the EC UI's "Missing Required Fields" list is the
    # server-side validation on action=finish.
    missing_required: list[dict] = []
    probe_ran = False
    if FINISH_MODE == "probe" and not done_indicator and not dry_run and not loan_locked:
        probe_ran = True
        finish_result = update_loan_milestone(
            loan_id, milestone_id, {}, state=state, action="finish",
        )
        if finish_result.get("success"):
            # Nothing missing — but auto-finish is held, so re-open immediately.
            update_loan_milestone(loan_id, milestone_id, {}, state=state, action="unfinish")
            flags.append({
                "substep": "14.3",
                "title": "Milestone Required Fields All Satisfied",
                "severity": "info",
                "details": (
                    f"A finish-attempt probe on {MILESTONE_NAME} passed validation — no "
                    "missing required fields. The milestone was immediately re-opened "
                    "(auto-finish is held pending processor confirmation)."
                ),
                "suggestion": "Check the Finished box on the worksheet when ready.",
                "resolved": True,
                "timestamp": now(),
            })
        elif finish_result.get("locked"):
            loan_locked = True
            flags.append({
                "substep": "14.3",
                "title": "Loan Locked — Required-Fields Check Skipped",
                "severity": "warning",
                "details": (
                    "The loan is open/locked in Encompass — the missing-required-fields "
                    "probe could not run. Close the loan in the EC UI and re-run."
                ),
                "suggestion": "Close the loan in Encompass, then re-run this substep.",
                "resolved": False,
                "timestamp": now(),
            })
        elif finish_result.get("status_code") == 403:
            # Permission failure, NOT a required-fields validation — don't
            # mislabel it. Confirmed on prod 2026-07-23: "User not allowed to
            # finish milestone." (missing 'Finish Milestones' persona right).
            flags.append({
                "substep": "14.3",
                "title": "API User Lacks Finish-Milestone Permission",
                "severity": "warning",
                "details": (
                    "Encompass returned 403 'User not allowed to finish milestone' — the "
                    "missing-required-fields probe needs the API user's persona to have the "
                    "Finish Milestones permission. Until granted, the Go To Fields list "
                    "cannot be collected via API."
                ),
                "suggestion": (
                    "Grant the API user's Encompass persona the Finish Milestones permission, "
                    "or review the Missing Required Fields dialog manually in the EC UI."
                ),
                "resolved": False,
                "timestamp": now(),
            })
        elif finish_result.get("status_code") != 400:
            flags.append({
                "substep": "14.3",
                "title": "Required-Fields Probe Failed",
                "severity": "warning",
                "details": f"Finish-attempt probe failed unexpectedly: {finish_result.get('error')}",
                "suggestion": "Re-run the substep; if it persists, review the milestone in Encompass.",
                "resolved": False,
                "timestamp": now(),
            })
        else:
            error_text = finish_result.get("response_text") or finish_result.get("error") or ""
            missing_required = _parse_missing_fields(error_text)
            for row in missing_required:
                _manual_field(
                    manual_rows, "14.3", row["field_id"],
                    row.get("description") or f"Field {row['field_id']}",
                    current_value=None,
                    reason="Milestone required field is blank — must be filled before the milestone can be finished",
                )
            detail_list = "\n".join(
                f"- {r['field_id']}" + (f": {r['description']}" if r.get("description") else "")
                for r in missing_required
            ) or error_text[:800]
            flags.append({
                "substep": "14.3",
                "title": f"Milestone Required Fields Missing — {len(missing_required) or '?'} field(s)",
                "severity": "warning",
                "details": (
                    f"Finishing {MILESTONE_NAME} was rejected by Encompass because required "
                    f"fields are blank (same list as the EC UI Go To Fields dialog):\n{detail_list}"
                ),
                "suggestion": "Fill the listed fields (in the dashboard Field Writes tab or Encompass), then finish the milestone.",
                "resolved": False,
                "timestamp": now(),
            })

    # ── Finished checkbox — HELD (surface only, never left set) ────────────
    flags.append({
        "substep": "14.3",
        "title": "Milestone Finish Pending Manual Confirmation",
        "severity": "info",
        "details": (
            f"{MILESTONE_NAME} doneIndicator (the worksheet's Finished checkbox) is currently "
            f"{'CHECKED' if done_indicator else 'UNCHECKED'}. Auto-finishing is held pending "
            "processor confirmation — Encompass enforces the milestone's required fields when "
            "the box is checked."
        ),
        "suggestion": (
            "Check the Finished box on the In Processing/Submitted worksheet after verifying "
            "required fields." if not done_indicator else "Already finished — no action needed."
        ),
        "resolved": done_indicator,
        "timestamp": now(),
    })

    result = {
        "success": True,
        "substep": "14.3",
        "tool": "update_processing_submitted",
        "milestone_id": milestone_id,
        "processor_assigned": assignment_performed,
        "processor_already_assigned": has_processor,
        "done_indicator": done_indicator,
        "loan_locked": loan_locked,
        "required_fields_probe_ran": probe_ran,
        "missing_required_fields": missing_required,
        "manual_fields_registered": [row["field_id"] for row in manual_rows],
        "flags_count": len(flags),
        "message": (
            f"{MILESTONE_NAME}: "
            + ("assigned Loan Processor " + DEFAULT_PROCESSOR_NAME if assignment_performed
               else f"processor already assigned ({associate.get('name')})" if has_processor
               else "loan locked — writes skipped" if loan_locked
               else "processor assignment not performed")
            + f"; Finished={'checked' if done_indicator else 'unchecked (manual)'}"
            + (f"; {len(missing_required)} missing required field(s)" if missing_required else "")
        ),
    }

    logger.info(f"[UPDATE_PROCESSING_SUBMITTED] {result['message']}")

    update: dict = {
        "messages": [ToolMessage(content=json.dumps(result), tool_call_id=tool_call_id)],
        "flags": flags,
    }
    if manual_rows:
        update["manual_fields"] = manual_rows

    return Command(update=update)
