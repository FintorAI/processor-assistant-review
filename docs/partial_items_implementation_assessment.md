# Partial-Status Checklist Items — Implementation Assessment

_Last updated: 2026-07-03_

Source of truth: the canonical Google Sheet (pulled via `scripts/sheet_sync.py` into
`docs/processor_checklist_code_reality.csv`). At the time of writing there are **196 rows**,
of which **41 are `Partial`**.

"Easy" here means **pure rule logic on data we already extract** — no new server-side
extraction schema and no external integration (email / dashboard / browser / eFolder upload).

---

## Tier 1 — Easy (data already extracted, rule-only) — ✅ SHIPPED

All four implemented and pushed (commit `f8d6d42`, branch
`feat/checklist-partial-gaps-integration`); sheet + CSV synced to **Implemented**.
All inputs already existed in `required_docs.json` and are read into agent state —
no new schema, no `factory-reset` (both tools `FACTORY-LOCK: true`).

| Item | Gap closed | What shipped | Home |
|---|---|---|---|
| **05 W-2 #2** — Pay stubs dated within 30 days | 30-day recency not enforced | Most-recent `pay_date` recency (AUS 30d); stale / unverifiable flags | `review_urla_employment.py` |
| **05 W-2 #3** — Verify name/employer on stubs | stub identity not done (employer VOE-ref done) | Per-stub `borrower_name` + `employer_name` reconciled vs 1003/VOE (token match) | `review_urla_employment.py` |
| **05 W-2 #8** — VOE consistency with W-2 | W-2 consistency not done (VOE-doc done) | W-2 `employer_name`/`borrower_name` consistency vs VOE/1003 + tax-year surfacing | `review_urla_employment.py` |
| **08 #2** — Transaction history to current | recency-to-current already done; **continuity between statements** not enforced | Coverage-continuity: gap > 35d between consecutive `statement_period_start/end` | `review_urla_assets.py` |

Notes:
- Paystub / bank-statement **presence** is already flagged in `run_pre_checks` (1.1), so the
  new rules only add recency / identity / continuity checks and never re-flag "missing".
- Bank-statement **recency-to-current** (stale > 60d Conv / 30d FHA) already exists in
  `review_urla_assets`; 08 #2's remaining piece (now shipped) is **gap-in-coverage between statements**.

---

## Resolved by earlier steps — marked Implemented on the sheet (2026-07-03)

These were flagged Partial but are already covered by rules shipped in an earlier step;
no new code — status corrected on the sheet to reflect existing coverage.

| Item | Covered by |
|---|---|
| **19 #4** — File Contacts / AKAs | Applicant AKA reconcile (§3.1) already parses Credit Report AKAs and writes-if-blank to URLA aliases 1869/1874 (`review_borrower_summary.py`); file contacts reviewed in `review_file_contacts.py` |
| **07 #2** — Self-employed name/address/SSN | Same identity checks as every borrower: name vs Driver's License + SSN vs Credit Report (§1.6/§3.1) in `review_borrower_summary.py`; property address via USPS `address_validation`. Self-employed uses the same reads — no SE-specific gap |
| **09 #5** — Update Loan Stakeholders | Buyer's/seller's real estate agents are synced from the Purchase Contract OCR by `_sync_contacts` in `review_file_contacts.py` (`BUYERS_AGENT` / `SELLERS_AGENT` from `buyer_agent_*` / `seller_agent_*`, also fed by ESS broker columns). Same purchase-contract source as the listing/selling company block — no new code, status corrected on the sheet |

## Tier 2 — Medium (wiring — data IS extracted)

