## Purpose

Populate CX.KM.SUBMISSION.NOTES (Cover Letter / Submission Notes field). LLM pre-drafts the notes based on Almas' email/notes and the loan profile. Also clears pre-populated Title Company, Appraisal, and Additional Notes fields. No interrupt here — raise a flag with the draft text for processor review in the UI.


**⚠️ This step may WRITE to Encompass.** Substeps that write are marked below.

**NOTE**: Each substep has its own dedicated tool. State (`los_fields`, `doc_fields`, `loan_summary`) is automatically injected.

## Available Tools

| Tool | Purpose |
|------|---------|
| `draft_cover_letter` | Draft Cover Letter / Submission Notes |

## Overview

| Substep | Description | Tool |
|---------|-------------|------|
| 8.1 | Draft Cover Letter / Submission Notes | `draft_cover_letter` |

## Tool Calls

```python
# Substep 8.1 - Draft Cover Letter / Submission Notes
# ⚠️ This substep WRITES to Encompass
draft_cover_letter(loan_guid=loan_id)
```

---

## Substeps

### Substep 8.1 - Draft Cover Letter / Submission Notes
**Tool**: `draft_cover_letter`

Populate CX.KM.SUBMISSION.NOTES using Almas' notes from state["almas_notes"] and loan profile data (AMI eligibility, missing docs, etc.). Clear pre-populated Title Company, Appraisal, and Additional Notes sub-fields. Raise a flag with the draft text so the processor can review and edit it in the UI. Do NOT interrupt here.


**LOS Fields (read from state):**

| Encompass Field | Field ID | Key | Purpose |
|-----------------|----------|-----|---------|
| Submission Notes (Cover Letter) | `CX.KM.SUBMISSION.NOTES` | `submission_notes` | Current value — clear and replace with LLM draft |
| Cover Letter - Title Company (pre-populated) | `CX.KM.CL.TITLE.COMPANY` | `cover_letter_title_company` | Clear this field as per Almas' standard process |
| Cover Letter - Appraisal (pre-populated) | `CX.KM.CL.APPRAISAL` | `cover_letter_appraisal` | Clear this field |
| Cover Letter - Additional Notes (pre-populated) | `CX.KM.CL.ADDITIONAL.NOTES` | `cover_letter_additional_notes` | Clear this field |
| AMI Percentage | `CX.AMI.PERCENTAGE` | `ami_percentage` | Used to add grant program note if <= 50% AMI |
| Mortgage Type | `1172` | `loan_type` | Include in cover letter profile summary |

**Business Rules:**
- **Draft from Almas Notes** (custom): Read state["almas_notes"]. Use as primary source for the submission notes. Augment with loan-derived facts: AMI grant eligibility, missing docs (if any known from Steps 1-6 flags), loan type, and borrower profile highlights.

- **Clear Pre-populated Fields** (custom): Clear Title Company, Appraisal, and Additional Notes sub-fields of CX.KM.SUBMISSION.NOTES as per Almas' standard process (notes.txt:126).


**Flags — raise when conditions are met:**
- INFO: "Cover Letter Draft for Processor Review"
  - Condition: Always raised — processor must review and approve the draft
  - Remedy: Review the cover letter draft in the UI and edit as needed
- WARNING: "Almas Notes Missing"
  - Condition: state["almas_notes"] is empty or None
  - Remedy: Provide Almas' notes/email as agent input before running

**⚠️ Field Updates (writes to Encompass):**
- Field `CX.KM.SUBMISSION.NOTES` = `{llm_drafted_notes}` (when: always)
- Field `CX.KM.CL.TITLE.COMPANY` = `` (when: always)
- Field `CX.KM.CL.APPRAISAL` = `` (when: always)
- Field `CX.KM.CL.ADDITIONAL.NOTES` = `` (when: always)

After completing this substep, call:
```
write_todo(step_id="STEP_08", substep_id="8.1", status="completed", notes="<detailed report with every check result, field IDs/values, and flags>")
```

---

## Step Completion

When ALL substeps above are completed:
1. Call `save_step_report(step_name="STEP_08", status="completed", ...)`
2. Call `write_todo(step_id="STEP_08", status="completed")` to advance to the next step
3. Call `write_todo(step_id="STEP_09", status="in_progress")` to start STEP_09 (Borrower Info - Vesting)

⚠️ You MUST call write_todo to advance — do NOT produce a text-only response.
