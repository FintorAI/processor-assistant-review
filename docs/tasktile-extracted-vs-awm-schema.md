# TaskTile `rns_ai_only` — actual extracted data vs. AWM schema

**Source job:** `6e2a2c0d-ce82-493b-b03c-82745bb017b3` (pipeline `rns_ai_only`, status `success`)
**Client:** `78059b2d-479d-4fe6-90bc-a7a4f5006cb6` (PROD) · TaskTile **staging** · run 2026-09-23
**Docs:** 8 real eFolder PDFs from loan 2608976334 (Mora / Velasco purchase, 4 borrowers)
**Manifest:** `local/rns_results/2608976334_rns_ai_only_6e2a2c0d.json`
**AWM schema:** `tasktile.staging.cybersoftbpo.ai/api/category-sets/awm/{category_id}`

> ## Headline
> **`rns_ai_only` does free-form AI extraction — it does NOT follow the AWM `content_schema`.**
> The extracted key names differ almost entirely from the AWM schema (e.g. AWM `buyers[]` →
> extracted `escrow.buyers[]`; AWM `owner.idNumber` → not present; AWM `escrowCompany` → not
> present). So the comparison below matches by **concept/data**, not key name. Adding fields to the
> AWM set will **not** change this output — the fields must be added to the ai-only extractor itself.

Legend: ✅ data present · ⚠️ partial (some sub-fields or shape differs) · ❌ data missing

---

## 🚩 Critical gaps (our two live bugs)

| Bug | Category | AWM field | In this job's extraction? |
|---|---|---|---|
| **Issue A — Govt ID number** | 323 Drivers License | `owner.idNumber` | ❌ **MISSING on BOTH borrower DLs** (Iliana Velasco, Gerardo Torres) |
| **Issue B — Escrow / title company** | 2168 ALTA | `escrowCompany`, `titleCompany`, `settlementAgent.*` | ❌ **MISSING** (only `escrow.escrowOfficer` name + escrow office address) |
| Issue B — File / case # | 2168 ALTA | `fileNumber` | ⚠️ present as `escrow.escrowNumber` = `"14576 - SM"` (different key) |

> Note: the file # + title company **do** get extracted — but off the **CPL (167)**, not the ALTA
> (`settlementAgent.name = "Fidelity National Title Company"`, `fileNumber = "1500-2505564"`).

---

## 323 — Drivers License (×2)

| AWM schema field | Extracted? | Notes |
|---|---|---|
| `owner.idNumber` | ❌ | **License number never extracted** — the core ID bug |
| `owner.firstName/lastName/middleName` | ✅ | as `name.*` |
| `owner.suffix` | ❌ | |
| `owner.DOB` | ✅ | as `dateOfBirth` |
| `owner.sex` | ⚠️ | only on Torres DL (`sex`), not Velasco |
| `owner.address` | ✅ | as `address.*` (residence) |
| `owner.issueDate` | ❌ | |
| `state` (issuing state) | ❌ | no issuing-state field |
| `expirationDate` | ✅ | |

**Extra beyond schema:** Torres DL also returned `hairColor`, `eyeColor`, `height`, `weight`, `licenseClass`.

---

## 2168 — ALTA Settlement Statement

| AWM schema field | Extracted? | Notes |
|---|---|---|
| `escrowCompany` | ❌ | **missing** |
| `titleCompany` | ❌ | **missing** |
| `settlementAgent.{name,contact,phone,email,address,stLicenseId,contactStLicenseId}` | ❌ | no `settlementAgent` object at all |
| `fileNumber` | ⚠️ | present as `escrow.escrowNumber` `"14576 - SM"` |
| `buyers[].firstName/lastName` | ✅ | as `escrow.buyers[]` (4 buyers) |
| `buyers[].middleName/suffix` | ❌ | |
| `sellers[].firstName/lastName` | ✅ | as `escrow.sellers[]` |
| `sellers[].middleName/suffix` | ❌ | |
| `propertyAddress.*` | ✅ | as `escrow.propertyAddress.*` |
| `date` | ⚠️ | as `escrow.closingDate` / `printDateTime` |
| `titleCharges[].description` / `.paidTo` | ❌ | only `escrow.titleCharges[].amount` |
| `titleCharges[].buyerDebit` | ⚠️ | amount only, no debit/credit split |
| `recordingCharges[].description` | ❌ | only `.amount` |

**Extra beyond schema:** `escrow.escrowOfficer`, `settlementLocation.*`, `totalConsideration`, `deposits[]`, `encumbrances[]`, `prorations[]`, `commissions[]`, `escrowCharges[]`, `additionalDisbursements[]`, `approximateNetProceeds`.

---

## 167 — Closing Protection Letter

AWM schema is sparse (`CPLDate`, `issuingAgent`) — extraction returned **much more**:

| AWM schema field | Extracted? | Notes |
|---|---|---|
| `CPLDate` | ✅ | as `date` (`08/14/2026`) |
| `issuingAgent` | ⚠️ | as `settlementAgent.name` = `"Fidelity National Title Company"` |

