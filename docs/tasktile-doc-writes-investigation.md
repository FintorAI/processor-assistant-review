# Doc-write failures — thread investigation + TaskTile gaps (this repo)

Applies the [`tasktile-field-gaps-handoff.md`](tasktile-field-gaps-handoff.md) method to
`processor-assistant-review`, for the two reported doc-write failures:

- **A. Co-borrower Government ID not written to the form** (both IDs are usually two
  attachments in the same eFolder bucket).
- **B. File Contacts → Escrow Company** (company + Escrow Case # / "File #") **not populated.**

The question we were asked to answer first: **is each an extraction issue, a write issue, or an
agent issue?**

---

## TL;DR verdict

| Issue | Root cause | Why |
|---|---|---|
| **A. Co-borrower Govt ID** | **EXTRACTION** — pinned to a config/pipeline bug (write logic is correct but starved) | Both ID attachments are extracted, but the DL doc is processed as `best`/`is_multi_copy=False`, so the co-borrower's value collapses into one field (no `copies[]`). Root cause: the fallback branch of `get_required_documents_for_loan()` returns `{}` for `extraction_mode`, killing the `'all'` config. **Live-confirmed** through Sep 2026: 0/21 co-borrower loans wrote the co-borrower ID; `copies[]` present 0/21. |
| **B. Escrow Company + Case #** | **EXTRACTION** (write logic is correct) | The ESS settlement-agent contact block **never** reaches `doc_fields`: `contact_settlement_agent_*` appeared in **0/87** threads. Escrow only ever populates through a fragile LandingAI **download-bypass** (`shared/ess_contact_bypass.py`) — worked on **9/87**, and in all 9 the Case # came through too. When the bypass is skipped/fails, both are blank. |

Neither is a write bug and neither is an "agent didn't call the tool" bug. **Both are upstream
extraction gaps** — exactly the class of problem moving to TaskTile (SBIQ `content_schema`) with
LandingAI as fallback is meant to fix.

---

## How this was investigated

1. **Offline thread triage** — `scripts/analyze_doc_issues.py` over the 87 threads (with doc
   state) in `scripts/migration_out/cloud_threads.ndjson`. For each thread it classifies both
   issues into EXTRACTION / WRITE / AGENT by inspecting `doc_fields`, `efolder_documents`,
   `file_contacts`, and `field_writes_ledger`.
   ```bash
   python3 scripts/analyze_doc_issues.py            # summary
   python3 scripts/analyze_doc_issues.py --verbose  # per-thread
   ```
2. **SBIQ `content_schema` probe** — the handoff's insight ("in `content_schema`" == "in the
   manifest metadata, free to the agent"). Queried the SBIQ API directly (no auth, GET only):
   ```bash
   BASE=https://sbiqai.staging.cybersoftbpo.ai
   curl -s "$BASE/api/categories"                       # id ↔ name
   curl -s "$BASE/api/categories/2168?fields=content_schema"   # ALTA Settlement Statement
   curl -s "$BASE/api/categories/323?fields=content_schema"    # Drivers License
   ```

### Investigation results (87 threads)

```
A) Co-borrower Government ID (5054/5056)
   70  N/A (no co-borrower)
   15  EXTRACTION (single/none DL id)   ← every co-borrower loan that should have written it
    2  OK written
   → of the 30 threads whose DL bucket had ≥2 attachments, 30/30 lost the 2nd ID.

B) File Contacts — Escrow Company (from ESS)
   78  EXTRACTION (no ESS contact data reached state)
    9  OK (company + Escrow Case # both populated, via the LandingAI bypass)
   → contact_settlement_agent_* present in doc_fields: 0/87
```

### Live confirmation (Issue A) — current deployment, through Sept 2026

Re-ran against the live deployment
(`https://processor-assistant-review-9f1dbaa4d0ab59ec81d9c9d0b4971df8.us.langgraph.app`) with
`scripts/probe_live_govt_id.py` — the failure is **current**, not a stale-export artifact:

```
Threads scanned: 120  |  with co-borrower: 21
  10  EXTRACTION_single_or_none
   9  EXTRACTION_multi_attach_single_id   ← DL bucket had 2–4 attachments, only 1 ID reached state
   2  OK_written                          ← same loan, value pre-existing in Encompass (distinct_ids=1)
copies[] present on dl_gov_id: 0/21       ← multi-copy path never fires
```
Most recent examples (Sep 11 / Sep 1, 2026): loans `2607974371`, `2608976041` — `ef_copy_count=2`,
`dl_copies_list=None`, `distinct_ids=1`, `cob_written=False`. **0/21 co-borrower loans wrote the
co-borrower Govt ID from fresh extraction.**

### Root cause pinned to a specific bug

Dumping one failing thread's DL detail (loan 2607974371):
```
efolder DL: copy_count=2  extraction_mode=best  is_multi_copy=False   ← should be 'all' / True
  copy_index=0 status=completed title='ID Customer Identification Documentation'
  copy_index=1 status=completed title='ID Customer Identification Documentation'
doc_fields dl_gov_id: value='10273321229' copies=None all_sources=["Driver's License","Driver's License"]
```
Both ID attachments **are** extracted and both normalise into the same `dl_*` keys — but because
the doc is treated as **`best` / `is_multi_copy=False`**, the second (co-borrower) value collapses
into the single field instead of creating a `copies[]` list. `_id_copies()` then sees one record →
only the borrower is written.

**Why `best` when the config says `all`?** `output/config/required_docs_conditions.json` has a
**single** `conditions` entry, `{"fallback": true}`, whose `extraction_mode` map *does* set
`Driver's License: 'all'` (17 types total). But `get_required_documents_for_loan()`
(`output/tools/data_gathering.py`) discards it in the fallback branch:
```python
for entry in conditions:
    cond = entry.get("condition", {})
    if cond.get("fallback"):
        doc_list = entry.get("document_list", [])
        return doc_list, {}          # ← BUG: returns {} instead of entry["extraction_mode"]
```
Every real loan hits this branch → `extraction_modes = {}` → **every** doc defaults to `best`.
So the `'all'` config is dead for all 17 multi-copy types (Driver's License, VOE, Bank Statement,
Paystubs, W-2, …), not just IDs.

**Two fixes, independent:**
- **Immediate (this repo) — ✅ APPLIED.** `get_required_documents_for_loan()` fallback branch now
  returns the entry's `extraction_mode` (minus `_comment`) instead of `{}`, re-enabling the
  existing multi-copy write path for all 17 configured types (Driver's License, VOE, Bank
  Statement, Paystubs, W-2, …). Verified: `Conventional/Purchase/2` now yields 17 multi-copy types
  and `Driver's License → all`. (Impact: turns on `selectionMode=All` for those types → more
  extraction volume/latency — a deliberate switch-on.)
- **Strategic (TaskTile):** per-attachment classification + the schema-323 `idNumber` add makes
  both IDs available regardless of the copy-mode plumbing.

> **Full scope + field-level TaskTile gaps for all 45 doc types:** see
> [`tasktile-field-gaps-tasktile-side.md`](tasktile-field-gaps-tasktile-side.md) (the SBIQ-team
> handoff). Key correction from field-level probing: SBIQ `content_schema` for **most** doc types
> is **classification-only** (`borrowers[]` names + `signed`/`dateSigned` + `propertyAddress`), so
> a high field *count* does **not** mean the data we need is present. Only a handful are genuinely
> serviceable today (SSN Card, Credit Report, the ID docs minus the number, Property Tax); the
> rest need SBIQ field additions before TaskTile can serve them.

---

## A. Co-borrower Government ID — the extraction gap

Write path (correct, no change needed): `output/tools/review_borrower_summary.py`
`_id_copies()` → `_match_copy_to_person()` → `_write_government_id()` writes borrower
**5053/5055** and co-borrower **5054/5056**, mapping each DL copy to the right person by name.
It only writes when Encompass is blank. It works — when it's given two ID records.

Two extraction-layer reasons it's starved today:

1. **One ID per bucket.** Even with `extraction_mode: all` on `Driver's License`, the co-borrower
   copy's structured `dl_gov_id` isn't reaching `doc_fields` (30/30 multi-attachment threads).
2. **The number field doesn't exist in SBIQ.** `Drivers License` (**323**) `content_schema` is:
   ```
   owner{DOB, firstName, middleName, lastName, suffix}, state, expirationDate
   ```
   **No ID/license number.** Same for Passport (845), Permanent Resident Card (844). So even after
   moving to TaskTile, the DL/ID *number* (Encompass 5053/5054) can't be emitted until the schema
   gains it.

**Why TaskTile fixes the co-borrower multiplicity:** TaskTile classifies and extracts **each
attachment separately** and emits each as its own manifest entry/metadata. Two ID PDFs in one
bucket → two extracted `owner{}` blocks → both borrower and co-borrower IDs available, instead of
one "best" doc per bucket. Combined with the schema add below, this closes Issue A.

## B. Escrow Company + Case # — the extraction gap

Write path (correct, no change needed): `output/tools/review_file_contacts.py` maps ESS
`contact_settlement_agent_*` → the `ESCROW_COMPANY` file contact, incl. `file_number →
referenceNumber` (the "Escrow Case #", same as Encompass field 186). When the data exists it
writes cleanly (9/9 that had a company name also got the Case #).

The gap, stated by the code itself (`shared/ess_contact_bypass.py` docstring): the ESS page-5
Contact Information table **"never reaches `state['doc_fields']` through the normal pipeline."**
Confirmed: `contact_settlement_agent_*` = 0/87. Escrow only appears via the LandingAI
download-bypass, which needs `LANDINGAI_API_KEY`, grabs only the **first** attachment in the
bucket, must get a real PDF, and has to *infer* the "File #" from a hand-injected extra field.

**SBIQ confirms why:** `ALTA Settlement Statement` (**2168**) `content_schema` is only:
```
date, buyers[]{name...}, sellers[]{name...}, propertyAddress{...}
```
**No escrow_company, no settlement_agent, no file/case number, no title/recording charges.**
`Settlement Statement` (1036), `Closing Disclosure` (819) and even `Title Escrow Contact
Information` (497) carry only borrower name/signature fields. So TaskTile can't emit any of it
today → the agent is forced onto the LandingAI fallback → frequent blanks.

---

## Deliverable — SBIQ `content_schema` additions

Add to the category JSON in the **sbiq-ai** repo (`src/utils/categories/{id}.json`,
`content_schema`), then re-map on our side (below).

| Doc (SBIQ id) | Fields to ADD | Unblocks (our consumer) |
|---|---|---|
| **ALTA Settlement Statement (2168)** | `escrow_company`, `settlement_agent{ name, contact, phone, email, address, st_license_id (company license), contact_st_license_id (individual license) }`, `file_number` (the "File #" → Escrow Case #) | `review_file_contacts.py` (1.2) `ESCROW_COMPANY` incl. `referenceNumber`/field 186. Also `title_company`, `title_charges[]`, `recording_charges[]` per the base handoff. |
| **Drivers License (323)** | `owner.idNumber` (license/ID number); optional `owner.idType` | `review_borrower_summary.py` (2.1) `_write_government_id` → 5053/5054 (+5055/5056) for **both** borrowers |
| **Passport (845)** | `owner.idNumber` (passport number) | same (ID fallback docs) |
| **Permanent Resident Card (844)** | `resident.idNumber` (A#/USCIS #) | same |

These field names deliberately match the keys our tools already read
(`_ESS_SUBKEY_TO_FIELD` in `review_file_contacts.py`; `dl_gov_id` in `review_borrower_summary.py`),
so no write-side logic changes are needed.

## Our-side follow-up once SBIQ adds them

1. Route these doc types' extraction through **TaskTile first, LandingAI as fallback** (the
   inverse of today, where LandingA/CatchingDoc is primary and the ESS bypass is the crutch).
2. Map the new manifest metadata into `doc_fields` (the `contact_settlement_agent_*` and
   `dl_gov_id`/per-`owner` keys the tools already consume).
3. Keep `shared/ess_contact_bypass.py` as the LandingAI fallback only; retire it once 2168
   populates reliably (verify with `scripts/analyze_doc_issues.py`).
4. For Issue A, rely on TaskTile's per-attachment extraction so both IDs arrive as separate
   `owner{}` blocks; `_match_copy_to_person` then assigns each to the right borrower.

## Already covered by SBIQ — **no schema ask, our-side mapping only**

These return the data we need today; they only need our config mapping (TaskTile-first, drop the
LandingAI pass). Not part of the SBIQ handoff.

| Doc (SBIQ id) | Why it's ready |
|---|---|
| **SSN Card (843)** | `owner.SSN` + names — complete |
| **Credit Report (117)** | applicant1/2 names + `last4SSN`, `scores[]`, `aliasesAndAddresses` (AKAs), `employer`, `reportIssued` (order date) — only the two P3 gaps remain |
| **Drivers License / Passport / PRC (323/845/844)** | names + DOB + expiration ready **now** (ID *number* is the P1 SBIQ add) |
| **Property Tax (1481)** | `year`, `county`, `propertyAddress`, `taxInstallments` + 4 installments — annual = **sum the installments** on our side; optional `parcel_number` |
| **Notice of Right to Appraisal (277)** | `borrowers[].signed` + `borrowers[].dateSigned` cover the signature dates we check |
| **Initial 1003 / URLA (349)** | 444-field full URLA — richest schema, covers all our Initial-1003 needs |
| **Tax Returns – Business (1)** | `corporation.name` = our `business_name` |

## Integration strategy / sequence (our roadmap)

**TaskTile-first + LandingAI-fallback, uniformly** — not category-by-category. TaskTile metadata
is free once a doc is classified, so mapping it never hurts and removes a LandingAI call. But per
the field-level reality, **LandingAI must remain primary for every Priority-2 doc until SBIQ adds
the fields** — today those schemas only classify. Sequence:

1. **Ship the P1 SBIQ adds** (2168 contacts + `file_number`; 323/845/844 `idNumber`) — unblocks the
   two reported bugs.
2. **Map the "already covered" docs** to TaskTile now (SSN Card, Credit Report, ID
   names/DOB/expiry, Property Tax, Right-to-Appraisal, URLA, Business Tax) — immediate
   LandingAI-call reduction.
3. **Work P2 by ROI** (Purchase Contract, Transmittals, MI, Flood) — each add moves a doc from
   LandingAI to free TaskTile metadata.
4. Re-verify each with `scripts/probe_sbiq_fieldlevel.py` (schema present) and a fresh manifest
   (actually populates — schema-present-but-empty is still a gap).

## Verify each addition

1. Add field → deploy to SBIQ `staging`.
2. In schema: `curl -s "$BASE/api/categories/{id}?fields=content_schema"` — new path present.
3. Populates: reclassify a real doc, inspect the manifest metadata (schema-present but
   always-empty is still a gap).
4. On our side it then flows once mapped into `doc_fields`; re-run
   `scripts/analyze_doc_issues.py` and confirm the EXTRACTION buckets drop to ~0.
