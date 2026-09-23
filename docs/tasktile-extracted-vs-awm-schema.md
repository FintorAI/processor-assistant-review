# TaskTile `rns_ai_only` — actual extracted data vs. AWM schema

> ## ✅ UPDATE 2026-09-23b — TaskTile shipped an `ai_only` extraction fix (retest job `a2643fae-88e9-4289-96eb-fde7f652e2af`)
> Re-ran the **identical 8-doc set** (loan 2608976334) after TaskTile's fix. The two live bugs below
> are now **RESOLVED**, plus two more gaps closed — but two categories **regressed**:
>
> | Category | Field(s) | Before (6e2a2c0d) | After fix (a2643fae) |
> |---|---|---|---|
> | 323 Drivers License | `owner.idNumber` | ❌ missing both DLs | ✅ **`D9123301`, `D4913348`** (+ issueDate/suffix/state/sex/DOB) |
> | 2168 ALTA | `escrowCompany`, `titleCompany`, `settlementAgent.*`, `fileNumber`, `titleCharges[]`, `recordingCharges[]`, `sellers[]` | ❌ missing | ✅ **all extract directly off the ALTA** (CPL cross-doc workaround retired) |
> | 200 Purchase | `sellers[]`, `sellersAgent.*` | ❌ missing | ✅ **name/company/phone/email/licenseId**, split from buyersAgent |
> | 141 MI | `totalPremium`, `upfront/monthlyPremiumAmount`, `renewal.*` | ❌ missing | ✅ present (cert#/miFileNumber still empty — correct, it's a *quote*) |
> | 117 Credit | SSN | ✅ full SSN | ⚠️ **now `last4SSN` only**; tradelines/collections still ❌ |
> | 538 Flood | flood zone / determination# / NFIP | ✅ rich | ❌ **REGRESSED** — only date+lender+names |
> | 167 CPL | settlementAgent.name / fileNumber | ✅ rich | ❌ **REGRESSED** — only CPLDate+issuingAgent |
>
> **Takeaway:** `rns_ai_only` output shape is **unstable run-to-run** — treat any single result as
> directional and let shadow mode confirm over time. Bucket assignments updated in
> [`config/tasktile_doc_buckets.json`](../config/tasktile_doc_buckets.json) (version `2026-09-23b`).
> The per-category tables below now reflect the **post-fix** state (job `a2643fae`), with the
> pre-fix (`6e2a2c0d`) value noted inline where it changed.

---

**Source job:** `6e2a2c0d-ce82-493b-b03c-82745bb017b3` (pipeline `rns_ai_only`, status `success`)
**Client:** `78059b2d-479d-4fe6-90bc-a7a4f5006cb6` (PROD) · TaskTile **staging** · run 2026-09-23
**Docs:** 8 real eFolder PDFs from loan 2608976334 (Mora / Velasco purchase, 4 borrowers)
**Manifest:** `local/rns_results/2608976334_rns_ai_only_6e2a2c0d.json`
**AWM schema:** `tasktile.staging.cybersoftbpo.ai/api/category-sets/awm/{category_id}`

> ## Headline
> **`rns_ai_only` does free-form AI extraction — it does NOT follow the AWM `content_schema`.**
> The extracted key names still differ from the AWM schema and the **shape varies per document and
> per run** (the a2643fae fix improved coverage but also regressed Flood/CPL). So the comparison
> below matches by **concept/data**, not key name. Adding fields to the AWM set will **not** change
> this output — the fields must be added to the ai-only extractor itself.

Legend: ✅ data present · ⚠️ partial (some sub-fields or shape differs) · ❌ data missing

---

## ✅ Critical gaps (our two live bugs) — RESOLVED by the fix

| Bug | Category | AWM field | Pre-fix (6e2a2c0d) | Post-fix (a2643fae) |
|---|---|---|---|---|
| **Issue A — Govt ID number** | 323 Drivers License | `owner.idNumber` | ❌ missing on both DLs | ✅ **`D9123301` (Velasco), `D4913348` (Torres)`** |
| **Issue B — Escrow / title company** | 2168 ALTA | `escrowCompany`, `titleCompany`, `settlementAgent.*` | ❌ missing | ✅ **`Escrow Closing Services Inc.`, `Fidelity National Title Company`, `settlementAgent.name = Shane Magness`** |
| Issue B — File / case # | 2168 ALTA | `fileNumber` | ⚠️ as `escrow.escrowNumber` | ✅ **`fileNumber = "14576 - SM"`** (correct key) |

> The ALTA now carries escrow/title/file# **directly**, so the earlier cross-doc-from-CPL workaround
> is retired. (Ironically the **CPL regressed** in the same run — see §167 below.)

---

## 323 — Drivers License (×2)  ✅ FIXED

| AWM schema field | Extracted? | Notes |
|---|---|---|
| `owner.idNumber` | ✅ | **FIXED** — `D9123301` (Velasco), `D4913348` (Torres). Was ❌ in 6e2a2c0d |
| `owner.firstName/lastName/middleName` | ✅ | as `owner.*` (middleName on Torres) |
| `owner.suffix` | ✅ | **FIXED** — present on Torres. Was ❌ |
| `owner.DOB` | ✅ | as `owner.DOB` |
| `owner.sex` | ✅ | `owner.sex` on both DLs now |
| `owner.address` | ✅ | as `owner.address` (residence) |
| `owner.issueDate` | ✅ | **FIXED** — was ❌ |
| `state` (issuing state) | ✅ | **FIXED** — top-level `state`. Was ❌ |
| `expirationDate` | ✅ | |

**Net:** every DL field the processor needs now extracts. Category promoted to bucket 1 (no overrides).

---

## 2168 — ALTA Settlement Statement  ✅ FIXED

| AWM schema field | Extracted? | Notes |
|---|---|---|
| `escrowCompany` | ✅ | **FIXED** — `Escrow Closing Services Inc.` Was ❌ |
| `titleCompany` | ✅ | **FIXED** — `Fidelity National Title Company`. Was ❌ |
| `settlementAgent.{name,phone,address}` | ✅ | **FIXED** — `Shane Magness`, `714-854-9344`, `3 Pointe Drive, Suite 107`. Was ❌ |
| `settlementAgent.{contact,email,stLicenseId,contactStLicenseId}` | ⚠️ | keys present but empty — pinned bucket 2 |
| `fileNumber` | ✅ | **FIXED** — `fileNumber = "14576 - SM"` (correct key now). Was `escrow.escrowNumber` |
| `buyers[].firstName/lastName/middleName` | ✅ | now top-level `buyers[]` (not `escrow.buyers[]`) |
| `sellers[].firstName/lastName/middleName` | ✅ | now top-level `sellers[]` |
| `propertyAddress.*` | ✅ | top-level `propertyAddress.*` |
| `date` | ✅ | top-level `date` |
| `titleCharges[].description` | ✅ | **FIXED** — `Title - Owners Policy For $1,170,000.00`. Was ❌ |
| `titleCharges[].buyerDebit` | ✅ | **FIXED** — `2868`. Was amount-only |
| `titleCharges[].paidTo` | ❌ | still not returned — pinned bucket 2 |
| `recordingCharges[].description` / `.buyerDebit` | ✅ | **FIXED** — was ❌ |

**Net:** ALTA is self-sufficient for escrow/title/file# + charges. Promoted to bucket 1; only `titleCharges[].paidTo` + empty settlementAgent sub-fields remain bucket 2.

---

## 167 — Closing Protection Letter  ❌ REGRESSED

Was rich in 6e2a2c0d (settlementAgent.name = title company, `fileNumber`, property) — the fix **collapsed it**:

| AWM schema field | Extracted? | Notes |
|---|---|---|
| `CPLDate` | ✅ | as `CPLDate` |
| `issuingAgent` | ✅ | as `issuingAgent` |
| `settlementAgent.*` / `fileNumber` / `property` | ❌ | **REGRESSED** — no longer returned (were present pre-fix) |

**Net:** CPL is no longer a useful Issue-B source, but the ALTA now covers that directly. Demoted to bucket 3; monitor — `ai_only` shape is unstable run-to-run.

---

## 200 — Purchase Contract  ✅ FIXED

| AWM schema field | Extracted? | Notes |
|---|---|---|
| `buyers[].firstName/lastName/middleName/suffix` | ✅ | top-level `buyers[]` (suffix now present) |
| `sellers[].firstName/lastName/middleName/suffix` | ✅ | **FIXED** — `sellers[]` now extracted. Was ❌ |
| `datePrepared` | ✅ | |
| `propertyAddress.*` | ✅ | top-level `propertyAddress.*` |
| `purchasePrice` | ✅ | |
| `earnestMoney` | ✅ | `earnestMoney` (correct key now) |
| `closingDate` | ✅ | top-level `closingDate` |
| `buyersAgent.{name,company,phone,email,address,licenseId}` | ✅ | **FIXED** — full contact now. Was partial (`agents[]`) |
| `sellersAgent.{name,company,phone,email,address,licenseId}` | ✅ | **FIXED** — `Lori Desantis` / `T.N.G. Real Estate` / lic `01836828`. Was ❌ |
| `sellerCreditAmount` | ❌ | not present on tested contract — pinned bucket 2 until seen |

**Net:** buyer + seller agents split correctly; sellers extracted. Promoted to bucket 1.

---

## 141 — Mortgage Insurance  *(doc is an MI Quote, not an issued certificate)*  ✅ FIXED (premiums)

| AWM schema field | Extracted? | Notes |
|---|---|---|
| `company.name` | ✅ | **FIXED** — `Radian`. Was ❌ |
| `company.address` | ⚠️ | key present, empty |
| `totalPremium` | ✅ | **FIXED** — `166.73`. Was ❌ |
| `upfrontPremiumAmount` / `monthlyPremiumAmount` | ✅ | **FIXED** — `8002.8` / `166.73`. Was ❌ |
| `frequency` | ✅ | |
| `renewal.*` / `cancelAtPercent` | ✅ | **FIXED** — renewal first/second percent+months. Was ❌ |
| `premiumType` | ⚠️ | key present, empty |
| `certificateNumber` / `miFileNumber` | ❌ | empty — **expected on a quote** (belong on an issued cert 816/1838), not a gap; pinned bucket 2 |
| `expirationDate` / `propertyAddress.*` | ✅ | |

**Net:** premium/renewal now extract. Promoted to bucket 1; cert#/miFileNumber remain bucket 2 (need an issued cert to verify).

---

## 538 — Flood Certification  ❌ REGRESSED

Was rich in 6e2a2c0d (flood zone, determination#, NFIP) — the fix **collapsed it**:

| AWM schema field | Extracted? | Notes |
|---|---|---|
| `date` | ✅ | top-level `date` |
| `lender` | ✅ | |
| `borrowers[]` | ✅ | `borrowers[].firstName/lastName/middleName` |
| `floodZone` / `determinationNumber` / NFIP community+map | ❌ | **REGRESSED** — no longer returned (were present pre-fix) |

**Net:** the flood-determination fields (the whole point) are now missing. Demoted to bucket 3; fall back for flood-zone fields. Monitor — `ai_only` shape is unstable.

---

## 117 — Credit Report  ⚠️ MIXED

Scores + identity extract; tradelines still don't; SSN downgraded to last-4.

| AWM schema field | Extracted? | Notes |
|---|---|---|
| `scores[]` | ✅ | `scores[].score` + `.applicant.*` (tri-merge) |
| `applicant1/2.last4SSN` | ⚠️ | **now last-4 only** (`3234`, `6533`). Was **full SSN** in 6e2a2c0d — a downgrade |
| `creditReferenceNumber` | ✅ | top-level `creditReferenceNumber` |
| `company.name` | ✅ | **FIXED** — `ALL WESTERN MORTGAGE INC`. Was ❌ |
| `alerts` | ✅ | present |
| `creditTradeLines[]` | ❌ | **still no tradelines** — pinned bucket 2 |
| `collectionAccounts[]` / `derogatoryAccounts[]` / `publicRecords[]` / `inquiries[]` / `tradeSummary[]` | ❌ | still not extracted — pinned bucket 2 |

**Net:** kept at bucket 1 for scores/identity; tradelines/collections/derogatory/public records/inquiries remain bucket 2. NOTE the SSN downgrade (full → last-4).

---

## Summary — status after TaskTile's `ai_only` fix (job a2643fae)

**Resolved:**
1. ✅ **`owner.idNumber` (Drivers License)** now extracts on both borrower DLs — Issue A closed.
2. ✅ **ALTA `escrowCompany` / `titleCompany` / `settlementAgent` / `fileNumber`** now extract directly off the ALTA — Issue B closed; CPL cross-doc workaround retired.
3. ✅ **Purchase `sellers[]` + `sellersAgent.*`** now extract, split from buyersAgent.
4. ✅ **MI premium / renewal** now extract off the quote.
5. ✅ **ALTA title/recording charges** now return `description` + `buyerDebit` (not amount-only).

**Still open / to raise with TaskTile:**
6. ❌ **Credit tradelines / collections / derogatory / public records / inquiries** — still only scores + identity.
7. ⚠️ **Credit SSN downgraded** to last-4 only (was full SSN pre-fix) — confirm intended.
8. ❌ **Flood (538) regressed** — lost flood zone / determination# / NFIP.
9. ❌ **CPL (167) regressed** — down to CPLDate + issuingAgent.
10. ⚠️ **Output shape is unstable run-to-run** — the regressions (#8/#9) landed in the same run that fixed everything else. `rns_ai_only` still ignores the AWM `content_schema`; treat single results as directional and let shadow mode confirm.
11. **MI `certificateNumber` / `miFileNumber`** — re-test with an *issued* MI cert (this doc is a rate quote), not a gap on a quote.