| Item | Status |
|---|---|
| **02 #2** — Title Insurance Company (license + order #) | ✅ **SHIPPED** — `_sync_title_company` in `review_file_contacts.py` writes the `TITLE_INSURANCE_COMPANY` file contact from the Title Report: company name, company license (`bizLicenseNumber`), commitment/order # (`referenceNumber`), issuing agent/contact (`personalLicenseNumber`), phone/email/address. Create-or-overwrite-differing with `info-overwrite` flags; no-op when no title data. Reference doc = **Title Report / commitment**. |
| **06 #3** — Receipt of income on bank statements | ✅ **SHIPPED (Tier 3 extraction + rule)** — Added a structured `recurring_deposits[]` field (source, income_type, amount, date, frequency) to the **Bank Statement** server schema (catchingDoc), targeting non-payroll recurring income and explicitly excluding payroll (→ `payroll_deposits`) and one-time deposits (→ `bank_large_deposits`). Registered in `required_docs.json`. `review_urla_other_income.py` now cross-checks the stated other-income type (Field 172) against `recurring_deposits[]`: confirms receipt (info), reconciles a monthly deposit vs stated $/mo (Field 173) and warns on >10% variance, or flags "not evidenced" (info). Runs only when a bank statement is in the file. **Extraction validated** on 2604964148 (negative/exclusion case clean); positive path unit-tested — no test loan in the sample has both other-income and bank statements (SS/pension borrowers verified via award letters, no statements uploaded). |
| **03 #13** — Confirm Name & Vesting for Title Commitment | ✅ **SHIPPED** — `update_urla_lender.py` reconciles field 33 against the Title Report extracted `final_vesting`. The tenancy phrase is normalized to a standard Encompass manner-held value (`_title_manner_from_vesting`); when it differs from LOS, field 33 + URLA.X138 are **overwritten with the normalized standard** (e.g. "tenancy in the common" → "Tenancy in Common"; `info-overwrite`, no warning). A title value that does **not** map to a recognized standard is **left unchanged + warned** (never write a non-standard value). Agreement → confirm; Title Report takes precedence over the profile computation. Applicant surnames are also confirmed against the vesting string (warn only). |
| **08 #10** — Complete asset screen (Page 3) | ✅ **SHIPPED (write-blank-only)** — Existing 2a/VOD entries matched to a bank statement / asset doc but with **blank subfields** are completed via new `encompass_client.update_vod_accounts` (PATCH `/vods/{id}`, URLA-2020 items schema) + `encompass_io.update_vods`: account type, cash/market value, account number, account holder are filled from the extracted doc. **Only blank fields are written — a populated value is never overwritten** (balance discrepancies still warn). Legacy `accountInformation` VODs are surfaced for manual completion. Honors `DEV_MODE.dry_run`. Complements the existing `add_vods` path for entirely-missing accounts. Also fixed `_parse_float` to tolerate currency strings so balances parse. |
| **03 #12** — USPS multi-doc address cross-ref | ✅ **SHIPPED (Flood leg deferred)** — `review_borrower_summary.py` now cross-references the subject address against the USPS-validated address across **Tax Certificate** (`tax_property_address`), **Evidence of Insurance** (`insured_location` / `hazard_insurance_address`), and **Title Report** (`property_address`, source-filtered via `_sourced_property_address`) — on top of the pre-existing Purchase Contract + Appraisal checks. Mismatch → warning, match → info; never auto-corrected. **Flood cert has no extracted address** (only company/policy/zone) → that one leg stays Tier 3 (== 12 #2). |
| **03 #17** — Green card flag for Resident Alien | ✅ **SHIPPED (flag)** — `review_urla_page1.py` now warns, for borrower **and** co-borrower, when citizenship is **Permanent** or **Non-Permanent Resident Alien**, requesting the right doc (Green Card / I-551 for Permanent; valid visa or EAD / I-766 for Non-Permanent). Citizenship is still never auto-populated. The *automated sending* of the green-card request remains an external workflow (Tier 4). |

---

## Tier 3 — Blocked: true extraction gaps (need new schema fields)

| Item | Missing extraction |
|---|---|
| **05 W-2 #4** — OT/bonus/holiday variances | paystub has `gross_pay_this_period` only, no OT/bonus/holiday line items |
| **03 #8 / 04 #6** — Liabilities vs credit report | credit_report extracts scores/SSN/DOB/AKA only, **no liability lines** |
| **03 #9** — Alimony/child-support isolation | needs liability-type detail |
| **03 #10** — REO debts tied to properties | needs credit-report mortgage lines (same cluster as #8) to link each REO to its mortgage |
| **03 #11** — Correct mortgage on Financial Info | needs credit-report mortgage lines / mortgage-statement extraction |
| **07 #4** — Schedule E properties / rental income | no Schedule E extraction |
| **08 #5** — Transfers between accounts | needs transaction-level data |
| **12 #2** — Applicant/property vs flood cert | flood cert extracts company/policy/zone only (no address/name) |
| **06 #1** — Awards letters / 1099s | no 1099 doc type |

---

## Spec — Credit-report liabilities cluster (03 #8 / #9 / #10 / #11, 04 #6)

These four items share **one unblock**: the credit-report doc currently extracts no
tradeline lines. Grounded in the EC screenshots of loan 2605968111 (2c Liabilities grid
+ VOL detail):

