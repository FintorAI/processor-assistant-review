# Unimplemented Items — Build Plan

_Pulled live from the canonical Google Sheet on **2026-07-13** (`scripts/sheet_sync.py pull`, 196 rows)._

## Status snapshot

| Bucket | Count |
|---|---|
| **Implemented** (incl. 1 impl+ordering-blocked) | **58** |
| Not Implemented | 103 |
| Not Implemented, ordering blocked (creds) | 10 |
| Partial | 8 |
| Partial, ordering blocked (creds) | 7 |
| Partial, Needs Clarification | 7 |
| Needs Clarification | 1 |
| Ordering blocked (creds) | 1 |
| Partial, Qualia ordering blocked | 1 |
| **Remaining (this plan)** | **138** |

## How to read this plan

**The sheet's "Owning Agent" column is not a feasibility signal.** 84 of the 138 remaining
items are labelled `none`, but many of those are ordinary **review doc‑vs‑LOS checks** on
documents we *already extract* (verified against `output/config/required_docs.json`). This
plan re‑groups everything by **build feasibility**, cheapest first, following the shipped
pattern: value is added **inside `review`** (rule‑adds + `_flag`/`_write_fields`) and via
**discrete comms/doc‑mgmt buttons** — never the (deferred) orchestrator.

Tiers:
- **Tier 1** — build now. Rule‑add in a `review` tool; every field is already extracted / in LOS.
- **Tier 2** — needs a new/extended extraction schema (server field in `LG-docsOrch` + `required_docs.json`), then a rule.
- **Tier 3** — discrete comms / doc‑mgmt button (existing pattern, no new source).
- **Tier 4** — blocked on external credentials / third‑party integration (library often already written).
- **Tier 5** — manual, computer‑use, or process/out‑of‑scope.

## Scope — sections 01–16 only (updated 2026-07-13)

**Sections 17–24 are deferred — do not implement yet.** Everything from **§17 Submit to
Underwriting onward** (17 Submit to UW, 18 Change of Circumstance, 19 Loan Approval &
Conditions, 20 CD Request & Approval, 21 Condition Handling, 22 Docs Stage, 23 Funding,
24 Notice of Action Taken) is **post‑submission / post‑UW** and out of scope for this plan.
That's **43 of the 138** remaining items; the tiers below cover only the **95 remaining items
in §01–16**. The deferred §17–24 items are listed at the bottom for reference. (This matches
the roadmap decision to defer post‑UW; revisit with the existing button/action‑item pattern.)

---

## Tier 1 — Build now (review rule‑adds, data already extracted)

> **Progress (2026-07-13, branch `feat/checklist-not-implemented-gaps-integration`):** 1A **shipped** —
> §13 #2–#10 built into `review_flood_hazard_insurance` (STEP_08, substep 8.1), warn/info only,
> `Validation: PASSED`, behavioral test green. 1B **shipped** — §02 #6/#7 HOI + Flood file‑contact
> auto‑fill built into `review_file_contacts` (STEP_01, 1.2). Contact types confirmed against the Test
> instance (`HAZARD_INSURANCE` / `FLOOD_INSURANCE`); `required_docs.json` flood keys corrected to the
> live CatchingDoc schema (`company_*` / `contact_*`). Create‑or‑overwrite with an info‑overwrite audit flag.

### 1A. Hazard Insurance review — §13 #2–#10 ✅ SHIPPED  → `review_flood_hazard_insurance` (STEP_08, 8.1)
`evidence_of_insurance` already extracts the **entire** policy: `insured_name`,
`insured_location`, `policy_number`, `mortgagee_name`/`mortgagee_address`/`mortgagee_loan_number`,
`coverage_start_date`/`coverage_end_date`, `hazard_insurance_coverage`, `hazard_insurance_premium`,
`deductible`, `wind_hail_deductible`, `loss_of_use_coverage`, `replacement_cost`,
`hazard_insurance_company`/`phone`/`agent_email`. STEP_08 was scaffolded for exactly this.

