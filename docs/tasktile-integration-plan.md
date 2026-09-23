# TaskTile integration plan (processor-assistant-review)

**Decision:** adopt TaskTile as the document-intelligence source using the **gated,
documents-first** model — the same two-phase pipeline Dashboard-Officer/LOA already runs, but on
TaskTile's **`ai_only`** pipeline (no human-in-the-loop). The processor picks one of two modes per
loan:

- **Loan already ran through LOA** → **pull the existing TaskTile manifest** for the loan; do not
  re-process. (mode B)
- **Loan has NOT run through LOA** → **self-trigger** a TaskTile `ai_only` job for the required
  eFolder docs, then review off the returned manifest. (mode A)

Either way the **review run is gated on the manifest being ready** (self-trigger waits on the
completion webhook), not on dashboard-click time.

> **🚨 Live smoke test 2026-09-23 — `rns_ai_only` does NOT use the AWM `content_schema`.**
> A real end-to-end job (PROD eFolder → TaskTile staging, client `78059b2d-…`) proved the schema
> update is present on the AWM set but **the ai-only pipeline ignores it at extraction time**. The
> added fields (`escrowCompany`, `settlementAgent`, `fileNumber`, co-borrower `idNumber`) therefore
> **do not populate** on the returned manifest. **Schema-present ≠ extracted.** See the full
> [Live smoke test results](#live-smoke-test-results-2026-09-23). This is now the primary open
> blocker with TaskTile.
>
> **✅ Verified 2026-09-23 (staging) — the TaskTile team's two prerequisites:**
> - **Schema update — LIVE on the AWM category set** (`tasktile.staging…/api/category-sets/awm/{id}`),
>   **not** the shared SBIQ base. **23 categories** now differ from base (**all P1/P2/P3 asks**), all
>   keys **camelCase** — see the full [Schema audit](#schema-audit--awm-category-set-verified-2026-09-23).
>   Both **Issue B (Escrow Co + Case #, 2168)** and **Issue A (co-borrower Govt ID, 323/845/844
>   `idNumber`)** are present in the schema — **but the live test shows they are NOT extracted by
>   `rns_ai_only`** (see banner above).
>   **Caveats:**
>   1. **`xpath` is not a blocker** — some added fields carry no `xpath`, but TaskTile confirmed the
>      ai-only pipeline **ignores `xpath`**. (The pipeline also appears to ignore the whole AWM
>      `content_schema` — see smoke test.)
>   2. **camelCase, not snake_case** — the mapper must translate (reuse LOA's config-driven
>      `tasktile_field_map.json`; see open question 0a).
> - **Pipeline name is `rns_ai_only`** (not `ai_only`), confirmed by a real `POST /jobs`. It is a
>   free-form string (there is no `/pipelines` endpoint). `rns_ai_only` has **no HIL** — the test job
>   completed in **~50s**, not the 2-hr Rack & Stack wait.
>
> **Both modes above are now first-class** (not "interim only"). The self-trigger still tapers as
> LOA-first coverage grows — see [Interim workaround vs. target state](#interim-workaround-vs-target-state).

Companion docs:
- [`tasktile-doc-writes-investigation.md`](tasktile-doc-writes-investigation.md) — the two live write
  bugs (co-borrower Govt ID, Escrow/Case #) + already-covered docs + our-side mapping to-dos.
- [`processor-tasktile-field-gaps-tasktile-side.md`](processor-tasktile-field-gaps-tasktile-side.md) —
  SBIQ `content_schema` additions to hand to the TaskTile team.
- LOA reference: `Dashboard-Officer/docs/loan-officer-assistant/TASKTILE-LOAN-ANALYSIS-INTEGRATION.md`.

---

## Interim workaround vs. target state

**Why this whole plan is framed as "processor triggers TaskTile":** it's a bridge for today's
reality, not the intended steady state.

| | Mode A — self-trigger (now) | Mode B — pull existing (LOA-processed) |
|---|---|---|
| **Who runs docs through TaskTile** | The **processor** self-triggers an `ai_only` job for the required docs (Phase 1 below). | The **loan officer** already ran the file through **LOA → Documents** in the dashboard, so a TaskTile manifest exists *before* the loan reaches the processor. |
| **What the processor does** | Upload eFolder PDFs → `POST /jobs` (`ai_only`) → wait on webhook → review. | **Pull the existing manifest** (via the loan's `tasktile_job_id` → `GET /jobs/{id}/manifest`) → review. No upload, no Phase-1 trigger, no wait. |
| **Latency** | `ai_only` (no HIL) → **minutes** (confirm in smoke test), not the old 2-hr Rack & Stack cold start. | Effectively zero — docs were processed upstream, earlier. |

**Why the workaround is needed now:** Ash has been running files through the **processor system
only** — the loans never went through LOA first, so there is **no upstream TaskTile manifest** to
pull. Until officers onboard to running files through LOA in the dashboard (e.g. **Almas** — TBD),
the processor has to trigger TaskTile Express itself.

**Migration path (mode A → mode B):** the branch is the **mode-B pull** — if a completed manifest
already exists for the loan (because LOA ran it), the processor **skips `start_doc_intel` and pulls
the manifest directly**. So as LOA-first coverage grows, self-triggering naturally tapers to zero
without a code change; eventually `start_doc_intel` can be retired for LOA-covered officers.
**Open dependencies:** (1) confirming who is onboarded to LOA-first (Almas + others) determines when
mode B becomes the default; (2) how the processor discovers the loan's existing `tasktile_job_id`
for the pull (see open question 0b).

---

## Why gated (documents-first), not parallel

The processor self-trigger (mode A) uses TaskTile's **`ai_only`** pipeline, which drops the
human-in-the-loop step — so extraction is expected in **minutes**, not the 2+ hrs Rack & Stack
(`rack_and_stack_v1`, what LOA runs) takes. It is still **asynchronous** (submit → webhook), so we
keep the same **gating** LOA uses: the review run starts only once the doc-intel webhook fires,
rather than running LandingAI and TaskTile in parallel and reconciling afterward.

Crucially, the wait costs nothing — during `AWAITING_DOC_INTEL` there is **no worker running**,
just a DynamoDB job row until the webhook arrives. (For loans already processed by LOA — mode B —
there is no wait at all: the manifest already exists and we pull it.)

## How LOA triggers documents → analysis (the model we copy)

1. **Phase 1 (docs):** `startTasktileDocIntelJob` (or the backfill SQS worker) uploads PDFs →
   `POST /jobs` with a `webhook_url`. Job → **`AWAITING_DOC_INTEL`**. Nothing else runs.
2. **TaskTile finishes** → POSTs `{event:"job.completed", status:"success", manifest_url}` to
   `tasktileWebhook`.
3. **Webhook is the trigger** (`tasktileWebhook/index.ts` ~L723): maps the manifest, persists
   classifications, then `enqueueLoanAnalysisGapWithCuaRouting` → SQS gap queue.
4. **Gap worker** (`processLoanAnalysisGap`) → `invokeGapAnalysisRunLambda` → the LangGraph run,
   polled to `pipeline_complete`, results persisted.

State machine: `QUEUED → RUNNING(tasktile) → AWAITING_DOC_INTEL → GAP_QUEUED → RUNNING(gap) →
SUCCEEDED`.

TaskTile API contract (from `Dashboard-Officer/amplify/functions/_shared/tasktileClient.ts`):
`POST /auth/token` → `POST /uploads/initiate` + `PUT` bytes + `/uploads/complete` per PDF →
`POST /jobs {pipeline_name:"ai_only", upload_ids, webhook_url, categories_url, entity}`
→ `job_id`. `pipeline_name` is a free-form string (no listing endpoint); the processor sends
`ai_only` (LOA sends `rack_and_stack_v1`). `categories_url` **must** point at the **AWM** category
set so extraction uses the P1 additions. Completion via webhook envelope; manifest via
`manifest_url` (~1h presigned) or `GET /jobs/{id}/manifest`.

> **Mode B has no submit path:** the TaskTile API only fetches a manifest by a **known `job_id`**.
> There is no "get manifest by loan number" call — the job↔loan mapping lives in the **dashboard's**
> DynamoDB (`LoanDocIntelJob` by `tasktileJobId`, GSI `listLoanDocIntelJobsByTasktileJobId`) plus a
> canonical manifest in S3 (`analysis-cache/{group}/{appId}/tasktile-manifest-canonical.json`). So
> for LOA-processed loans the processor needs the dashboard to hand it the loan's
> `tasktile_job_id(s)` (or S3 access) — see open question 0b.

---

## Processor architecture mapping

| LOA component | Processor equivalent |
|---|---|
| `startTasktileDocIntelJob` | New `start_doc_intel` path (**mode A only — skipped when an LOA manifest exists**): upload eFolder PDFs → `POST /jobs` (`pipeline_name=ai_only`), `webhook_url=/tasktile/webhook`, `entity={loan_number, assistant_id, run_body}` |
| `LoanAnalysisJob` / `LoanDocIntelJob` | `thread_store` doc-intel record: `thread_id ↔ tasktileJobId`, status, **stashed `run_body`** |
| `AWAITING_DOC_INTEL` wait | Same — just a DynamoDB row, **no Fargate worker, zero idle cost** |
| `tasktileWebhook` → enqueue gap | New `POST /tasktile/webhook` (secret-authed) → map manifest → persist doc_fields → **call `create_background_run`** with the stashed run_body |
| `processLoanAnalysisGap` → `gapAnalysisRun` | The existing Fargate review worker, reading TaskTile-first + LandingAI fallback |

**Sequence:** dashboard → `start_doc_intel` (returns immediately, review *pending*) → [TaskTile 2+ hrs]
→ webhook → `create_background_run` → review runs on TT data → existing `_fire_run_webhook` notifies
the dashboard.

This **drops** the earlier parallel / flag-only reconciliation design — a single gated review run
already has TaskTile data (LandingAI stays only as a fallback for fields TT doesn't cover).

### Touch points
| File | Change |
|---|---|
| `shared/tasktile_client.py` *(new)* | Port auth/upload/submit/manifest from `tasktileClient.ts` |
| `shared/tasktile_manifest_mapper.py` *(new)* | Manifest → `doc_fields`, keyed via `document_type_registry.py` (schema-id → our keys). **Must translate TaskTile's camelCase manifest keys (`escrowCompany`, `fileNumber`, `settlementAgent.*`, `owner.idNumber`) → our snake_case `doc_fields` keys (`contact_settlement_agent_*`, `dl_gov_id`, …).** |
| `api/services/runs.py` | New `start_doc_intel`; webhook path calls `create_background_run` with stashed run_body |
| `api/platform/*` + router | New `POST /tasktile/webhook` route (secret-authed) |
| `api/thread_store.py` | Store `tasktileJobId` + doc-intel status + stashed run_body per thread |
| `output/tools/data_gathering.py` | `fetch_doc_fields`: TaskTile-manifest-first, LandingAI fallback |
| env | `TASKTILE_API_BASE_URL`, `TASKTILE_CLIENT_ID/SECRET` (+ `TASKTILE_TENANTS` for tenant-scoped presign), `TASKTILE_WEBHOOK_SECRET`, `TASKTILE_WEBHOOK_URL`, `TASKTILE_PIPELINE_NAME=ai_only`, `TASKTILE_CATEGORIES_URL` (**AWM** set) |

### Latency & mitigations
- **`ai_only` (no HIL)** is the primary mitigation — extraction is expected in minutes, so the gated
  mode-A wait is short (confirm actual latency in the smoke test).
- **Pull-first for LOA loans (mode B):** if the loan already ran through LOA, a completed manifest
  exists — pull it and start the review immediately (no submit, no wait). Now first-class, not a
  fallback.
- **Pre-warm (optional, mode A):** trigger `start_doc_intel` when docs land / the loan hits the
  processor milestone rather than at "start review", so extraction is usually done first.

---

## Schema audit — AWM category set (verified 2026-09-23)

Full re-probe of every category the field-gaps doc lists, **base (`sbiqai…/api/categories/{id}`) vs
AWM set (`tasktile…/api/category-sets/awm/{id}`)**. The vendor's `tasktile-schema-api.md` (dated
**2026-09-12**) said "only 7 categories differ" — that is **stale**: a larger P1/P2/P3 batch has
landed since. **23 categories now differ**, all fields **camelCase**.

> ### Both live bugs are present in the AWM schema — but NOT extracted by `rns_ai_only`
> ⚠️ The 2026-09-23 [live smoke test](#live-smoke-test-results-2026-09-23) shows `rns_ai_only`
> ignores this schema, so "present" below means *defined in the AWM set*, **not** *returned on the
> manifest*.
> | Category | Fields added | Status |
> |---|---|---|
> | **2168 ALTA Settlement Statement** (Issue B) | escrow/title company, `settlementAgent.*`, `fileNumber`, charge arrays (15) | ⚠️ in schema, **NOT extracted by `rns_ai_only`** (got `escrowOfficer` only) |
> | **323 / 845 / 844 ID docs** (Issue A) | `owner.idNumber` / `resident.idNumber` (+issueDate…) | ⚠️ in schema, **not yet tested** on a real Govt ID doc |
> | **200 Purchase Contract** | price, dates, buyer/seller agents (20) | ✅ present |
> | **141 Mortgage Insurance** | `certificateNumber`, premiums, renewal (11) | ✅ present |
> | **117 Credit Report** | `creditReferenceNumber` | ✅ present |
> | 324, 351, 458, 996, 1007, 1046, 1064, 1833, 1836, 2104, 2198 (P2) | full monetary / VA / flood / SSPL / COC sets | ✅ present |
> | 484 SSN Verif, 819 CD dates, 984 LE fees, 816/1838 cert# | as listed | ✅ present |
>
> **On `xpath`:** an earlier read flagged that some of these fields (323/845/844 `idNumber`, all of
> 200, 141, 117 ref#) carry no `xpath`. **TaskTile confirmed the `ai_only` pipeline ignores `xpath`**
> — it's a legacy hint — so schema presence is sufficient. **Still verify actual population on a real
> `ai_only` manifest** (schema-present ≠ guaranteed-populated for other reasons).

**Classification-only on AWM** (no data fields — LandingAI stays primary; full breakdown in
[`tasktile-classification-only-docs.md`](tasktile-classification-only-docs.md)): 294 VA Loan Summary,
447 VA COE, 1118 Fraud, 1800/538 Flood Cert, 397 Payoff, 2087 Home Inspection, 578 Pest, 33
Appraisal Invoice. **PARTIAL (carry some data — not classification-only):** 522 Title Report, 1561
Evidence of Hazard Insurance, 162 Appraisal Report, 333 Approval, 167 CPL. Un-added extras: the
**full** CD (819) / LE (984) fee sets. Already-covered (no ask): 349 URLA, 1 Business Tax, 843 SSN
Card, 1481 Property Tax.

---

## Live smoke test results (2026-09-23)

First real end-to-end run of the ai-only pipeline. Script: `scripts/test_rns_ai_only.py`
(PROD eFolder read → download → upload to TaskTile **staging** → one `POST /jobs` → poll → manifest).

- **Pipeline:** `rns_ai_only` (free-form `pipeline_name`; no `/pipelines` listing endpoint).
- **TaskTile client:** `78059b2d-479d-4fe6-90bc-a7a4f5006cb6` (PROD; `.env` `TASKTILE_PROD_CLIENT_KEY`,
  identical to `LG-LOAEncompass/.env` `TASKTILE_CLIENT_ID`). Base `tasktile.staging.cybersoftbpo.ai/api`.
- **Loan:** `2607973377` (Masoud Jalalibidgoli, GUID `99ec396c-…`).
- **Docs (one job, two `upload_ids`):** ALTA Settlement Statement (attachment `35d6df9f-…`) +
  Statement of Identity (attachment `7896a9f5-…`).
- **Jobs:** `58e21c54-…` (tenant default set) and `d60a09ed-…` (pinned `categories_url=…/category-sets/awm`).
- **Manifests:** `local/rns_results/2607973377_rns_ai_only_58e21c54-….json` and `…_d60a09ed-….json`.

**Result: ✅ mechanics work / 🚨 schema not honored.**

Works:
- One job, two docs → both classified (2168 ALTA, 2044 Property/Address Search) + extracted.
- Fast: **~50s**, `status:success`, no HIL wait.
- Output is **camelCase** (confirms mapper approach).
- `input[].metadata.attachment_id` round-trips; `documents[].root_attachment_id` maps each extracted
  doc back to its Encompass attachment (per-attachment mapping confirmed).

🚨 Critical — **`rns_ai_only` ignores the AWM `content_schema`.** The manifest shape does not match
the AWM 2168 schema; passing `categories_url=…/category-sets/awm` changed **nothing** (identical
output; manifest `category_url` came back empty). So the AWM schema additions we verified are not
consumed by the ai-only extraction path.

| AWM `category-sets/awm/2168` schema | What `rns_ai_only` returned |
|---|---|
| `escrowCompany`, `titleCompany` | ❌ absent — only `escrowOfficer` (a person: "Nikki S Bott") |
| `settlementAgent{name,contact,phone,email,…}` | ❌ absent |
| `fileNumber` (the file/case #) | ❌ absent |
| `buyers[]`, `sellers[]` (arrays) | different shape: singular `buyer` / `buyerCo` / `seller` |
| `titleCharges[].buyerDebit` / `paidTo` | different: generic `loan.titleCharges[].description` / `amount` |

**Implications:**
- **Issue B (Escrow Co + Case #, 2168):** schema has the fields, but `rns_ai_only` does not extract
  them → **not fixed on the ai-only path**.
- **Issue A (co-borrower Govt ID `idNumber`):** not exercised — the ID doc sent was a SmartyStreets
  *Statement of Identity* (classified 2044), not a government ID. Needs a real license/passport doc.

**Open with TaskTile (primary blocker):** does `rns_ai_only` honor the AWM category-set
`content_schema`? If not, the schema updates don't reach the ai-only pipeline — the added fields must
be baked into the ai-only extraction schema, or those categories must run through `rack_and_stack`.

**Follow-up run (job `6e2a2c0d`, 8 real docs from loan 2608976334):** confirms the above on live data
— DL `owner.idNumber` never extracted (Issue A), ALTA has no `escrowCompany`/`titleCompany`/
`settlementAgent` (Issue B; file# + title company land on the CPL instead), and extracted key names
differ wholesale from the AWM schema. Full field-by-field breakdown:
[`tasktile-extracted-vs-awm-schema.md`](tasktile-extracted-vs-awm-schema.md).

---

## Fallback strategy — TaskTile-first + gap-driven LandingAI

Ship the integration now (feature-flagged, shadow mode first) and keep LandingAI as a **gap fill**,
not an all-docs pass. Every field a processor node needs is classified into one of three
**confidence buckets**, maintained in [`config/tasktile_doc_buckets.json`](../config/tasktile_doc_buckets.json)
(hand-maintained, iterate over time — **not** factory-generated):

| Bucket | Meaning | Runtime behaviour |
|---|---|---|
| **1 — trust** | ai_only reliably returns it | use the manifest value; fall back only if empty or fails validation |
| **2 — known-missing** | ai_only structurally never returns it | skip the manifest wait — go straight to fallback (**`cross_doc_source` first**, else LandingAI/targeted extractor) |
| **3 — check-then-fallback** *(default)* | untested / unknown | read manifest; fall back if absent **or fails a format check** — safe default for any category |

**Trigger rule (answers "when do we call LandingAI?"):** not on plain absence alone.
1. Bucket 1 → take the value; only fall back if empty/invalid.
2. Bucket 2 → don't even look — resolve via `cross_doc_source` (a sibling doc in the same job, e.g.
   ALTA's missing escrow/title company + file# come from the **CPL (167)**), else LandingAI.
3. Bucket 3 → check the manifest; fall back if absent **or** the value fails validation (zip=5 digits,
   SSN=9, dates parse, idNumber matches DL pattern). Absence-only would miss wrong-but-present values
   (we saw zip `90005`, flood middleName mis-parsed) — validation catches those.

**Latency:** batch all gap fields per doc into one LandingAI pass, parallelise across docs (we have
`root_attachment_id` per doc to know which eFolder PDF to send). The run stays gated on manifest-ready
anyway, so net cost ≈ ai_only time + one parallel LandingAI batch over the (small) gap set — far less
than LandingAI-on-everything today.

**Coverage is empirical & self-correcting, not frozen:** only the **8 categories with a `tested_job`**
are promoted to bucket 1 today; structural gaps (`idNumber` on 323/845/844, ALTA escrow/title company)
are pinned to bucket 2; **everything else stays bucket 3** until verified. Shadow mode then samples
every real run to promote/demote fields over time (a single one-off test isn't authoritative — ai_only
is free-form and the ALTA already returned two different shapes across two loans).

**Current config snapshot (2026-09-23):** 18 categories — 8 bucket-1 (tested: 2168, 323, 117, 167,
200, 141, 538, 2044), 10 bucket-3 (untested: 349, 819, 984, 816, 1838, 843, 845, 844, 1481, 1).
Bootstrap the common untested ones (349, 819, 984, 843, 1481, issued MI cert) via the harness
(`scripts/test_rns_ai_only.py`); leave the rare ID variants (845/844) to shadow mode.

---

## Open questions & decisions

**Resolved 2026-09-23:** schema batch is **live on AWM** (23 cats, camelCase); **both live bugs
(Issue A + Issue B) are addressed** — `ai_only` ignores `xpath`, so the ID-number fields should
extract (confirm on a real manifest). Pipeline is **`ai_only`** (no HIL). Mapping + mode-B channel
decided below.

0a. **camelCase → snake_case mapping — RESOLVED (reuse LOA's pattern).** The previous LOA schema
   additions are **also camelCase** (`escrowCompany`, `settlementAgent.name`, `closingDate`,
   `certificateNumber`, `creditReferenceNumber`, `applicationFee`…), and LOA already solved the
   translation with a **config-driven field map** — `LG-LOAEncompass/config/tasktile_field_map.json`
   (dotted camelCase manifest paths → snake_case `doc_fields` keys, with candidate fallbacks). Port
   that JSON + its mapper rather than hand-writing snake_case reads. **Known limit:** the flat-key
   mapper can't rename **array sub-fields**, so `titleCharges[]/recordingCharges[]`
   (`buyerDebit`/`paidTo`) stay on LandingAI — LOA does the same.
0b. **Mode-B handoff — DECIDED: dashboard passes the job id.** The dashboard passes the loan's
   `tasktile_job_id(s)` into the processor run input (mirrors LOA's `tasktile_job_ids`); the
   processor pulls `GET /jobs/{id}/manifest`. Chosen over giving the processor S3 access to the
   dashboard's canonical manifest — the dashboard already triggers the processor run and already
   stores `LoanDocIntelJob.tasktileJobId`, so this stays inside the current architecture.
   **Dependency:** dashboard change to include the id(s) in the run body.
1. **Phase-1 trigger (mode A):** *pre-warm* = fire `start_doc_intel` **early** — when docs land in
   the eFolder / the loan hits an earlier processor milestone — instead of at "start review", so the
   (minutes-long) `ai_only` extraction is already done before a review is requested (perceived
   latency ≈ 0). **Decision: pre-warm + allow on-demand at start-review as fallback.**
2. **TaskTile failure / partial / timeout — DECIDED: partial manifest + LandingAI fallback.** Run
   the review on whatever manifest fields exist and fall back to LandingAI for the rest; never block
   the processor.
3. **Cutover (TaskTile-first vs LandingAI) — RESOLVED by the audit.** TaskTile-primary for every
   category with the added data fields (2168 + all P1 ID docs + the P2 sets + 117/484/819/984/816/1838);
   **LandingAI-primary** only for the **classification-only** docs (294, 447, 1118, 1800/538, 397,
   2087, 578, 33) and the **PARTIAL** ones for their missing fields (522, 1561, 162, 333, 167) — see
   [`tasktile-classification-only-docs.md`](tasktile-classification-only-docs.md). (`xpath` is not a
   cutover factor — `ai_only` ignores it.)
4. **Attachment→manifest mapping — CONFIRMED supported.** The upload client sends
   `metadata:{attachment_id}` on `POST /uploads/initiate` (`LG-loaOrch/tools/tasktile_client.py`
   `initiate_upload`), so each manifest row maps back to the right eFolder attachment — this is what
   makes per-attachment co-borrower ID resolution possible.
5. **Webhook — how LOA/Dashboard-Officer does it.** LOA registers `webhook_url` on `POST /jobs`;
   TaskTile POSTs a per-job `job.completed` envelope with the **full manifest** to `tasktileWebhook`
   (secret-authed via `TASKTILE_WEBHOOK_SECRET`); the handler resolves `LoanDocIntelJob` by
   `tasktileJobId`, maps the manifest → classifications, stores S3 + canonical manifest, and enqueues
   gap analysis. The processor mirrors this: `POST /tasktile/webhook` (secret-authed) → map →
   `create_background_run`.
6. **Category confirmation:** `our doc → SBIQ id` mappings are name-match guesses — confirm each via
   a real manifest's `category_id` before trusting the gap analysis (the audit validated the ids
   exist on AWM, not that each eFolder bucket classifies to the expected one).
7. ~~**HIL ownership**~~ — **N/A. `ai_only` has no human-in-the-loop.**
8. **LOA-first onboarding:** **assume all officers eventually onboard to LOA → Documents.** Mode B
   (pull existing manifest) becomes the steady-state default; mode A (`ai_only` self-trigger) is the
   bridge for loans that reach the processor before an LOA run, and tapers to zero as onboarding
   completes. See [Interim workaround vs. target state](#interim-workaround-vs-target-state).

---

## Why this shape
- Never blocks the 3-hr Fargate run on a 2-hr TaskTile job; the wait is a free DynamoDB row.
- Reuses the existing background-run + `_fire_run_webhook` infra — TaskTile is just a slower
  upstream producer that gates the run.
- Single review run on the best available data (TaskTile-first, LandingAI fallback), so no
  double-writes or diff-reconciliation.