**Schema (add to the credit-report extraction, catchingDoc — same schema-first approach as 06 #3):**
`liabilities[]`, one object per tradeline:
- `creditor` — e.g. "MISSOURI HIGHER EDUC", "DISCOVER CARD"
- `account_number` — **the dedup key**; name alone is not enough (two DISCOVER rows on this file are *different* cards: `$12,286` vs `$7,426`)
- `account_type` — Installment / Revolving / Mortgage / HELOC / Open / Lease
- `balance`, `monthly_payment`, `months_remaining`, `past_due`, `credit_limit`
- `owner` — Borrower / CoBorrower / Joint (matches VOL "Account in Name of")
- `status` / `comment` — carries "authorized user", "student loan", "deferred", "paid/closed", "collection"
- `is_mortgage` + `secured_property_address` (for #10/#11 REO linkage)

**Reconciliation rule (new, in `review_urla_liabilities.py`):** match each extracted tradeline
to a VOL row **by account-number last-4 (+ creditor + balance)** and flag:
- tradeline with a balance **missing** from the VOL (liability not entered),
- **authorized-user** tradelines that are still counted in DTI (should typically be Omit),
- **duplicates** only when account numbers match (never by creditor name),
- **deferred student loans** with no payment (needs a qualifying payment),
- VOL rows **Excluded / Payoff** without a matching documented reason (extends today's checks).
- **#10/#11:** link each `is_mortgage` tradeline to an REO property (VOL "Subject Property" /
  "Mortgage Type" / "NOT secured to Subject Property" flags) and flag REO properties whose
  mortgage is missing or mismatched.

**#9 note:** alimony/child-support **paid** lives in URLA **2d "Other Liabilities and Expenses"**
(already surfaced via `read_other_liabilities`); **received** is other income (§5.2). On
2605968111 2d is "Does not apply" (nothing to isolate). The only enhancement is explicitly
labeling alimony vs child support when a 2d row exists — low value, no extraction needed.

---

## Tier 4 — Blocked: external integration, not a rule

01 #11 (title order email), 10 #2 / #3 (title package / tax-cert via dashboard),
11 #9 / 13 #11 / 21 #5 (eFolder upload — dry-run), 17 #2 (doc re-order),
17 #10 (HUD Addendum upload), 21 #1 (upload conditions), 03 #17 (green-card request workflow).

---

## Full Partial list (41) for reference

| Section | # | Item | Tier |
|---|---|---|---|
| 01 Loan Received | 11 | Order title docs (Processor Workflow) | 4 |
| 02 File Contacts & Vesting | 2 | Update Title Insurance Company (license + order #) | ✅ 2 |
| 03 URLA / 1003 | 8 | Liabilities match credit report; paid/omitted | 3 |
| 03 URLA / 1003 | 9 | Child support / Alimony noted | 3 |
| 03 URLA / 1003 | 10 | Schedule of real estate; debts tied to properties | 3 |
| 03 URLA / 1003 | 11 | Correct mortgage on Financial Info screen | 3 |
| 03 URLA / 1003 | 12 | USPS Address Verification multi-doc cross-ref | ✅ 2 (Flood leg → 3) |
| 03 URLA / 1003 | 13 | Confirm Name and Vesting for Title Commitment | ✅ 2 |
| 03 URLA / 1003 | 17 | US Citizen / Green card for Resident Alien | ✅ 2 (flag); request workflow → 4 |
| 03 URLA / 1003 | 18 | Government screen (CAIVRS, Housing Act, LAPP) | 3 |
| 04 Credit Report | 6 | Compare Liabilities/Debts vs Encompass | 3 |
| 05 Income W-2 | 2 | Pay stubs per AUS, dated within 30 days | **1 ✅** |
| 05 Income W-2 | 3 | Verify name/address/employer on stubs | **1 ✅** |
| 05 Income W-2 | 4 | Paystub variances base/OT/bonus/holiday | 3 |
| 05 Income W-2 | 7 | Obtain gap of employment letters | 4 |
| 05 Income W-2 | 8 | VOE consistency with paystubs and W-2 | **1 ✅** |
| 06 Income SS/Pension | 1 | Awards letters and 1099s per AUS | 3 |
| 06 Income SS/Pension | 3 | Verify receipt on bank statements | 2 |
| 07 Self-Employed | 2 | Verify borrower name, address, SSN | ✅ (via §1.6/§3.1) |
| 07 Self-Employed | 4 | Schedule E properties / rental income | 3 |
| 08 Assets | 2 | Transaction history to current | **1 ✅** |
| 08 Assets | 5 | Transfers between accounts | 3 |
| 08 Assets | 10 | Complete asset screen (Page 3) | ✅ 2 |
| 09 Purchase Contract | 5 | Update Loan Stakeholders | ✅ 2 |
| 10 Title Order | 2 | Confirm completeness (CPL, chain, wire, tax cert) | 4 |
| 10 Title Order | 3 | Update Encompass w/ Tax Cert | 4 |
| 11 Appraisal | 9 | Upload appraisal, 442, condo invoices | 4 |
| 12 Flood Cert | 2 | Confirm Applicant/Property match Encompass | 3 |
| 12 Flood Cert | 4 | Confirm Flood Zone Designation | (near-done) |
| 13 Hazard Insurance | 11 | Upload evidence of insurance | 4 |
| 17 Submit to UW | 2 | Docs in correct buckets; most recent on top | 4 |
| 17 Submit to UW | 3 | Update 1003 pages with verified data | (broad, ongoing) |
| 17 Submit to UW | 5 | Final 1008, FHA Transmittal, VA Analysis | 3 |
| 17 Submit to UW | 8 | FHA/VA/USDA/Fannie/Freddie data screens | 3 |
| 17 Submit to UW | 10 | Upload final 1003 + HUD Addendum | 4 |
| 19 Loan Approval | 2 | Lock Confirmation; qualifying rate vs Transmittal | (near-done) |
| 19 Loan Approval | 4 | File Contacts / AKAs | ✅ (via §3.1) |
| 20 CD Request | 3 | Verify 2015 Itemization screen | 3 |
| 21 Condition Handling | 1 | Upload conditions to eFolder; mark Ready | 4 |
| 21 Condition Handling | 4 | Enter signing date and wire request | (near-done) |
| 21 Condition Handling | 5 | Upload docs to be signed at closing | 4 |
