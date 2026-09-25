# TaskTile `rns_ai_only` — actual extracted data vs. AWM schema

> ## ⚠️ UPDATE 2026-09-25 — VOE mis-extraction (436) + homeowners policy routes to 1479
> Two agent-side validations run against real docs:
>
> - **436 Verification of Employment — ❌ NOT extracted (TaskTile-side).** Ran `rns_ai_only` on
>   TWO real Truework VOEs from loan 2604964148 (`VOE - Truework` job `c1b93443`, `PVOE - Truework`
>   job `20ede7d0`). Both returned only `loanType` / `loanAmount` (`"Pre-approval"` / `15116.41`,
>   and `""` / `0`) — **none** of the AWM VOE fields (`employer.name`, `rateOfPay`, `frequencyOfPay`,
>   `averageHoursPerPayPeriod`, `VOEDate`, `dateOfEmployment`, `employerAddress.*`). The 436
>   field_map is structurally correct but fills **nothing** today. **Raise with TaskTile** — the
>   ai-only extractor misclassifies the VOE as a loan/pre-approval doc.
> - **1479 Property Insurance — ✅ wired.** The actual homeowners POLICY classifies as **1479**
>   (not 1561 Evidence of Insurance) on the ai-only path (loan 2606970588). Added a 1479 field_map
>   entry mirroring 1561 (+`insured_location`) so it fills regardless of the unstable classification.
> - **Form 1040 (10)** — routing alias kept, intentionally UNMAPPED (0 data leaves on ai-only).