| Item | Check | Inputs |
|---|---|---|
| 13 #2 Loan # on policy | `mortgagee_loan_number` == LOS loan number (364) | doc + LOS |
| 13 #3 Applicant names | `insured_name` overlaps borrower/co‑borrower surnames (4002/4006) | doc + LOS |
| 13 #4 Property address matches subject | `insured_location` vs USPS‑validated subject (`address_validation`) | doc + LOS |
| 13 #5 Effective date on/before Note/Closing | `coverage_start_date` ≤ closing date (763) and `coverage_end_date` ≥ closing | doc + LOS |
| 13 #6 Insurable coverage ≥ minimum | `hazard_insurance_coverage` ≥ min(loan amount, `replacement_cost`); dwelling coverage rule | doc + LOS |
| 13 #7 Paid in full / due at closing | premium present; cross‑ref CD/closing (info flag) | doc |
| 13 #8 Premium & deductibles vs guidelines | `deductible` / `wind_hail_deductible` under program ceiling (e.g. ≤ 5% or $ cap) | doc |
| 13 #9 Mortgagee clause correct | `mortgagee_name`/`mortgagee_address` match lender standard mortgagee clause (config constant) | doc + config |
| 13 #10 Rent‑loss coverage if rental income | require `loss_of_use_coverage` when occupancy = investment / rental income used | doc + LOS |

Notes: add HOI contact/mortgagee LOS field IDs to `FIELD_MAP` + step YAML `los_fields_read`;
add a `config/mortgagee_clause.yaml` for #9. Warn‑only (no auto‑write).

### 1B. File‑Contacts auto‑fill from insurance docs — §02 #6, §02 #7 ✅ SHIPPED  → `review_file_contacts` (STEP_01, 1.2)
Both source from already‑extracted docs and write via the Encompass contacts API by `contactType`.
- **02 #6 HOI company / phone / email** ← `evidence_of_insurance.hazard_insurance_company` / `hazard_insurance_phone` / `agent_email` (+ contact / address) → `HAZARD_INSURANCE`.
- **02 #7 Flood company / phone / email** ← Flood doc `company_name` / `company_phone` / `contact_email` (+ contact / address) → `FLOOD_INSURANCE`. Required `required_docs.json` flood keys to be corrected to the live CatchingDoc schema (done).
- Create‑or‑overwrite, per‑field diff (address compared whole), phone/email validated, one info‑overwrite flag per write; no‑op when no company name is extracted.

### 1C. Flood policy / zone logic — §12 #5 (and partial #3/#7/#8)  → `review_flood_hazard_insurance` (8.1/8.2)
`flood_certificate` extracts `in_sfha`, `flood_zone`, `flood_insurance_company`,
`flood_policy_number`, `flood_annual_premium`.
- **12 #5 SFHA → notice + insurance required**: when `in_sfha` truthy, require a flood policy on file (flood cert company/policy or Flood Insurance doc); flag if missing. (Extends the SFHA check already in 8.1.)
- **12 #7 Flood policy details** *(partial)*: confirm `flood_policy_number` + `flood_annual_premium` present; coverage/mortgagee not extracted → surface what exists, defer the rest to Tier 2.
- **12 #8 Paid in full / due at closing** *(partial)*: premium presence + closing cross‑ref (info).
- **12 #3 Life‑of‑Loan Determination**: needs a `life_of_loan` cert field → Tier 2 (tiny).

### 1D. Credit‑report review — §04 #2, §04 #3  → `review_borrower_summary.py` (or `review_urla_liabilities`)
`credit_report` extracts `borrower_aka`, `coborrower_aka`, `borrower_ssn`, `coborrower_ssn`, `borrower_dob`, `coborrower_dob`.
- **04 #2 Applicant name + AKAs**: mostly done by **3.1** (AKA write to 1869/1874). Extend to co‑borrower + emit an explicit 04 #2 result, or reclassify as covered.
- **04 #3 SSN confirmation / discrepancies**: overlaps **1.6** (SSN last‑4 vs credit report). Emit an explicit 04 #3 result; add co‑borrower SSN compare.

### 1E. PIW / appraisal-waiver check — §11 #2  → `review` tools
- **11 #2 PIW / appraisal waiver**: `du_findings.appraisal_waiver_eligible` / `appraisal_waiver_expiration` already extracted → flag whether a PIW was issued/executed.

