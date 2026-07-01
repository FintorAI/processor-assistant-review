# Processor Checklist — Scope & Implementation Status

**Date:** 2026-06-30
**Source checklist:** AWM / Matrix Division "Loan Processor Checklist" (24 sections, `output/_checklist_p1-3.png`)
**Item-level mapping:** [`processor_checklist_code_reality.csv`](./processor_checklist_code_reality.csv)
**Per-repo code reality:** [`video2_implementation_audit.md`](./video2_implementation_audit.md) + the five `video2_*_gaps.md` docs

This document summarizes how the AWM Loan Processor Checklist maps onto what the
processor-assistant multi-agent system actually does in code today, and where the
checklist runs past the assistant's intended scope.

> **Status taxonomy:** a row tagged "Ordering blocked by unavailable credentials"
> is counted as **Blocked** only (pulled out of its base status), since the code
> exists or is registered but cannot run without external setupIds / credentials —
> it is not the same as unbuilt work.

---

## 1. Scope boundary

The AWM checklist covers the **entire loan lifecycle, intake → funding**. The
processor-assistant workflow (validated with the processor, Ash) is narrower: it
ends at **mark Ready-for-UW + flip the Processing milestone to Submitted**
(checklist **section 17**).

Everything in **sections 18–24** sits *past* that submission boundary and is owned
downstream by **underwriters, the disclosure / CD desk, closers, and funders** —
not by the processor at submission time.

| Scope | Items | ✅ Impl | 🟡 Partial | ❌ Not Impl | ⛔ Blocked |
|---|---:|---:|---:|---:|---:|
| **Sections 01–17** — submission prep (in scope) | 162 | 23 | 51 | 86 | 2 |
| **Sections 18–24** — post-UW / closing (likely out of scope) | 34 | 1 | 6 | 27 | 0 |

---

## 2. Overall status (196 items)

| Status | Count | % |
|---|---:|---:|
| ✅ Implemented | 23 | 12% |
| 🟡 Partial | 51 | 26% |
| ❌ Not Implemented | 104 | 53% |
| ⛔ Blocked (credentials / setupIds) | 18 | 9% |

---

## 3. Status per agent

A row counts toward **each** agent it lists, so the agent totals sum to more than 196.

| Agent | Total | ✅ Impl | 🟡 Partial | ❌ Not Impl | ⛔ Blocked |
|---|---:|---:|---:|---:|---:|
| **review** | 70 | 19 | 41 | 9 | 1 |
| **integrations** | 23 | 0 | 1 | 6 | 16 |
| **document-management** | 13 | 2 | 7 | 3 | 1 |
| **communications** | 11 | 4 | 4 | 3 | 0 |
| **computer-use** | 3 | 0 | 1 | 2 | 0 |
| **none** (manual / no agent) | 87 | 0 | 0 | 85 | 2 |

**Takeaways:**
- **review** is the implementation workhorse — 19 of 23 implemented items, plus 41 partial.
- **integrations** is almost entirely **Blocked** (16 of 23): order_appraisal, AUS (DU/LP),
  Ocrolus, Xactus, etc. are coded or registered but waiting on setupIds / credentials.
- **87 items have no agent at all** — and 24 of those are the post-submission steps below.

---

## 4. Possibly out of scope (to confirm with Ash)

**24 items** are explicitly flagged "Aren't these post-submission to UW? To confirm".
All 24 are currently `Not Implemented` and span six sections:

| Section | Items flagged |
|---|---:|
| 18 Change of Circumstance | 4 |
| 19 Loan Approval & Conditions | 7 (of 11) |
| 20 CD Request & Approval | 6 (of 7) |
| 22 Docs Stage | 3 |
| 23 Funding | 3 |
| 24 Notice of Action Taken | 1 |

### Straddle-the-boundary items (the extra 10 in sections 18–24)

These were **not** flagged because the assistant already has partial coverage; they
need a specific ruling on whether the processor owns them pre- or post-UW:

| Item | Behavior | Status |
|---|---|---|
| 19.2 | Review Lock Confirmation; qualifying rate vs Transmittal | 🟡 Partial |
| 19.3 | Update Encompass via Polly (property/value/DSCR) | ❌ Not Impl |
| 19.4 | File Contacts / AKAs | 🟡 Partial |
| 19.5 | Order LDP/GSA via Processor Workflow | ⛔ Blocked |
| 20.3 | Verify 2015 Itemization screen (CTC) | 🟡 Partial |
| 21.1 | Upload conditions to eFolder; mark Ready-for-UW | 🟡 Partial |
| 21.2 | Attach supporting docs to conditions in Encompass | ❌ Not Impl |
| 21.3 | Upload Conditions Cover Sheet / add comments | ❌ Not Impl |
| 21.4 | Enter signing date and wire request | 🟡 Partial |
| 21.5 | Upload docs to be signed at closing (LOEs, tax pages) | 🟡 Partial |

### Recommendation

1. Ask Ash a single boundary question: *"The assistant's scope ends at Submitted-to-UW.
   Sections 18–24 (COC, conditions, CD, docs, funding, NOAT) happen after that. Do
   processors own any of those, or hand off to UW / disclosure desk / closing / funding?"*
2. Get a specific ruling on the 10 straddle items above (pre-submission vs post-UW).
3. If confirmed out of scope, **mark those rows "Out of scope (post-submission)"** rather
   than "Not Implemented", so the metrics reflect only committed scope.

### Effect on the metrics if the 24 flagged items are excluded

| Metric | With all 196 | Excluding 24 out-of-scope (→172) |
|---|---:|---:|
| Not Implemented | 104 (53%) | 80 (47%) |
| Implemented | 23 (12%) | 23 (13%) |

---

## 5. Caveats

- These counts come from the manually reviewed checklist export (status corrections +
  scope questions applied). The committed [`processor_checklist_code_reality.csv`](./processor_checklist_code_reality.csv)
  carries the same item-level mapping minus the `Questions` column.
- The **review** rows were verified against current code (the repo received a large
  2026-06-29 batch: FHA Management, HUD Transmittal, URLA Lender / manner-held, cover
  letter, EMD, downpayment / gift, retirement haircut, processor workflow & closing).
- The **integrations / communications / document-management / computer-use** rows rest on
  the late-May multi-repo audit; those repos have had no major commits since, but their
  code was not fully re-read in this pass.
- The older `video2_*_gaps.md` audit docs are now **stale for the review repo** (they still
  list cover letter, ethnicity, FHA Management, HUD Transmittal, and Transmittal field-writes
  as stubs/missing — all since implemented).
