# Video 5 Verification — FHA Sample Loan (redacted)

End-to-end `review` runs against local `langgraph dev` on **Encompass Prod**, used to
verify every fix documented in `docs/video5.md`. Findings below are taken from the
final thread's agent state.

> **Note:** All borrower, property, and government-identifier values in this artifact
> have been replaced with synthetic placeholders (`[redacted]`). Only non-identifying
> structural detail (field IDs, flag titles, pass/fail status) is retained.

| | |
|---|---|
| Loan | `[loan # redacted]` — Borrower / Co-Borrower `[redacted]` |
| Property | `[subject property — redacted]` |
| Program | **FHA** (Purchase, both borrowers on loan + title) |
| Env | Prod |
| Run 1 thread | `[thread id redacted]` (initial verification) |
| Run 2 thread | `[thread id redacted]` (**last run** — after FHA + PUD code fixes) |
| Outcome | All 12 steps completed (`current_step = COMPLETED`), ~79 flags |

The "last run" (Run 2) is the source of truth below. Run 1 is referenced only where
it surfaced a bug that Run 2 then confirmed fixed.

---

## Summary

| video5 issue | Status | Evidence (Run 2) |
|---|---|---|
| Cover letter — no OCR / boilerplate | ✅ PASS | `CX.KM.SUBMISSION.NOTES` clean; sections dropped; AUS + Program salvaged |
| File contacts — address split | ✅ PASS | `address` / `city` / `state` / `postalCode` split correctly |
| File contacts — flags list written fields | ✅ PASS | bullet-list per contact flag |
| File contacts — Buyer/Seller agent + Escrow | ✅ PASS | all three updated from settlement / purchase agreement |
| File contacts — smart address comparison | ✅ PASS | only genuine diffs flagged |
| File contacts — Seller 1/2 = subject property | ✅ PASS | both set to subject property |
| Borrower summary — DL Gov ID + type write | ✅ PASS | field 5053 + 5055 written |
| URLA P1 — unit normalization | ✅ PASS (N/A) | no unit in address → no spurious writes |
| URLA P1 — work-phone backfill | ✅ PASS (N/A) | P1 work phones already present → no backfill needed |
| URLA P2 — income validation (borrower) | ✅ PASS | base-pay match flag |
| URLA P2 — co-borrower validation + match flag | ⚠️ PARTIAL (data) | co-borrower cross-check ran; base-pay match couldn't confirm (VOE base pay not extracted) |
| URLA Part 3 — retirement FHA 60% | ✅ PASS (N/A) | assets = bank statements; no retirement statement to evaluate |
| Transmittal — PUD detection | ✅ PASS | Project Type PUD surfaced; appraisal doc = PUD |
| Transmittal — Zillow Project Name write | ⚠️ FIXED post-run | code path reached + Zillow subdivision returned; write was rejected by wrong field ID — now corrected to 1298/3050 |
| FHA-Specific Forms (STEP_11) runs | ✅ PASS (after fix) | 11.1 + 11.2 executed as FHA |
| FHA — CAIVRS write | ✅ PASS | both numbers confirmed present (write-if-blank no-op) |
| FHA — Case Number write | ✅ PASS | field 1040 present (write-if-blank no-op) |
| HUD Transmittal flag | ✅ PASS | "HUD-92900-LT Review Required" raised |
| build_action_items (12.3) | ❌ MISSING at run time | tool was orphaned (not registered) — now restored as substep 12.3 |

---

## 1. Cover Letter (`draft_cover_letter`, 8.1)

**PASS.** `CX.KM.SUBMISSION.NOTES` contains **no** verbatim OCR / purchase-agreement
image text. The File Summary, Team Contacts, Appraisal, and Additional Notes sections
were dropped; `AUS Findings` and `Loan Program` were salvaged.
Appended "Documents still needed:" (Appraisal, HOI, Title Report).
- Flags: `Cover Letter — Submission Notes Written` (sections removed).

## 2. File Contacts (`review_file_contacts`, 1.2)

**PASS.** Contacts updated from the settlement statement / purchase agreement with
flags enumerating each written field as a bullet list:
- **Escrow Company** → `[escrow agent — redacted]`; address split to street / city / ZIP.
- **Buyer's Agent** → address split; only phone format + street diff flagged (smart
  comparison suppressed formatting-only noise).
- **Seller's Agent** → address split; company license diff surfaced for verification.
- **Seller 1 / Seller 2** → address set to the subject property.

Address splitting, smart comparison, bullet-list flags, and seller-address sync all
confirmed working.

## 3. Borrower Summary — Origination (`review_borrower_summary`, 2.1)

**PASS.** Driver's License extraction + write works — no "ID Expiry Unknown" flag:
- `Auto-corrected: Borrower Government ID` → field **5053** = `[gov id redacted]`
- `Auto-corrected: Borrower Government ID Type` → field **5055** = `DL`

No co-borrower Gov ID written (co-borrower DL not extractable) — acceptable.

## 4. URLA Page 1 (`review_urla_page1`, 4.1)

**PASS (N/A for this loan).**
- Unit normalization: subject property has no unit → no spurious unit-field writes.
- P1 work-phone backfill (4533←FE0117 / 4534←FE0217): no backfill flag → P1 work
  phones already populated (logic only fires on blank). Both borrowers ≥ 2Y at current
  address, dependents present.

## 5. URLA Page 2 — Employment (`review_urla_employment`, 5.1)