> ## ✅ UPDATE 2026-09-23d — bootstrapped 5 more categories (Poti loan) + found source loans for the rest
> Ran `rns_ai_only` on loan 2609978332 (Sarah Poti, job `badfc59f-b1c3-4e2d-a7e9-23dca8667f5c`) over 5
> Group-A docs, then pipeline-scanned 80 recent loans to locate source docs for the remaining gaps.
>
> - **5 new categories bootstrapped** — classifications differed from the filenames, so trust the returned `category_id`:
>
> | Cat | Source doc | Result |
> |---|---|---|
> | 522 Title Commitment | FHA Conditional Commitment / Statement of Appraised Value | ✅ RICH — titleCompany, escrowCompany, commitmentNumber, effectiveDate, legalDescription, parcelNumber, settlementAgent, vestedPersons[], loanAmount ⚠️ verify (source wasn't a title report) |
> | 194 Preliminary Report (**NEW**) | `PRELIMINARY REPORT…titleLOOK.PDF` | ✅ titleIssueDate, vestedIn, entities[] |
> | 1118 Fraud Report | `Fraud Report.pdf` | ✅ RICH — fraudAlertStatus, fraudScore, applicants[], addressHistory |
> | 14 Business License (**NEW**) | `SOS - Poti Group LLC` | ✅ business, businessAddress, owner1, owner2, expirationDate |
> | 352 Underwriting Decision (**NEW**) | `Approval Form - Open Conditons` | ⚠️ SPARSE — only borrowers[]; kept bucket 3 |
>
> - **Source loans found for Group B** (were previously "no source doc anywhere"): 844 Green Card → 2602958672 · 843 SSN Card → 2607974430 · 816/1838 issued MI cert → 2606970248 (+5) · 1561 Evidence-of-Insurance policy → 2606970588/2607972570 · 1 Business Tax Return → 2602958672. Only **845 Passport** still not found.
>
> Config updated to `2026-09-23d` — **20 categories tested**. See [Still-untested categories](#still-untested--missing-categories-2026-09-23d).
>
> ---
>
> ## ✅ UPDATE 2026-09-23c — bootstrapped 4 more categories + `category_id` is back
> Ran `rns_ai_only` on loan 2605966814 (job `f23bd6d9-a8b4-46f1-a7a3-62e5bcaf02ab`) over 4
> previously-untested doc types, and probed loan 2609978332 (Sarah Poti) for more source docs.
> Two notable changes vs. 23b:
>
> - **`category_id` is now returned per document again.** The "no `category_id` / free-form" caveat
>   in the Headline below now applies to field *names* only — classification came back in this run.
> - **4 new categories extract richly** (all promoted to bucket 1):
>
> | Cat | Doc | Key fields extracted |
> |---|---|---|
> | 351 Transmittal Summary 1008 | `1008 - Transmittal Summary` | borrowers[], salesPrice, appraisedValue, loanAmount, interestRate, firstMortgagePi, hoaFee, taxesSpecialAssessments, totalMortgagePayment, proposed{...}, **agencyCaseNumber** |
> | 324 DU Underwriting Findings | `.FINDINGS` | loanType, noteRate, loanPurpose, DTIPercentage, recommendation, borrowers[], loanAmount, **ltv**, appraisedValue |
> | 1481 Property Tax | Baltimore Real Property bill | year, county, propertyAddress, firstInstallment..fourthInstallment, taxInstallments |
> | 1482 Property Profile (**NEW category**) | MD SDAT Real Property search | issuer.name, propertyAddress, estimatedMarketValue, installments |
>
> Config updated to `2026-09-23c` — **15 categories now tested**. See
> [**Still-untested categories**](#still-untested--missing-categories-2026-09-23c) at the bottom.
>
> ---
>
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
> *(2026-09-23c update: the extractor now DOES return a `category_id` per document again — this
> caveat is about field **names/shape**, not classification.)*

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
⚠️ **Still open (raise with TaskTile):** `titleCharges[].paidTo` + empty `settlementAgent` sub-fields (bucket 2).

---

## 167 — Closing Protection Letter  ❌ REGRESSED

Was rich in 6e2a2c0d (settlementAgent.name = title company, `fileNumber`, property) — the fix **collapsed it**:

| AWM schema field | Extracted? | Notes |
|---|---|---|
| `CPLDate` | ✅ | as `CPLDate` |
| `issuingAgent` | ✅ | as `issuingAgent` |
| `settlementAgent.*` / `fileNumber` / `property` | ❌ | **REGRESSED** — no longer returned (were present pre-fix) |

**Net:** CPL is no longer a useful Issue-B source, but the ALTA now covers that directly. Demoted to bucket 3; monitor — `ai_only` shape is unstable run-to-run.
⚠️ **Still open (raise with TaskTile):** collapsed to CPLDate + issuingAgent — `settlementAgent.*` / `fileNumber` / `property` **regressed** (bucket 3, fall back).

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
⚠️ **Still open (raise with TaskTile):** `sellerCreditAmount` (bucket 2, not present on tested contract).

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
⚠️ **Still open (raise with TaskTile):** `certificateNumber` / `miFileNumber` — belong on an issued cert (816/1838), not this quote.

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
⚠️ **Still open (raise with TaskTile):** flood zone / determination# / NFIP community+map **regressed** (bucket 3, fall back).

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
⚠️ **Still open (raise with TaskTile):** tradelines / collections / derogatory / public records / inquiries (bucket 2); SSN is **last-4 only**.

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
10. ⚠️ **Output shape is unstable run-to-run** — the regressions (#8/#9) landed in the same run that fixed everything else. `rns_ai_only` still ignores the AWM `content_schema` for field *names*; treat single results as directional and let shadow mode confirm.
11. **MI `certificateNumber` / `miFileNumber`** — re-test with an *issued* MI cert (this doc is a rate quote), not a gap on a quote.

---

## Still-untested / missing categories (2026-09-23d)

**Categories exercised (25 have a `tested_job`):** 1, 14, 117, 141, 167, 194, 200, 323, 324, 349, 351, 352, 522, 538, 816, 819, 843, 844, 984, 1118, 1479, 1481, 1482, 2044, 2168.
*(Of these, `816` and `1` "ran" but FAILED — see Section B; `352` sparse. The other 22 extract usefully.)*

### A. RAN 2026-09-23d on loan 2609978332 (Sarah Poti, job `badfc59f`) — ✅ DONE
ai_only classified these differently from the filenames — trust the returned `category_id`:

| Cat | Source doc | Result |
|---|---|---|
| **522** Title Commitment | `Conditional Commitment … Statement of Appraised Value` | ✅ **RICH** — address, vestedIn, vestedPersons[], loanAmount, titleCompany, escrowCompany, commitmentNumber, effectiveDate, legalDescription, parcelNumber, settlementAgent. ⚠️ source was an FHA commitment doc, not a title report — verify values. |
| **194** Preliminary Report *(new cat)* | `PRELIMINARY REPORT…titleLOOK.PDF` | ✅ titleIssueDate, vestedIn, entities[] — the actual prelim title report landed here (not 522). Good vesting source. |
| **1118** Fraud Report | `Fraud Report.pdf` | ✅ **RICH** — date, borrowers[], fraudAlertStatus, fraudScore, applicants[], addressHistory. |
| **14** Business License *(new cat)* | `SOS - Poti Group LLC` | ✅ issue, expirationDate, business, businessAddress, owner1, owner2. |
| **352** Underwriting Decision *(new cat)* | `Approval Form - Open Conditons` | ⚠️ **SPARSE** — only borrowers[]; conditions/status NOT extracted. Kept bucket 3. |

### B. RAN 2026-09-23d across 4 source loans — mixed (classifications differed again)
| Cat (returned) | Source loan / doc | Result |
|---|---|---|
| **844** Permanent Resident Card | 2602958672 / `Green card-FrontBack.pdf` | ✅ **`resident.idNumber`** + resident name/DOB/issueDate + expirationDate — **co-borrower Govt-ID (Issue A) now works for green cards** |
| **843** Social Security ID | 2607974430 / `Social Security Card.pdf` | ✅ **`owner.SSN`** (full) + owner name |
| **1479** Property Insurance *(new cat)* | 2606970588 / `… Homeowners Insurance Policy` | ⚠️ company.name, policyType, expirationDate, owners[], propertyAddresses[]; **missing** policyNumber, coverage, effectiveDate, agent |
| **816 / 1838** issued MI cert | 2606970248 / `Mortgage Insurance Certificate.pdf` | ❌ **misclassified as 375 Identifying Documentations** — only borrowers[]; cert#/premium NOT extracted → raise with TaskTile |
| **1** Business Tax Return | 2602958672 / `LLC Business Tax Return - 2024` | ❌ **misclassified as 2191 Form 1099-K** — only borrowers[]; corporation.name/financials NOT extracted → raise with TaskTile |
| **845** Passport | ❌ no source loan found in 80-loan scan | still open |

> **Note:** the actual homeowners POLICY classified as **1479 Property Insurance**, not **1561 Evidence of Insurance**.
> Remaining B blockers are now TaskTile-side (misclassification of MI cert + business return), not source-doc availability.

### C. Field-level gaps WITHIN tested categories
Now documented in-place under each category's **Net** line (see the ⚠️ **Still open** notes in
§2168, §167, §200, §141, §538, §117 above).
