"""draft_cover_letter — Tool for substep 9.1: Draft Cover Letter / Submission Notes

Step 9 (STEP_09): Cover Letter
Phase: FORM_UPDATES

# FACTORY-LOCK: true
"""

import json
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.prebuilt import InjectedState
from langgraph.types import Command

from ._helpers import _los, _write_fields, _efolder_present

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

logger = logging.getLogger(__name__)

# Almas' notes are organized into emoji-headed sections. Per processor feedback
# (Video 5), the UW cover letter must drop the File Summary, Team Contacts,
# Appraisal, and Additional Notes sections entirely.

# Recognized section headers, matched emoji/whitespace-insensitively (lowercased,
# leading non-letters and a trailing ':' removed).
_SECTION_HEADERS = {
    "file summary",
    "employment & income", "employment and income",
    "assets",
    "team contacts",
    "appraisal",
    "additional notes",
}
# Sections removed wholesale (header + all content until the next section).
_DROP_SECTIONS = {"file summary", "team contacts", "appraisal", "additional notes"}
# Section header lines removed from the output (the dropped sections plus the
# Employment & Income label, which is noise once its sub-lines remain). The Assets
# header is kept as a visual divider.
_DROP_HEADER = _DROP_SECTIONS | {"employment & income", "employment and income"}
# Underwriting-relevant File Summary lines kept even though the section is dropped.
_SECTION_KEEP = {"file summary": ("aus findings", "loan program")}
# Boilerplate identifier lines UW does not need (matched as a case-insensitive
# prefix) inside the sections we keep.
_STRIP_PREFIXES = (
    "client name",
    "property address",
    "closing date",
    "borrower(s) on loan:",
    "borrower(s) on title:",
    "voe contact email:",
    "need business return",
    "use this section",
)


def _norm_header(line: str) -> str:
    """Normalize a line to its section-header form (drop leading emoji + trailing ':')."""
    s = re.sub(r"^[^0-9A-Za-z]+", "", line.strip()).strip().lower()
    return s.rstrip(":").strip()


def _strip_boilerplate(notes: str) -> str:
    """Clean Almas' email before writing to CX.KM.SUBMISSION.NOTES.

    - Drops the File Summary, Team Contacts, Appraisal, and Additional Notes
      sections entirely (header + content), salvaging AUS Findings / Loan Program
      from File Summary.
    - Drops the section header label for Employment & Income (keeps its content).
    - Drops boilerplate identifier lines (client name, property address, etc.).
    - Collapses consecutive blank lines.
    """
    cleaned: list[str] = []
    current = ""
    dropping = False
    for line in notes.splitlines():
        stripped = line.strip()
        header = _norm_header(line) if stripped else ""
        if header in _SECTION_HEADERS:
            current = header
            dropping = header in _DROP_SECTIONS
            if header in _DROP_HEADER:
                continue
            cleaned.append(line)  # keep the header as a divider (e.g. "💰 Assets")
            continue
        if dropping:
            keep = _SECTION_KEEP.get(current, ())
            if keep and stripped.lower().startswith(keep):
                cleaned.append(line)
            continue
        if any(stripped.lower().startswith(p) for p in _STRIP_PREFIXES):
            continue
        cleaned.append(line)

    # Collapse runs of more than one blank line.
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

    Call this tool during STEP_09 (Cover Letter) as substep 9.1.
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

    # ── Almas-notes images (transcribed in step 0.6) ──
    # These images are uploaded to DocRepo by the frontend (not the eFolder) and
    # OCR'd by extract_almas_images. Per processor feedback we do NOT append the
    # OCR'd transcription to the cover letter (it is boilerplate / contact info that
    # belongs in File Contacts, handled by review_file_contacts). We still surface
    # each image as a DocRepo coordinate reference on the 8.1 flag for traceability.
    almas_images = (
        state.get("almas_notes_images")
        or (state.get("additional_info") or {}).get("almas_notes_images")
        or []
    )
    image_refs: list[dict] = []
    for idx, img in enumerate(almas_images):
        if not isinstance(img, dict):
            continue
        label = img.get("filename") or f"Almas Notes Image {idx + 1}"
        doc_id = img.get("doc_id") or img.get("docrepo_location") or ""
        url = img.get("url") or img.get("signed_url") or img.get("s3_url") or ""
        if doc_id or url:
            image_refs.append({
                "doc_type": label,
                "client_id": img.get("client_id", ""),
                "doc_id": doc_id,
                "bucket": img.get("bucket", ""),
                "attachment_id": "",
                "url": url,
                "source": "almas_notes_image",
                "copies": [],
            })

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
        _write_fields(loan_id, field_updates, "9.1", flags, state=state)
        missing_summary = (
            f" Appended 'Documents still needed:' with {len(missing_docs)} item(s): "
            + ", ".join(missing_docs) + "."
            if missing_docs else " No missing docs appended (all present in eFolder)."
        )
        notes_flag = {
            "substep": "9.1",
            "title": "Cover Letter — Submission Notes Written",
            "severity": "info-overwrite",
            "details": (
                f"Almas' notes copied to CX.KM.SUBMISSION.NOTES "
                f"({len(almas_notes)} characters), with the File Summary, Team "
                f"Contacts, Appraisal, and Additional Notes sections removed."
                f"{missing_summary}"
            ),
            "suggestion": (
                "Review the submission notes for accuracy before submitting to UW."
            ),
            "resolved": True,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        if image_refs:
            notes_flag["relevant_documents"] = image_refs
        flags.append(notes_flag)
        logger.info(f"[DRAFT_COVER_LETTER] Wrote {len(almas_notes)} chars to CX.KM.SUBMISSION.NOTES")
    else:
        flags.append({
            "substep": "9.1",
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
        "substep": "9.1",
        "tool": "draft_cover_letter",
        "almas_notes_length": len(almas_notes),
        "flags_count": len(flags),
        "message": (
            "Cover Letter step completed — "
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
