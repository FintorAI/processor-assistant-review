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

from ._helpers import _los, _write_fields, _efolder_present

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

logger = logging.getLogger(__name__)

# Lines in Almas' email that UW does not need — stripped before writing to Encompass.
# Match is case-insensitive, prefix-only (up to first newline).
_STRIP_PREFIXES = (
    "client name",
    "property address",
    "closing date",
    "borrower(s) on loan:",
    "borrower(s) on title:",
    "employment & income",
    "voe contact email:",
    "need business return",
    "dependents",
    "assets",          # investment/reserve detail — not relevant for UW cover letter
    "asset ",          # catches "Asset (cash-out)" variants
    "team contacts",
    "appraisal",       # handled separately in "Documents still needed" section
)


def _strip_boilerplate(notes: str) -> str:
    """Remove boilerplate lines from Almas' email before writing to CX.KM.SUBMISSION.NOTES.

    Any line whose stripped content starts with a prefix in _STRIP_PREFIXES is dropped.
    Consecutive blank lines left behind are collapsed to a single blank line.
    """
    cleaned: list[str] = []
    for line in notes.splitlines():
        stripped = line.strip().lower()
        if any(stripped.startswith(p) for p in _STRIP_PREFIXES):
            continue
        cleaned.append(line)

    # Collapse runs of more than one blank line
    result: list[str] = []
    prev_blank = False
    for line in cleaned:
        is_blank = line.strip() == ""
        if is_blank and prev_blank:
            continue
        result.append(line)
        prev_blank = is_blank

    return "\n".join(result).strip()


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
    almas_notes = _strip_boilerplate(str(almas_notes).strip())

    # ── Build "Documents still needed:" appendix ──
    missing_docs: list[str] = []

    # Appraisal — required unless waiver flag set or Appraisal Acknowledgement in eFolder
    appraisal_waiver = (_los(state, "appraisal_waiver") or "").strip().lower()
    has_waiver = appraisal_waiver in ("y", "yes", "true", "1", "waived")
    appraisal_in_efolder = (
        _efolder_present(state, "Appraisal Report")
        or _efolder_present(state, "Appraisal Acknowledgement")
        or _efolder_present(state, "Appraisal Invoice")
    )
    if not has_waiver and not appraisal_in_efolder:
        missing_docs.append("Appraisal")

    # HOI — always required
    if not _efolder_present(state, "Evidence of Insurance"):
        missing_docs.append("HOI (Evidence of Insurance)")

    # Title Report — always required
    if not _efolder_present(state, "Title Report"):
        missing_docs.append("Title Report")

    # Assets (Bank Statement / Retirement) — required when REO properties exist
    reo_props = state.get("reo_properties") or []
    if reo_props:
        has_assets = (
            _efolder_present(state, "Bank Statement")
            or _efolder_present(state, "Assets")
        )
        if not has_assets:
            missing_docs.append("Assets / Bank Statement (reserves for investment property)")

    # Append to notes if any are missing
    if missing_docs:
        docs_section = "\n\nDocuments still needed:\n" + "".join(
            f"- {d}\n" for d in missing_docs
        )
        almas_notes = almas_notes + docs_section if almas_notes else docs_section.strip()

    # ── Rule: Copy Almas notes → CX.KM.SUBMISSION.NOTES ──
    if almas_notes:
        field_updates = {"CX.KM.SUBMISSION.NOTES": almas_notes}
        _write_fields(loan_id, field_updates, "7.1", flags, state=state)
        missing_summary = (
            f" Appended 'Documents still needed:' with {len(missing_docs)} item(s): "
            + ", ".join(missing_docs) + "."
            if missing_docs else " No missing docs appended (all present in eFolder)."
        )
        flags.append({
            "substep": "7.1",
            "title": "Cover Letter — Submission Notes Written",
            "severity": "info-overwrite",
            "details": (
                f"Almas' notes copied to CX.KM.SUBMISSION.NOTES "
                f"({len(almas_notes)} characters).{missing_summary}"
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
