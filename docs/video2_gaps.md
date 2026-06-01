# Video 2+ Gaps — `processor-assistant-review`

**Source:** `notes.txt` lines 397–545
**Repo:** `/Users/naomi/Desktop/FINTOR/processor-assistant-review`
**Scope searched:** `definitions/`, `output/tools/`, `shared/`, `factory/`

**Legend:** ✅ IMPLEMENTED · 🟡 PARTIAL · ❌ NOT IMPLEMENTED · 🔒 FACTORY-LOCK

---

## Status matrix

### Step 2 — Borrower Summary (`review_borrower_summary` 🔒 true)

| Item | Status | Evidence |
|---|---|---|
| Cell phone empty → copy from home phone (FIELD WRITE) | ✅ | `review_borrower_summary.py:187-207`; YAML `step_02_borrower_summary.yaml:462-464` |
| Driver's License expiry check (borrower) | 🟡 | `review_borrower_summary.py:384-401` — co-borrower DL expiry defined in YAML (`step_02_borrower_summary.yaml:437-440`) but not in Python |
| Current address vs subject property (cash-out refi) | ✅ | `review_borrower_summary.py` — gates on `loan_purpose` containing "cashout"; compares `FR0126` (borr current street) vs `URLA.X73` / field `11` (subject property). Flags `warning` if mismatch (confirm occupancy), `info` if match (consistent with primary residence refi). FR city/state/zip also read for full address display. |
| Govt ID bucket blank as info non-blocker | ✅ | `run_pre_checks.py` — Driver's License moved out of `warning_docs`; now raises `info` "Govt ID Not Yet Uploaded" when eFolder bucket is empty. Non-blocking. |

### Step 4 — 1003 URLA Page 2

#### 4.1 Employment (`review_urla_employment` 🔒 true)

| Item | Status | Evidence |
|---|---|---|
| Married + same-employer → copy date hired / years in job / years in line of work from borr → co-borr | ✅ | `review_urla_employment.py` — gates on `borrower_marital_status == "MARRIED"` + co-borrower present. Finds borrower and co-borrower current slots via `BE0X08` (voe_is_for) + `BE0X09` (employment_type). If employer names match (normalized), copies `date_hired`, `years_in_job`, `months_in_job`, `years_in_line_of_work`, `months_in_line_of_work` to co-borrower's BE slot via `_write_fields`. Flags `info-overwrite` on success, `warning` if borrower tenure fields are blank. |
| "Does not apply" checkbox detection for empty 1b/1c/1d | ✅ | `review_urla_employment.py` — `info` flag for each section (1b/1c/1d, borrower + co-borrower) when key field empty AND DNA checkbox unchecked. 1b: `FE0119`/`FE0219` vs `URLA.X199`/`X200`. 1c: `FE0302`/`FE0402` vs `URLA.X201`/`X202`. 1d: `FE0502`/`FE0602` vs `URLA.X203`/`X204`. All FE03-FE06 fields + DNA fields added to `data_gathering.py` and `step_04_urla_page2.yaml`. URLA.X201/X202 relabeled from "Employment" → Section 1c. |
| "Does not apply" for 1c | ✅ | Handled in `review_urla_employment.py` (substep 4.1) alongside 1b and 1d — see row above. `URLA.X201`/`X202` now correctly labeled as Section 1c DNA; `FE0302`/`FE0402` (employer name) used as presence gate. |
| Gross income surfacing both borrowers | ✅ | `review_urla_employment.py` — when 1c or 1d employer name is populated, flags `info` showing total gross (FE0112/FE0212/FE0312/FE0412) and monthly income (FE0156/FE0256/FE0356/FE0456) for borrower and co-borrower. All 8 fields added to `data_gathering.py` and `step_04_urla_page2.yaml`. |
| FHA gap rules | ✅ | `review_urla_employment.py:407-420`; YAML `step_04_urla_page2.yaml:463-482`; `workflow_config.json:210-219` |

### Step 5 — 1003 URLA Part 3

#### 5.1 Assets (`review_urla_assets` 🔒 true)

| Item | Status | Evidence |
|---|---|---|
| Investment property → 6-month reserves calc | ❌ | No REO reserve linkage in `review_urla_assets.py:234-389` |
| Retirement statement / bank statement cross-check for reserves | 🟡 | Bank stmt + Assets bucket vs VOD (`review_urla_assets.py:317-372`); Retirement Statement in registry (`required_docs.json:358`) but not explicitly checked |
| Feed reserves info to Cover Letter | ❌ | No Step 7 linkage |
| FHA 1-month bank stmts | ✅ | YAML `step_05_urla_part3.yaml:105-115`; code `review_urla_assets.py:236-237,391-393` |

#### 5.3 Liabilities (`review_urla_liabilities` 🔒 true)

| Item | Status | Evidence |
|---|---|---|
| Surface BOTH columns (Excluded Mo Pay + To Be Paid Off) — warning per row | ✅ | Excluded: `review_urla_liabilities.py:95-116`; payoffs: `:118-139`; YAML `step_05_urla_part3.yaml:182-200` |