_(The §19/§20 approval & CD confirmations that previously lived here — 19 #8, 19 #9, 20 #1 —
are §17+ and now deferred; see the bottom section.)_

### 1F. AUS findings PARSE (run stays blocked) — §16 #3, §16 #5  → `review` (split from Tier 4)
`du_findings` is richly extracted (recommendation, LTV/TLTV, ratios, conditions, property/occupancy,
message codes). The *running* of AUS is credential‑blocked, but **parsing an existing findings PDF**
is not. Add flags for:
- **16 #3 Property address/type/units/occupancy** vs LOS (`du_property_address`, `du_property_type`, `occupancy_status`).
- **16 #5 Review findings / verification / approval conditions**: surface `actionable_message_codes`, `employment_conditions`, `asset_conditions` as structured flags.

---

## Tier 2 — Needs a new/extended extraction schema first

Each: add the server field(s) in `LG-docsOrch` catchingDoc schema → register in `required_docs.json`
→ then a `review` rule. (Same playbook used for `tax_owner_name`, `appraisal_occupancy`, `liabilities[]`.)

| Cluster | Items | New extraction needed |
|---|---|---|
| **MI Quote** (no `mi_quote` doc type today) | §14 #1–#8 | New MI Quote schema: expiration, paid/plan type, remittance freq, refund/amort type, coverage %, premium rate/schedule, LTV, DTI. Then compare vs LOS MI fields; 14 #8 triggers redisclosure when LTV/DTI moves materially. |
| **Personal tax returns / self‑employed** | §07 #1, #3, #4, #5, #7, #8 | 1040 + Schedule C / Schedule E / K‑1 schema (income, ownership %, rental props). **07 #4 already flagged Needs Clarification** — no sample loan / bucket name (see that row). Blocked on a live example. |
| **W‑2 / 1099 two‑year** | §05 #6 | `w2` is extracted; need 1099 doc + 2‑yr continuity rule. |
| **Credit address / employment discrepancies** | §04 #4 | Add credit‑report `addresses[]` / `employers[]` extraction, compare vs 1003. |
| **Appraisal identity** | §11 #4, §11 #7 | Add `legal_description`, appraisal borrower/seller names (11 #4); `as_is_vs_subject_to` / 1004D completion status (11 #7). |
| **Income doc validation** | §06 #1 *(Needs Clar.)* | Awards letters / 1099 presence + amount vs LOS per AUS. Partially surfaced today; needs award/1099 fields. |
| **URLA form‑completion writes** | §03 #15 HMDA, §03 #16 BK, §03 #19 Mortgage Terms (refi), §03 #21 HUD Addendum | Form‑field writes; some need extracted inputs (BK dates, prior‑loan terms). _(HUD‑92900‑LT also appears in the deferred §17 #5/#10.)_ |

---

## Tier 3 — Discrete comms / doc‑mgmt buttons (existing pattern)

Follow the shipped button model (a `comms_actions[]` item or a doc‑mgmt tile graph); no orchestrator.

| Item | Button |
|---|---|
| 13 #1 Order Hazard Insurance | HOI request email (comms) |
| 11 #8 Send appraisal via Blend + ROV disclosure | comms |
| 10 #2 Title package completeness | title‑order email exists; add completeness check on returned pkg |
| 10 #3 Update Encompass w/ Tax Cert (parcel, annual amt) | write from `tax_summary` (`tax_parcel_number`, `annual_taxes`) — actually **Tier 1 write** once wired |
| 10 #5 Upload title docs (Qualia) | upload **available** via dashboard Doc Management button; only the Qualia _download_ pipeline is Tier 4 |
| ~~11 #9, 13 #11 Uploads~~ | ✅ **Implemented** — generic upload is live via the dashboard Doc Management upload button (`upload_forms_to_efolder.py`) |

