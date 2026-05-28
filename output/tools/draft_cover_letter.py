"""draft_cover_letter — Tool for substep 7.1: Draft Cover Letter / Submission Notes

Step 7 (STEP_07): Cover Letter
Phase: FORM_UPDATES

# FACTORY-LOCK: true
"""

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Optional

from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.prebuilt import InjectedState
from langgraph.types import Command

from ._helpers import _los, _write_fields

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

logger = logging.getLogger(__name__)


@tool
def draft_cover_letter(
    tool_call_id: Annotated[str, InjectedToolCallId],
    state: Annotated[dict, InjectedState],
) -> Command:
    """Copy Almas' LOA notes into CX.KM.SUBMISSION.NOTES (Cover Letter field).

    Reads almas_notes from state (passed via additional_info at invocation).
    Writes directly to CX.KM.SUBMISSION.NOTES.
    Flags warning if no notes were provided.

    Call this tool during STEP_07 (Cover Letter) as substep 7.1.
    """
    loan_id = state.get("loan_id")
    if not loan_id:
        return Command(update={"messages": [ToolMessage(
            content=json.dumps({"error": "No loan_id in state. Run find_loan first."}),
            tool_call_id=tool_call_id,
        )]})

    logger.info(f"[DRAFT_COVER_LETTER] Starting for loan {str(loan_id)[:8]}...")

    flags = []

    # ── Read Almas notes from state ──
    almas_notes = (
        (state.get("additional_info") or {}).get("almas_notes")
        or state.get("almas_notes")
        or ""
    )
    almas_notes = str(almas_notes).strip()

    # ── Rule: Copy Almas notes → CX.KM.SUBMISSION.NOTES ──
    if almas_notes:
        field_updates = {"CX.KM.SUBMISSION.NOTES": almas_notes}
        write_flags = _write_fields(loan_id, field_updates, "7.1", state=state)
        flags.extend(write_flags)
        flags.append({
            "substep": "7.1",
            "title": "Cover Letter — Submission Notes Written",
            "severity": "info-overwrite",
            "details": (
                f"Almas' notes copied to CX.KM.SUBMISSION.NOTES "
                f"({len(almas_notes)} characters)."
            ),
            "suggestion": (
                "Review the submission notes and remove any sections not applicable "
                "to this loan (e.g. Title Company, Appraisal, Additional Notes "
                "if pre-populated by Almas)."
            ),
            "resolved": True,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        logger.info(f"[DRAFT_COVER_LETTER] Wrote {len(almas_notes)} chars to CX.KM.SUBMISSION.NOTES")
    else:
        flags.append({
            "substep": "7.1",
            "title": "Cover Letter — Almas Notes Missing",
            "severity": "warning",
            "details": "No Almas notes found in state. CX.KM.SUBMISSION.NOTES was not populated.",
            "suggestion": "Pass almas_notes in additional_info when invoking the agent.",
            "resolved": False,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        logger.warning("[DRAFT_COVER_LETTER] almas_notes not in state — skipping field write")

    # ── Build result ──
    result = {
        "success": True,
        "substep": "7.1",
        "tool": "draft_cover_letter",
        "almas_notes_length": len(almas_notes),
        "flags_count": len(flags),
        "message": (
            f"Cover Letter step completed — "
            + (f"notes written ({len(almas_notes)} chars)" if almas_notes else "no notes provided")
            + (f"; {len(flags)} flag(s)" if flags else "")
        ),
    }

    logger.info(f"[DRAFT_COVER_LETTER] {result['message']}")

    update = {
        "messages": [ToolMessage(content=json.dumps(result), tool_call_id=tool_call_id)],
    }
    if flags:
        update["flags"] = flags

    return Command(update=update)