#### 5.4 REO (`review_urla_reo` 🔒 true)

| Item | Status | Evidence |
|---|---|---|
| List REO properties | ✅ | `review_urla_reo.py:92-103` |
| Cross-check eFolder for mortgage statement / HOI deck / HOA / tax bill | ✅ | `review_urla_reo.py` — warns per missing doc type (Mortgage Statement, HOA Statement, Property Tax Bill) when REO properties exist |
| Stale mortgage statement → Xactus credit supplement request | ✅ | `review_urla_reo.py`: reads `_doc(state, "statement_date")` (extracted via new "Mortgage Statement" schema registered in eFolder API, bucket `Other Owned Property Documents`). Parses date with multi-format fallback. If >90 days old → `warning` "Mortgage Statement — Stale (>90 Days)" with suggestion to pull Xactus credit supplement. If ≤90 days → `info` "Mortgage Statement — Current". Schema fields also wired into `required_docs.json` (9 fields) and `required_docs_conditions.json` so `fetch_doc_fields` normalizes them into state. Xactus API call itself not yet in scope. |

### Step 6 — 1003 URLA Part 4

#### 6.2 Declarations (`review_urla_declarations` 🔒 true)

| Item | Status | Evidence |
|---|---|---|
| 5a "occupy as primary residence" → cross-validate 403/981/1069 | ✅ | Full cascade `review_urla_declarations.py:109-236`; YAML `step_06_urla_part4.yaml:63-108` |
| Verify Estate Held = Fee Simple | ✅ | `review_urla_declarations.py` — warns when field 1066 (Estate Will Be Held In, 1003 URLA Lender) is not FeeSimple, info when blank. Field 33 = Manner of Title (separate field). |

#### 6.3 Ethnicity / URLA Lender (`review_urla_ethnicity` 🔒 false)