_(§17+ buttons — 21 #1/#2/#3 conditions, 21 #5 upload, 23 #1/#2 funding, 18 #2/#3 COC, 20 #4 CD request — are deferred; see the bottom section.)_

---

## Tier 4 — Blocked on external credentials / third‑party integration

Library frequently already written; waiting on creds or dispatch wiring. **Do not build now**; track as blocked.

- **VOE / Xactus**: 01 #8, 04 #1, 04 #8, 05 #1.
- **Appraisal order (Reggora)**: 01 #12, 11 #1 (lib done; plug creds).
- **Flood cert order**: 01 #10, 12 #1, 12 #6.
- **CAIVRS / tax transcripts**: 01 #13.
- **Ocrolus (income calc / indexing)**: 01 #14, 05 #10, 07 #10. _(17 #1/#4 deferred.)_
- **AUS run (DU+LP)**: 16 #1/#2/#4/#6/#7 (run lib done, not wired). *Findings PARSE split to Tier 1 (16 #3/#5). (17 #9 deferred.)*
- **FHA case order (FHA Connection)**: 01 #9 (case‑# write from doc already done).
- **Mavent / FraudGuard**: 15 #1, 15 #3, 15 #4.

---

## Tier 5 — Manual / computer‑use / process (not a rule build) — §01–16 only

- **Intake confirmations**: 01 #1, 01 #3, 03 #4 (signed/dated), 03 #20 indexing, 09 #4 fully‑executed contract (some are simple presence checks → could promote to Tier 1 if a doc field exists).
- **Program/eligibility judgement**: 05 #5/#9, 06 #2/#4, 08 #3/#8/#9 — mostly guideline calls; revisit per‑program.

_(The §17+ computer‑use / milestone / Teams / process items moved to the deferred section below.)_

---

## Deferred — sections 17–24 (post‑submission / post‑UW, not now)

Out of scope for this plan. Captured here so nothing is lost; revisit with the existing
button/action‑item pattern (no orchestrator). **43 items.**

| Section | Items (count) |
|---|---|
| 17 Submit to Underwriting | #1, #2, #4, #5, #7, #8, #9, #10, #12, #13, #14 (11) |
| 18 Change of Circumstance | #1, #2, #3, #4 (4) |
| 19 Loan Approval & Conditions | #1, #2, #3, #6, #7, #8, #9, #10, #11 (9) |
| 20 CD Request & Approval | #1, #2, #3, #4, #5, #6, #7 (7) |
| 21 Condition Handling | #1, #2, #3, #4, #5 (5) |
| 22 Docs Stage | #1, #2, #3 (3) |
| 23 Funding | #1, #2, #3 (3) |
| 24 Notice of Action Taken | #1 (1) |

When resumed, the readiest of these are still cheap review reads (e.g. 19 #8 property type
appraisal vs UW, 19 #9 expiration date, 20 #1 lock‑covers‑CD from `lock_confirmation` /
`du_findings` / `loan_estimate`) and a milestone write (17 #14 Processing → Underwriting via
the Encompass milestone API).

---

## Recommended build order (§01–16)

1. ✅ **§13 Hazard Insurance (1A)** — shipped. ✅ **§02 #6/#7 HOI+Flood contact auto‑fill (1B)** — shipped.
2. **§12 flood policy/zone (1C)** — same tool, finishes the flood cluster.
3. **§16 #3/#5 AUS findings parse (1F)** and **§11 #2 PIW (1E)** — pure reads off `du_findings`.
4. **§04 #2/#3 credit (1D)** — small rule‑adds, some reclassify.
5. **Tier 2 MI Quote schema** — biggest single Tier‑2 win (8 items) once the MI Quote extraction lands.
6. Tier 3 buttons (§01–16) opportunistically; Tier 4 when creds arrive.

**Near‑term §01–16 remaining: 95 items** (43 in §17–24 deferred). Tier‑1 (no new schema, no
credentials) is the priority; §13 already shipped.

## Mechanics reminder
- Rule‑adds land in `FACTORY-LOCK: true` review tools; if a new `_los`/`_doc` key is used, add it to `FIELD_MAP` (`data_gathering.py`) **and** the step YAML `los_fields_read`, then `python3.11 -m factory factory-reset` and confirm `Validation: PASSED`.
- New Encompass field IDs (HOI/mortgagee/flood contacts) must be verified in the Encompass UI before writing.
- Push status back with `scripts/sheet_sync.py push` per item as it ships.
