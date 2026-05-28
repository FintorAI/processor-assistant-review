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
| Current address vs subject property (cash-out refi) | ❌ | Property checked vs purchase contract / USPS (`review_borrower_summary.py:362-382`); no borrower current address (field 35 / FR0126) vs subject header comparison |
| Govt ID bucket blank as info non-blocker | ❌ | Missing DL is warning in pre-checks (`run_pre_checks.py:146-150`); no "blank-is-fine" info flag in Borrower Summary |

### Step 4 — 1003 URLA Page 2

#### 4.1 Employment (`review_urla_employment` 🔒 true)

| Item | Status | Evidence |
|---|---|---|
| Married + same-employer → copy date hired / years in job / years in line of work from borr → co-borr | ❌ | No AutoLeasing or married-same-employer field-copy logic |
| "Does not apply" checkbox detection for empty 1b | 🟡 | Flag when FE0119/FE0219 empty + URLA.X201/X202 unchecked (`review_urla_employment.py:355-384`); does **not** auto-write DNA boxes |
| "Does not apply" for 1c | ❌ | Step 4 has only 4.1 + 4.2; no 1c substep/tool exists |
| Gross income surfacing both borrowers | ❌ | Only base monthly (`FE0119`/`FE0219`) checked; `paystub_gross_pay` referenced in YAML (`step_04_urla_page2.yaml:361-362`) but unused |
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
| Cross-check eFolder for mortgage statement / HOI deck / HOA / tax bill | ❌ | YAML defines doc_types (`step_05_urla_part3.yaml:217-231`) but tool ignores them — only suggests "verify in eFolder" |
| Stale mortgage statement → Xactus credit supplement request | ❌ | No Xactus logic anywhere |

### Step 6 — 1003 URLA Part 4

#### 6.2 Declarations (`review_urla_declarations` 🔒 true)

| Item | Status | Evidence |
|---|---|---|
| 5a "occupy as primary residence" → cross-validate 403/981/1069 | ✅ | Full cascade `review_urla_declarations.py:109-236`; YAML `step_06_urla_part4.yaml:63-108` |
| Verify Estate Held = Fee Simple | ❌ | Reads `estate_held` but no check (`review_urla_declarations.py:91`) |

#### 6.3 Ethnicity / URLA Lender (`review_urla_ethnicity` 🔒 false)

| Item | Status | Evidence |
|---|---|---|
| Ethnicity cross-check vs DL | ❌ | Stub: reads then `pass` (`review_urla_ethnicity.py:45-70`) |
| URLA.X138 Manner Held: Tenancy by Entirety (husband+wife) vs Tenancy in Common (siblings) | ❌ | URLA.X138 referenced only in notes; repo uses field 34 (`fields_config.json:554-560`) and field 33 inconsistently — no suggestion logic |
| Estate held = Fee Simple verification | ❌ | Commented TODO (`review_urla_ethnicity.py:87-100`) |

### Step 7 — Cover Letter (`draft_cover_letter` 🔒 false)

| Item | Status | Evidence |
|---|---|---|
| Auto-copy Almas' email → `CX.KM.SUBMISSION.NOTES` | ❌ | Stub returns `pass`; no `state["almas_notes"]` read (`draft_cover_letter.py:45-49,87-96`) |
| Smart removal: Client Name, Property Address, Closing Date, Borrower(s), Employment & Income, Need Business Return, Dependents, Asset, Team Contacts, Appraisal | ❌ | Only 3 sub-fields defined (`CX.KM.CL.TITLE.COMPANY`, `.APPRAISAL`, `.ADDITIONAL.NOTES`) — not the full list |
| Inclusion of AUS Findings + Income Breakdown | ❌ | Absent from tool/YAML/plans |
| "Documents still needed:" auto-population (Appraisal unless waived, HOI, title, dynamic missing) | ❌ | No doc-list logic |
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
| Tasks-list verification (flood, FraudGuard, LDP/GSA, SSN, tax summary) | 🟡 | eFolder presence only for Flood + LDP (`run_pre_checks.py:96-100,134-137`); FraudGuard / GSA / SSN / Tax Summary absent from pre-checks |
| File Contacts refi rule (escrow only) | ❌ | Always requires buyer/seller agents (`review_file_contacts.py:33-38`) |

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
1. **`draft_cover_letter.py`** — Full cover letter generation (Almas notes copy, smart strip, AUS/income inclusion, "Documents still needed", refi awareness)
2. **`review_urla_ethnicity.py`** — DL ethnicity, Fee Simple, Manner Held suggestion

### P1 — partial coverage to complete
3. Borrower Summary: co-borrower DL expiry; current address vs subject header for refi
4. Employment: 1c DNA writes; gross income surfacing; married-same-employer copy
5. REO: eFolder doc presence checks (not just info list)
6. Vesting: wife-first ordering; Tenancy by the Entirety broadly for husband+wife

### P2 — new field writes (after ID reconciliation)
7. Transmittal Summary: property type / project type / review level / form number / TSUM.PropertyFormType
8. FHA Management step (Zillow lookup or equivalent)
9. HUD/FHA Loan Transmittal step (construction = Existing)

### P3 — new substeps
10. Tasks-list verification: extend pre-checks for FraudGuard / LDP / GSA / SSN / Tax Summary
11. File Contacts refi rule (escrow-only)
12. Reserves logic for investment properties (link 5.1 → Cover Letter)