| Item | Status | Evidence |
|---|---|---|
| Ethnicity cross-check vs DL | ✅ | `review_urla_ethnicity.py` (substep 6.3) — implemented with `_ethnicity_bucket()` normalizer (hispanic / not_hispanic / unknown). Gates on `dl_ethnicity_indicator` being populated (most US DLs don't print ethnicity — no flag if null). `warning` on mismatch, `info` on match or if LOS is blank. Also: attachment type blank → `info`, estate held (field 1066) → moved here from 6.2. `dl_ethnicity_indicator` is confirmed in live CatchingDoc API schema for Driver's License. |
| Manner Held suggestion logic: Tenancy by the Entirety (husband+wife) vs Tenancy in Common (siblings) | ✅ | `update_borrower_vesting.py` `_determine_manner_held()` — computes correct manner from marital status, co-borrower, property state (MD→Tenancy By The Entirety, NV→As Joint Tenants force-override, community property states, solo unmarried, etc.) and flags incompatible LOS values. Both field 33 and `URLA.X138` now written together with correct enum mapping via `_manner_to_urla_x138()`. Field ID corrected (was wrongly 34). |
| Estate held = Fee Simple verification | ✅ | `review_urla_ethnicity.py:127-143` (substep 6.3) — reads `estate_held` (field 1066, 1003 URLA Lender). Blank → `info` "Estate Held Not Set". Non-FeeSimple → `warning` "Estate Not Held in Fee Simple". Moved here from 6.2 (Declarations) since field 1066 is a URLA Lender field, not URLA Part 4 Declarations. |

### Step 7 — Cover Letter (`draft_cover_letter` 🔒 false)

| Item | Status | Evidence |
|---|---|---|
| Auto-copy Almas' email → `CX.KM.SUBMISSION.NOTES` | ✅ | `draft_cover_letter.py` — copies `state["almas_notes"]` to field; flags warning if not provided |
| Smart removal: Client Name, Property Address, Closing Date, Borrower(s), Employment & Income, Need Business Return, Dependents, Asset, Team Contacts, Appraisal | ✅ | `draft_cover_letter.py` — `_strip_boilerplate()` runs on Almas' notes before writing. Drops any line whose content starts with: `Client Name`, `Property Address`, `Closing Date`, `Borrower(s) on Loan:`, `Borrower(s) on Title:`, `Employment & Income`, `VOE Contact Email:`, `Need Business Return`, `Dependents`, `Assets` / `Asset `, `Team Contacts`, `Appraisal`. Matching is case-insensitive prefix. Consecutive blank lines collapsed. |
| Inclusion of AUS Findings + Income Breakdown | ❌ | Absent from tool/YAML/plans |
| "Documents still needed:" auto-population (Appraisal unless waived, HOI, title, dynamic missing) | ✅ | `draft_cover_letter.py` (substep 7.1) — before writing `CX.KM.SUBMISSION.NOTES`, checks eFolder presence and appends `"\n\nDocuments still needed:\n- ..."` for any missing: **Appraisal** (skipped if `CX.APPRAISAL.WAIVER` = Y or Appraisal Report/Acknowledgement/Invoice in eFolder), **HOI** (`Evidence of Insurance`), **Title Report** (both always required), **Assets / Bank Statement** (only if REO properties in state, for reserves). Missing items listed in `info-overwrite` flag details. |
| Cash-out refi awareness (CTC check; don't include Assets) | ❌ | CTC prefetched (`data_gathering.py:1083`) but unused by cover letter |
| HOA-letter request append when no HOA on subject | ❌ | No HOA linkage |
| Mortgage statement importance flag for refis | ❌ | No refi-specific flag |
| Investment property → assets needed for reserves note | ❌ | No bridge |

### Step 8 — Borrower Vesting (`update_borrower_vesting` 🔒 true)

| Item | Status | Evidence |
|---|---|---|
| Intent = "Will Occupy" when primary | ✅ | `_compute_occupancy_intent`; `update_borrower_vesting.py:157-164,241-273` |
| Vesting order: wife first if URLA-first | ❌ | Fixed order: 1868 borrower, 1873 co-borrower (`update_borrower_vesting.py:275-351`) |
| "Manner: Tenancy by the Entirety" for husband+wife | 🟡 | Returns Tenancy by Entirety only for MD (`update_borrower_vesting.py:42,94-95`); other states get "Husband And Wife" / "Wife And Husband" (`:96`) |
| "Build Final Vesting" click | 🟡 | Doesn't auto-click; flags when 1867 empty (`:416-424`); sets 1872/1877 to support build |

### Step 9 — Transmittal Summary (`update_transmittal_summary` 🔒 true)

| Item | Status | Evidence |
|---|---|---|
| Property type 1553 = "1 unit" | ❌ | Field 1553 mapped as **Project Type** in repo, read-only (`update_transmittal_summary.py:79,123-136`) — notes/code field-ID mismatch |
| Project type 1012 = "Other: G/Not in a Project or Development" | ❌ | Field 1012 absent from registry |
| Property review 1541 = exterior/interior | ❌ | Field 1541 absent |
| Form number 1542 = 1004 | ❌ | Field 1542 absent |
| `TSUM.PropertyFormType` = "Uniform Residential Appraisal Report" | ❌ | Absent |
| Note rate vs qualifying rate + condo CPM CUA pending flag | ✅ | `update_transmittal_summary.py:86-164` |

### New steps needed

| Suggested Step | Behavior | Status |
|---|---|---|
| FHA Management (9.x / new) | Property type 2996 = "1 unit" via Zillow lookup | ❌ — field 2996 absent, no step/tool |
| HUD/FHA Loan Transmittal (new) | Construction = "Existing" when no purchase price | ❌ — HUD-92900-LT in doc registry only (`document_type_registry.py:445-449`) |

### Cross-cutting items

| Item | Status | Evidence |
|---|---|---|
| Qualia title docs (Randazzo): download, bucket route, UW condition fulfill | ❌ | No Qualia integration; Encompass conditions client exists but no title-prelim workflow |
| Tasks-list verification (flood, FraudGuard, LDP/GSA, SSN, tax summary) | ✅ | FraudGuard (Fraud bucket) + Tax Summary added to `run_pre_checks.py` warning-docs block |
| File Contacts refi rule (escrow only) | ✅ | `review_file_contacts.py` — purchase checks all 4 contacts; refi checks Escrow only |

---

## Field-ID reconciliation needed

Notes vs `fields_config.json` conflicts (probe loan `2604964148` to confirm):

| Field | Notes meaning | Repo mapping |
|---|---|---|
| 1553 | Property type | Project Type |
| 1012 | Project type | not mapped |
| 2996 | FHA property type ("1 unit") | not mapped |
| 1541 | Property review level | not mapped |
| 1542 | Form number | not mapped |
| URLA.X138 | Manner held | not mapped; field 33/34 used inconsistently |
| TSUM.PropertyFormType | Property form type | not mapped |

---

## Backfill priority (review repo)

### P0 — stubs that must be written
1. **`draft_cover_letter.py`** — Full cover letter generation (smart strip, AUS/income inclusion, "Documents still needed", refi awareness) — ~~Almas notes copy~~ ✅ done
2. **`review_urla_ethnicity.py`** — DL ethnicity, ~~Fee Simple~~ ✅ done (moved to 6.2), Manner Held suggestion

### P1 — partial coverage to complete
3. Borrower Summary: co-borrower DL expiry; current address vs subject header for refi
4. Employment: 1c DNA writes; gross income surfacing; married-same-employer copy
5. ~~REO: eFolder doc presence checks~~ ✅ done
6. Vesting: wife-first ordering; Tenancy by the Entirety broadly for husband+wife

### P2 — new field writes (after ID reconciliation)
7. Transmittal Summary: property type / project type / review level / form number / TSUM.PropertyFormType
8. FHA Management step (Zillow lookup or equivalent)
9. HUD/FHA Loan Transmittal step (construction = Existing)

### P3 — new substeps
10. ~~Tasks-list verification: extend pre-checks for FraudGuard / LDP / GSA / SSN / Tax Summary~~ ✅ done (FraudGuard + Tax Summary)
11. ~~File Contacts refi rule (escrow-only)~~ ✅ done
12. Reserves logic for investment properties (link 5.1 → Cover Letter)