- **Borrower:** ✅ `Monthly Base Pay Match — Current (Borrower)` — LOS (1003) base pay
  == VOE base pay. Employer/position mismatches flagged separately.
- **Co-borrower:** ⚠️ cross-check **ran** (produced `Position Title Mismatch — Current
  (Co-Borrower)` and `Section 2d Income — Co-Borrower`), confirming the "both borrowers"
  fix is active. The base-pay **match** flag did not fire because the co-borrower's VOE
  `current_monthly_base_pay` / `current_employer_name` were **not extracted** (only
  `previous_employer_name` came through) — nothing to compare. **Data limitation, not a
  code gap.**

## 6. URLA Part 3 — Assets (`review_urla_assets`, 6.1)

**PASS (N/A).** Source of assets = bank statements (no Retirement Account Statement on
file), so the FHA 60% haircut rule had nothing to evaluate and correctly no-op'd. Bank
statements matched 2a/VOD; large-deposit sourcing flags raised as expected.

## 7. Transmittal Summary — PUD (`update_transmittal_summary`, 10.1)

**PASS (detection) + FIXED (project name).**
- Project Type (field 1553) = `PUD`; appraisal doc Project Type = `PUD`; property type
  (1041) already `PUD`; HOA dues (233) present.
- **Bug found in Run 1:** the Zillow subdivision → Project Name write lived only in the
  `if not _is_condo(property_type)` branch, so for an already-`PUD` property the code
  fell straight through to the "CUA Required" flag and never called Zillow. **Fixed** —
  the lookup (`_zillow_lookup`) now runs in the condo/PUD branch too.
- **Run 2 confirmed** the path is reachable: Zillow returned a subdivision and the write
  was attempted, BUT Encompass rejected it:
  `Project Name (id CX.CONDO.PROJECT.NAME): custom field not defined in this Encompass
  instance` → the field ID was wrong.
- **Now resolved (post-run):** verified field IDs wired in — Project Name = **1298**,
  CPM Project ID# = **3050** (FIELD_MAP + step_10 YAML + tool). The next run will save
  the subdivision to field 1298; CPM Project ID# (3050) still needs the Freddie Mac CPA
  browser lookup (CUA).

## 8. FHA-Specific Forms — STEP_11 (FHA-gated)

**PASS (after fix).**
- **Bug found in Run 1:** both 11.1 and 11.2 **skipped** with "loan_type != FHA —
  Conventional." Root cause: `build_loan_summary` set
  `loan_profile.loan_type = preflight_mortgage_type or "Conventional"`, and the preflight
  field was blank → defaulted to Conventional, even though the authoritative LOS Mortgage
  Type (field 1172) = `FHA`. `_is_fha` checked the profile first via `or` and
  short-circuited on the wrong value.
- **Fix:** (a) `build_loan_summary` now falls back to the LOS `loan_type` / `loan_purpose`
  before defaulting; (b) `_is_fha` in both FHA tools treats the loan as FHA if **either**
  the LOS field or the profile says FHA.
- **Run 2 confirmed:**
  - **11.1 FHA Management** — executed. FHA Case Number present (field 1040 =
    `[fha case # redacted]`); 2 CAIVRS numbers found (borrower + co-borrower,
    `[caivrs redacted]`), 0 written (already populated → correct write-if-blank no-op);
    `caivrs_fields_verified = TRUE`.
  - **11.2 HUD Transmittal** — executed; raised `HUD-92900-LT Review Required` (info);
    case number present.

All extracted FHA data (case # / ADP / CAIVRS) was correct from the start — only the
gate was wrong.

## 9. build_action_items (12.3) — comms action items

**MISSING at run time → RESTORED.** The runs produced `comms_actions: 0` because the
`build_action_items` tool was orphaned: it had been hand-registered into the generated
files but never added to a YAML substep, so a prior `factory-reset` dropped it from
`__init__.py` / `workflow_config.json` / `registry.py`. Restored as **substep 12.3** in
`definitions/step_12_processor_workflow.yaml`; `factory-reset` → Validation PASSED; the
agent can now call it (order title report, lock-desk address fix, EMD request, no-HOA
LOE). Will populate `comms_actions` on the next run.

---

## Bugs found and fixed during verification

1. **FHA step skipped as "Conventional"** — `loan_profile.loan_type` defaulted to
   Conventional, masking LOS field 1172 = FHA. Fixed in `build_loan_summary` +
   `_is_fha` (both FHA tools). Confirmed in Run 2.
2. **PUD Project Name write unreachable for already-PUD loans** — gated under
   `not _is_condo`. Factored `_zillow_lookup` and run it in the condo/PUD branch.
   Confirmed reachable in Run 2.
3. **Project Name field ID undefined** (`CX.CONDO.PROJECT.NAME`/`.ID` rejected by
   Encompass) — replaced with verified IDs **1298** (Project Name) / **3050** (CPM
   Project ID#). Write guarded so a rejected write no longer masks itself.
4. **`build_action_items` orphaned** — restored as substep 12.3.

## Open / not-yet-verified

- **Project Name write to field 1298** — wired in but not yet exercised end-to-end (no
  re-run after the field-ID fix). Expected to save the Zillow subdivision on the next run.
- **Co-borrower base-pay match** — depends on the co-borrower VOE extraction returning a
  `current_monthly_base_pay`; the cross-check itself is in place.
- **`comms_actions`** — `build_action_items` (12.3) restored but not yet exercised in a run.
- Pre-existing partial-write: `CX.NONDEL.INV.APPROVAL` (12.1) — custom field not defined
  in this instance (unrelated to video5).