**Extra beyond schema (valuable):** `settlementAgent.{address,phone}`, `fileNumber` = `"1500-2505564"`, `loan.number`, `buyer[]`, `property.address.*`. **This is where Issue B data actually lands.**

---

## 200 — Purchase Contract

| AWM schema field | Extracted? | Notes |
|---|---|---|
| `buyers[].firstName/lastName/middleName` | ✅ | + `signatureDate` |
| `buyers[].suffix` | ❌ | |
| `sellers[]` | ❌ | **no sellers extracted** |
| `datePrepared` | ✅ | |
| `signed` / `dateSigned` | ⚠️ | via `buyers[].signatureDate` |
| `propertyAddress.*` | ✅ | as `property.address.*` (zip OCR'd `90005` vs `90605`) |
| `purchasePrice` | ✅ | `$1,160,000` |
| `earnestMoney` | ✅ | as `initialDepositAmount` |
| `sellerCreditAmount` | ❌ | |
| `closingDate` | ⚠️ | relative (`"21 Days after Acceptance"`) |
| `buyersAgent.{name,company,phone,email,address,licenseId}` | ⚠️ | `agents[]` has name + `licenseNumber` + `brokerageFirm`; no phone/email/address |
| `sellersAgent.*` | ❌ | agents not split into buyer/seller |

**Extra:** `property.assessorParcelNo`, `purchaseAgreement.loanAmount.first`, `balanceOfDownPayment`, `terms.occupancyType`.

---

## 141 — Mortgage Insurance  *(doc was an MI Quote, not a certificate)*

| AWM schema field | Extracted? | Notes |
|---|---|---|
| `company.name` / `company.address` | ❌ | (issuer Radian only in contact block) |
| `certificateNumber` | ❌ | |
| `miFileNumber` | ❌ | |
| `totalPremium` | ❌ | |
| `upfrontPremiumAmount` / `monthlyPremiumAmount` | ❌ | |
| `premiumType` / `frequency` | ❌ | |
| `renewal.*` / `cancelAtPercent` | ❌ | |
| `expirationDate` | ⚠️ | as `quote.validThrough` |
| `propertyAddress.*` | ✅ | as `property.*` |

**Extra:** rich quote data — `loanDetails.{ltv,cltv,miCoverage,loanAmount,auResponse}`, `borrower.{creditScore,dti,totalMonthlyIncome}`. The premium/certificate fields are absent likely because the source is a **rate quote**, not an MI certificate — re-test with an issued MI cert.

---

## 538 — Flood Certification

AWM schema is sparse (`date`, `lender`, `borrowers[]`) — extraction returned **much more**:

| AWM schema field | Extracted? | Notes |
|---|---|---|
| `date` | ⚠️ | as `determinationDate` |
| `lender` | ✅ | as `loanInformation.lender.*` |
| `borrowers[]` | ⚠️ | single `clientName` (middleName mis-parsed as `"Iliana Velasco"`) |

**Extra (valuable):** `floodZone` (X), `determinationNumber`, full NFIP community/map data, collateral lat/long.

---

## 117 — Credit Report

Scores + identity extracted well; the large AWM tradeline/derogatory structures did **not** extract.

| AWM schema field | Extracted? | Notes |
|---|---|---|
| `scores[]` | ✅ | as `borrower.creditScore[]` / `coBorrower.creditScore[]` (tri-merge) |
| `applicant1/2.last4SSN` | ✅➕ | extraction returned **full SSN** (`borrower/coBorrower.socialSecurityNumber`) |
| `creditReferenceNumber` | ⚠️ | closest is `loan.reportId` |
| `creditTradeLines[]` | ❌ | **no tradelines** |
| `collectionAccounts[]` | ❌ | |
| `derogatoryAccounts[]` | ❌ | |
| `publicRecords[]` | ❌ | |
| `inquiries[]` | ❌ | |
| `tradeSummary[]` | ❌ | |
| `alerts[]` | ❌ | |
| `company.name` | ❌ | |

**Extra:** `loan.{loanNumber,ordered,released,repositories}`, `invoiceSummary.*`.

---

## Summary — what to raise with TaskTile

1. **`owner.idNumber` (Drivers License / Govt ID) is never extracted** — top priority (Issue A). Confirmed missing on both borrower DLs in this job.
2. **ALTA (2168) does not return `escrowCompany` / `titleCompany` / `settlementAgent`** — only escrow officer + escrow # (Issue B). The company + file # exist but on the CPL, not the ALTA.
3. **`rns_ai_only` ignores the AWM `content_schema` entirely** (free-form extraction, non-schema key names, shape varies per document). Schema additions on the AWM set do not reach the ai-only output — they must be added to the ai-only extractor.
4. **Credit Report tradelines / collections / derogatory / public records / inquiries not extracted** — only scores + SSN + invoice.
5. **MI premium / certificate fields absent** — re-test with an issued MI certificate (this doc was a rate quote).
6. **Titled charges (ALTA) return `amount` only** — no `description` / `paidTo` / debit-credit split.
