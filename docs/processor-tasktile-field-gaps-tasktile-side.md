# processor-assistant-review field gaps — **TaskTile side** (SBIQ schema additions)

**This file = fields the agent needs that are NOT in the SBIQ `content_schema` at all.** Until then the agent
falls back to LandingAI OCR for each one.

Sibling doc: [`tasktile-doc-writes-investigation.md`](tasktile-doc-writes-investigation.md) — the
two live-confirmed failures (co-borrower Govt ID, Escrow Company + Case #) that motivated this.

Verified against SBIQ `staging`, 2026-09-21.

> ## 🚨 Live smoke test 2026-09-23 — the AWM schema is present but `rns_ai_only` does NOT use it
>
> The P1/P2/P3 fields below **are** defined on the **AWM category set**
> (`tasktile.staging.cybersoftbpo.ai/api/category-sets/awm/{id}`) — 23 cats differ from base,
> camelCase keys. **But a real end-to-end job proved `rns_ai_only` ignores that `content_schema` at
> extraction time.** Passing `categories_url=…/category-sets/awm` changed nothing; the returned
> manifest uses a generic built-in schema. So "present in AWM" ≠ "extracted."
>
> **Proof (ALTA 2168):**
>
> | AWM `category-sets/awm/2168` schema | What `rns_ai_only` returned |
> |---|---|
> | `escrowCompany`, `titleCompany` | ❌ absent — only `escrowOfficer` (a person: "Nikki S Bott") |
> | `settlementAgent{name,contact,phone,email,…}` | ❌ absent |
> | `fileNumber` (the file/case #) | ❌ absent |
> | `buyers[]`, `sellers[]` (arrays) | different shape: singular `buyer` / `buyerCo` / `seller` |
> | `titleCharges[].buyerDebit` / `paidTo` | different: generic `loan.titleCharges[].description` / `amount` |
>
> Net (revised):
> - **Issue B (Escrow Co + Case #, 2168): NOT fixed on the ai-only path** — fields in schema, not extracted.
> - **Issue A (co-borrower Govt ID, `323/845/844.idNumber`): untested** — the ID doc tested classified
>   as `2044` (address search), not a Govt ID. Needs a real license/passport.
> - **`xpath` is not the blocker** (TaskTile confirmed ai-only ignores it) — the blocker is that the
>   **whole AWM `content_schema` is not honored** by `rns_ai_only`.
> - **Mechanics confirmed:** one job / multiple docs, camelCase output, `attachment_id` round-trip,
>   ~50s no-HIL.
> - **Classification-only (LandingAI stays primary):** 294, 447, 1118, 1800, 538, 397, 2087, 578, 33 —
>   see [`tasktile-classification-only-docs.md`](tasktile-classification-only-docs.md).
>   *(522, 1561, 162, 333, 167 are PARTIAL — they carry data; see that doc.)*
>
> **Primary ask to TaskTile:** does `rns_ai_only` honor the AWM category-set `content_schema`? If not,
> these additions must be baked into the ai-only extraction schema (or those cats run `rack_and_stack`).
> Full breakdown + job ids/manifests: `docs/tasktile-integration-plan.md` →
> "Live smoke test results (2026-09-23)".
>
> **Field-by-field extracted-vs-AWM comparison** (8 real docs, job `6e2a2c0d`):
> [`tasktile-extracted-vs-awm-schema.md`](tasktile-extracted-vs-awm-schema.md).

---

## ⚠️ Category mappings are ASSUMED — to confirm

Every `our doc → SBIQ id` here is a **name-match guess**, not confirmed against what TaskTile
actually classifies each doc as. The schema gap depends on the category, so a wrong id = wrong "fields
to add."

**Mappings we are least sure about (to confirm these first):**

| Our doc type | Guessed id (category name) | Why unsure |
|---|---|---|
| Flood Certificate | `1800` Flood Hazard Determination | vs `538` Flood Certification — **both confirmed classification-only** (neither has `flood_zone`/`determination_number`), so the gap is the same either way; still confirm which one the bucket classifies as |
| MI Certificate | `141` Mortgage Insurance | vs `816` PMI Certificate / `1838` FHA Connection Mortgage Insurance Certificate — three plausible categories |
| Flood Insurance | `1064` Flood Insurance Information | auto-matcher had mislabeled to a disclosure; confirm it lands here |
| VA Documents Misc | `2198` VA Documentation | broad bucket — may split across several VA ids |
| FHA Transmittal | `1046` FHA Loan Underwriting and Transmittal Summary | vs `351` Transmittal Summary 1008 — easy to cross-classify |
| SSN Verification | `484` Social Security Number Certification | may collapse into `843` Social Security ID |
| VA 26-1820 | *(none)* | no SBIQ category found by name at all |

## Corrected SBIQ id mapping *(assumed as well — to confirm)*

| Our doc type | Correct SBIQ id | Note |
|---|---|---|
| ESS / Estimated Settlement Statement | **2168** ALTA Settlement Statement | Encompass bucket maps here |
| Driver's License | **323** Drivers License | |
| SSN Card | **843** Social Security ID | has `owner.SSN` |
| SSN Verification | **484** Social Security Number Certification | classification-only |
| Purchase Agreement | **200** Purchase Contract | |
| MI Certificate | **141** Mortgage Insurance | (816 PMI / 1838 FHA MI are classification-only) |
| FHA Transmittal Summary | **1046** FHA Loan Underwriting and Transmittal Summary | |
| Transmittal Summary (conv) | **351** Transmittal Summary 1008 | |
| Underwriting (DU / LP) | **324** DU Underwriting Findings / **2104** LP Underwriting Findings | |
| VA Documents Misc | **2198** VA Documentation | |
| Flood Insurance | **1064** Flood Insurance Information | |

---

## Priority 1 — the two live bugs

| Doc (SBIQ id) | `content_schema` today | Fields to ADD | Consumed by (our tool → substep) |
|---|---|---|---|
| **ALTA Settlement Statement (2168)** | 18: `date`, `buyers[]`, `sellers[]`, `propertyAddress` | `escrow_company`, `title_company`, settlement_agent {`name`,`contact`,`phone`,`email`,`address`,`st_license_id`,`contact_st_license_id`}, **`file_number`** (the "File #" → Escrow Case #), plus fee lines `title_charges[]`/`recording_charges[]` and the fee/side fields below | `review_file_contacts.py` (1.2) Escrow Company incl. `referenceNumber` / field 186 |
| **Drivers License (323)** 👥 | 8: `owner{firstName,middleName,lastName,suffix,DOB}`, `state`, `expirationDate` | **`owner.idNumber`** (license #); optional `owner.sex`, `owner.address`, `owner.issueDate` | `review_borrower_summary.py` (2.1) → 5053/5054 for **both** borrowers |
| **Passport (845)** 👥 | 7: `owner{…}`, `dateOfExpiration` | `owner.idNumber` (passport #); optional `owner.issueDate` | same (ID fallback) |
| **Permanent Resident Card (844)** 👥 | 7: `resident{…}`, `expirationDate` | `resident.idNumber` (A#/USCIS #); optional `resident.issueDate` | same (ID fallback) |

> 👥 = **co-borrower-critical**: all three ID types must resolve per-attachment so a co-borrower's
> ID in the same bucket is captured. Names/DOB/expiry are already covered — the **only** blocker
> for writing the Govt ID field (borrower **and** co-borrower) is the missing **`idNumber`**.
> TaskTile extracts each attachment separately, so two ID PDFs in one bucket both come through —
> which is exactly what fixes the co-borrower case.

## Priority 2 — high-value docs that are classification-only today (biggest ROI)

Each of these the agent relies on for real field writes, and SBIQ currently returns names only.

| Doc (SBIQ id) | `content_schema` today | Fields to ADD | Consumed by |
|---|---|---|---|
| **Purchase Contract (200)** ⚑ | Confirmed 15 fields: `buyers[]` (names), `signed`, `datePrepared`, `dateSigned`, `propertyAddress` — **names + address only** | `purchase_price`, `closing_date`, `seller_credits`/`earnest_money`, `sellers[]` (seller name), buyers_agent{`company`,`name`,`license`,`phone`,`email`}, sellers_agent{…} | `review_borrower_summary` (2.1), `review_file_contacts` (1.2), URLA/itemization |
| **FHA Loan Underwriting & Transmittal (1046)** | `borrowers[]`, `propertyAddress` | `sales_price`, `appraised_value`, `loan_amount`, `interest_rate`, `first_mortgage_pi`, `monthly_mip`, `upfront_mip`, `hoa_fee`, `taxes_special_assessments`, `total_mortgage_payment`, proposed {`hazard_insurance`,`mortgage_insurance`,`taxes`}, `agency_case_number` | `update_fha_management.py`, `update_transmittal_summary.py` |
| **Transmittal Summary 1008 (351)** | `borrowers[]`, `propertyAddress` | same monetary set as 1046 (minus MIP) | `update_transmittal_summary.py` |
| **Mortgage Insurance (141)** | `company.name`, `frequency`, `totalPremium`, `expirationDate`, `propertyAddress` | **`certificate_number`**, `mi_file_number`, `premium_type`, `upfront_premium_amount`, `monthly_premium_amount`, renewal {`first_percent`,`first_months`,`second_percent`,`second_months`}, `cancel_at_percent`, `company.address` | `review_borrower_summary` (MI), itemization |
| **Flood Insurance Information (1064)** | `address`, `company.name`, `expirationDate` | `flood_policy_number`, `flood_coverage_amount`, `flood_premium`, `flood_effective_date`, `flood_zone`, `nfip_map_number`, contact {`phone`,`address`} | `review_flood_hazard_insurance.py` |
| **SSPL / Pre Application Worksheet (1836)** | `borrowers[]` | `loan_number`, `property_address`, `loan_amount`, `loan_type`, `loan_purpose`, `submission_date` | SSPL presence/data checks |
| **VA Documentation (2198)** | `borrowers[]` | `veteran_name`, `va_case_number`, `entitlement_code`, `basic_entitlement`, `additional_entitlement`, `funding_fee_exempt`, `branch_of_service`, `dates_of_service` | VA form tools |
| **DU / LP Underwriting Findings (324 / 2104)** | 324: `loanType,noteRate,loanPurpose,DTIPercentage,recommendation,amortizationType,propertyAddress`; 2104: `borrowers[]` only | `borrowers[]` name, `coborrower` name, `loan_amount`, `ltv`, `appraised_value` (324); the full set on 2104 | `run_pre_checks`, review tools |
| **FHA Case Number Request Form (996)** | `borrowers[]` | `agency_case_number`, `case_assigned_date` | `update_fha_management.py` |
| **Change of Circumstance CD (1833)** | `signed`, `borrowers[]`, `dateSigned`, `propertyAddress` | `coc_reason`, `coc_date`, `coc_disclosed_by`, `coc_original_values`, `coc_revised_values` | COC checks |
| **Credit Invoice (458)** | `date`, `company.name`, `borrowers[]` | `credit_report_fee`, `invoice_number`, `bundled_charges` | itemization (credit fee) |
| **Trust Documents (1007)** | `borrowers[]` | `vesting_name` (full trust title) | vesting checks |

> ⚑ **Purchase Contract (200): don't double-file.** The same ask is already raised in the
> LG-LOAEncompass handoff (`purchase_price`, `closing_date`, agents). One SBIQ schema serves both
> repos — confirmed here as still names + address only.

## Priority 3 — small single-field adds (high signal, low effort)

| Doc (SBIQ id) | `content_schema` today | Field to ADD | Consumed by |
|---|---|---|---|
| **Credit Report (117)** | 145 (names, last4 SSN, scores, tradelines, AKAs, employer, `reportIssued`) | `credit_reference_number` (reissue/reorder id); optional full SSN vs. `last4SSN` | credit reissue |
| **SSN Verification (484)** | `borrowers[]` only | `owner.SSN` — **or skip**: SSN Card (843) already carries it | SSN cross-check |

---

## Remaining doc types (exhaustive) — field-level, assumed ids

Completes the picture for the other ~24 registry doc types (all ids **assumed**, see caveat above).
⭐ = priority (high-value data the agent writes today via LandingAI). Same pattern holds: unless
noted "covered/partial", the schema is **classification-only** (`borrowers[]` + property) and every
data field is a gap.

| Doc (assumed SBIQ id) | `content_schema` today | Status / fields to ADD | Pri |
|---|---|---|---|
| **Closing Disclosure (819)** | `lender`, `borrowers[]`, `withProjectedPayments` | GAP — needs the full CD fee/section set (85 fields: closing/note dates, section A–C fees + payees, escrow/prepaid, `settlement_agent_name`, `escrow_number`, seller credits, MIC ref) | ⭐ |
| **Initial CD (819)** | same as 819 | GAP — `loan_amount`, `interest_rate`, `apr`, `monthly_pi`, `appraisal_fee`, `credit_report_fee`, `processing_fee` | ⭐ |
| **Loan Estimate (984)** | `lender`, `borrowers[]` | GAP — all fees/terms: `loan_amount`, `interest_rate`, `apr`, `discount_points`, `origination_charges`, `appraisal_fee`, `credit_report_fee`, `title_company`, `settlement_agent`, `rate_lock_date`, `lock_expiration_date`, `impounds_taxes/insurance`, `date_issued` | ⭐ |
| **Title Report / Commitment (522)** | `address`, `vestedIn`, `vestedPersons[]`, `loanAmount` | PARTIAL — vesting covered; ADD `title_company`, `settlement_agent`, `escrow_company`, `issuing_agent`, `commitment_number`, `effective_date`, `legal_description`, `parcel_number`/`apn` | ⭐ |
| **Evidence of Insurance / Hazard (1561)** | `owner`, `company.name`, `policyType`, `totalPremium`, `expirationDate`, `propertyAddress` | PARTIAL — company/premium/expiry; ADD `policy_number`, `effective_date`, coverage {`dwelling`,`deductible`,`wind_hail`}, agent {`name`,`phone`,`email`,`address`}, `mortgagee_clause` | ⭐ |
| **Appraisal Report (162)** | `value`, `appraisalDate`, prior-sale | GAP — `appraised_value`(≈value), `property_type`, `year_built`, `parcel_number`, `flood_zone`, appraiser {`name`,`company`,`address`,`phone`,`email`}, condition flags (as-is / subject-to) | ⭐ |
| **Appraisal Invoice (33)** | `loanNumber`, `propertyAddress`, `appraisalCompany.name` | GAP — `appraisal_fee`, `total_invoice_amount`, `invoice_number`, `invoice_date`, `reinspection_fee`, `balance_due` | |
| **Appraisal Acknowledgement (1033)** | `lender`, `borrowers[]` (+ signed/dateSigned) | PARTIAL — signature/date; ADD `appraisal_delivery_date`, `sender_email`, `recipient_emails` | |
| **VA Loan Summary (294)** | `veterans[]` names | GAP — full monetary set (`sales_price`, `appraised_value`, `loan_amount`, `interest_rate`, PITI breakdown, proposed escrows) | |
| **VA Certificate of Eligibility (447)** | `veteran`, `dateIssued` | PARTIAL — name/date; ADD `va_case_number`, `entitlement_code`, `basic/additional_entitlement`, `funding_fee_exempt` | |
| **Fraud Report (1118)** | `date`, `borrowers[]` | GAP — `fraud_alert_status`, `fraud_score`, `aka`(borrower+coborrower), `address_history`, `ssn` | |
| **Flood Certificate (1800; or 538?)** | `borrowers[]` | GAP — `flood_zone`, `in_sfha`, `community_number`, `map_panel`, `map_date`, `determination_date`, `flood_determination_number`, `order_number` | |
| **Payoff Statement (397)** | `payoffStatementDate` | GAP — `creditor_name`, `account_number`, `payoff_amount`, `per_diem`, `good_through_date`, `payee_address` | |
| **Home Inspection (2087)** | `borrowers[]` | GAP — `inspection_date`, `inspector_name`, `inspector_company`, `result`, `major_issues` | |
| **Pest Inspection (578)** | `date`, `company.name` | PARTIAL — company/date; ADD `pest_found`, `treatment_required`, `result` | |
| **Approval Form (333)** | `decisionDate`, `underwritingStatus` | PARTIAL — status/date; ADD `prior_to_docs_conditions`, `prior_to_funding_conditions` | |
| **Closing Protection Letter (167)** | `CPLDate`, `issuingAgent` | PARTIAL — date + settlement agent; ADD `lender_name`, `cpl_title_underwriter` | |
| **Initial 1003 / URLA (349)** | 444 fields — full URLA (names, SSN, employment, assets, REO, declarations, vesting) | **COVERED** — richest schema; no ask. Map on our side. | |
| **Tax Returns – Business (1)** | 43 fields (1120: `corporation.name`, EIN, officers, financials) | **COVERED** — `corporation.name` = our `business_name`; no ask | |
| **VA Funding Fee Worksheet (820)** | `borrowers[]` | no fields consumed — **no ask** | |
| **VA Nearest Living Relative (296)** | `lender`, `borrowers[]` | no fields consumed — **no ask** | |
| **VA 26-1820** | — | **no SBIQ category found** by name; confirm the real category | |