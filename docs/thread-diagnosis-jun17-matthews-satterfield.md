# Thread Diagnosis — Matthews & Satterfield
**Date:** 2026-06-17  
**Cloud:** `processor-assistant-review` (Prod)  
**Scope:** Most recent completed run per loan; all 9 unique loans listed, deep-dive on Matthews and Satterfield.

---

## 1. All Active Loans — Overview

| Loan | Borrower | Env | Flags (total) | Unresolved | Last Run |
|---|---|---|---|---|---|
| 2605968646 | Cassandra Matthews | Prod | 66 | 44 | 2026-06-16 |
| 2601955374 | Derrick Satterfield | Prod | 56 | 40 | 2026-06-16 |
| 2605926537 | Amanda Scarboro | Test | 57 | 40 | 2026-06-16 |
| 2604964148 | (redacted) | Prod | 1 | 0 | 2026-06-08 |
| 2605968482 | Rene Carranza Salgado | Prod | 81 | 47 | 2026-06-03 |
| 2605967515 | Amy Matousek | Prod | 49 | 43 | 2026-06-03 |
| 2605968610 | Kyle Weihs | Prod | 81 | 46 | 2026-06-03 |
| 2605968608 | Jonathan Stumpf | Prod | 57 | 32 | 2026-06-03 |
| 2507943889 | Task Test 1 Processor | Prod | 59 | 50 | 2026-06-02 |

---

## 2. Cassandra Matthews — Loan 2605968646

### Loan Summary
| Field | Value |
|---|---|
| Purpose | Purchase |
| Loan Amount | $224,999.00 |
| Loan Type | FHA |
| Property | 5548 Daffodil Dr, Conway SC 29527 |
| Property Type | **ManufacturedHousing** |
| Borrower | Cassandra Matthews |
| Co-Borrower | James Martin |
| Marital Status | Unmarried |
| Rate Locked | No |
| Address Validation | ✅ USPS normalized matches LOS |

### Flag Summary
| Severity | Count |
|---|---|
| `critical` | 1 |
| `warning` | 14 |
| `info` | 36 |
| `info-overwrite` | 15 |
| **Unresolved** | **44** |

### Critical Flags
| Substep | Title | Details |
|---|---|---|
| 1.1 | **AUS Missing** | No Underwriting (DU/LP) document found in eFolder. Workflow cannot proceed to submission without AUS results. |

### Warning Flags (processor action required)
| Substep | Title | Notes |
|---|---|---|
| 1.1 | Missing Required Document — Assets | Assets document not found in eFolder |
| 2.1 | Co-Borrower Email Missing | Co-borrower (James Martin) has no email address in LOS |
| 2.1 | Undiscounted Rate Empty | Field 3293 blank |
| 2.1 | Last Rate Set Date Needs Update | Field 3253 = 06/11/2026, expected 06/16/2026 |
| 2.1 | Secondary Registration Empty | Field 3941 blank |
| 6.1 | Large/Green Deposit Requires Sourcing | Multiple deposits flagged: 05/07 $1,004, 05/21 $926, 05/28 $785 |
| 6.3 | Payoff Statement Required: WESTLAKE | Acct …3185, balance $14,033, marked To Be Paid Off. Column 2 payoff required. |
| 6.4 | REO Doc Missing — Mortgage Statement | ⚠️ **See Logic Error #1 below** |
| 6.4 | REO Doc Missing — HOA Statement | Borrower owns REO at 104 Persivant Dr |
| 6.4 | REO Doc Missing — Property Tax Bill | Borrower owns REO at 104 Persivant Dr |
| 6.4 | Gift Letter Missing | Gift funds of $150.00 — ⚠️ **See Logic Error #3 below** |
| 9.1 | Final Vesting Empty | Field 1867 — must click Build Final Vesting in Encompass |
| 10.1 | Project Type (1012) Unexpected Value | `G_NotInAProjectOrDevelopment` for ManufacturedHousing — see Logic Error #4 |
| 11.1 | Field Write Partially Failed | Non-Del Inv field rejected by Encompass — see Logic Error #5 |

### Comms Actions Triggered
| Action | Graph | Status | Notes |
|---|---|---|---|
| `order_title_report` | `processor_title_order` | actionable | No Title Report in eFolder |
| `emd_request` | `processor_emd_request` | actionable | EMD $2,000 to Grand Strand Law Group — check copy confirmation needed |
| `hoa_loe_signature` | `processor_blend_loe` | actionable | ⚠️ **See Logic Error #2 below** |

---

## 3. Derrick Satterfield — Loan 2601955374

### Loan Summary
| Field | Value |
|---|---|
| Purpose | Purchase |
| Loan Amount | $588,650.00 |
| Loan Type | FHA |
| Property | 3216 Holly Knoll Ct, Abingdon MD 21009 |
| Property Type | Detached |
| Borrower | Derrick Satterfield |
| Co-Borrower | None |
| Marital Status | Married (solo borrower) |
| Rate Locked | No |
| Address Validation | ✅ USPS normalized matches LOS |

### Flag Summary
| Severity | Count |
|---|---|
| `critical` | 1 |
| `warning` | 17 |
| `info` | 27 |
| `info-overwrite` | 11 |
| **Unresolved** | **40** |

### Critical Flags
| Substep | Title | Details |
|---|---|---|
| 1.1 | **1003 URLA Not Found in eFolder** | Required document missing from eFolder bucket `1003`. Cannot verify URLA data without it. |

### Warning Flags (processor action required)
| Substep | Title | Notes |
|---|---|---|
| 1.1 | ID Expiry Unknown | Driver's License present but expiry date could not be extracted |
| 1.1 | MD State Disclosures Missing | No Maryland eDisclosure documents in eFolder (property is in MD) |
| 2.1 | Credit Score Missing | TransUnion/Empirica (field 1450) blank |
| 2.1 | **Purchase Price Mismatch** | LOS field 136 = $610,000 vs Purchase Contract = $450,000. **Δ $160,000.** High severity — LOS purchase price is incorrect. |
| 2.1 | **Estimated Value vs Purchase Price Mismatch** | Field 1821 = $610,000 vs PA = $450,000. Same root cause as above. |
| 2.1 | **Down Payment Amount Mismatch** | 3.5% × $450,000 = $15,750 but LOS shows $21,350. **Δ $5,600.** Likely cascades from wrong purchase price. |
| 2.1 | Undiscounted Rate Empty | Field 3293 blank |
| 2.1 | Last Rate Set Date Needs Update | Field 3253 = 06/15/2026, expected 06/16/2026 |
| 2.1 | Secondary Registration Empty | Field 3941 blank |
| 3.1 | **Manner Held Mismatch** | Field 33 = `Single man`, computed = `Sole Ownership` — ⚠️ **See Logic Error #6 below** |
| 5.1 | Monthly Base Pay Mismatch | LOS $8,589/mo vs VOE $7,586.77/mo. Δ $1,002/mo. Needs reconciliation. |
| 5.1 | **FHA Employment Gap — Explanation Required** | 3-month gap: Oct 2023 → Feb 2024. FHA requires written explanation for any gap > 30 days in past 2 years. |
| 6.4 | Gift Letter Missing | Gift of $347.13 — ⚠️ **See Logic Error #3 below** |
| 7.1 | **Gift Letter Missing** | GiftOfCash $40,000 (Borrower) in eFolder — no Gift Letter found. **This is real and high priority.** |
| 9.1 | Final Vesting Empty | Field 1867 — must click Build Final Vesting |
| 10.1 | Project Type (1012) Unexpected Value | Same as Matthews — see Logic Error #4 |
| 11.1 | Field Write Partially Failed | Non-Del Inv field rejected — see Logic Error #5 |

### Comms Actions Triggered
| Action | Graph | Status | Notes |
|---|---|---|---|
| `order_title_report` | `processor_title_order` | actionable | No Title Report in eFolder |
| `emd_request` | `processor_emd_request` | actionable | EMD $12,000 to Cummings & Co Realtors |
| `hoa_loe_signature` | `processor_blend_loe` | actionable | ⚠️ **See Logic Error #2 below** |

---

## 4. Logic Errors & Diagnoses

### Logic Error #1 — REO Mortgage Statement: False "Current" Flag (Matthews)
**Affected:** Matthews 6.4  
**Symptoms:** Two contradictory flags emitted simultaneously:
- `warning` "REO Doc Missing — Mortgage Statement" (`resolved=False`) ← **CORRECT**
- `info` "Mortgage Statement — Current — dated 05/31/2026 (within 90-day window)" (`resolved=False`) ← **FALSE POSITIVE**

**eFolder probe result (verified):**  
`Mortgage Statement` bucket → `count=0` (document truly absent). The date `05/31/2026` found in `doc_fields["statement_date"]` is actually extracted from a **Bank Statement** document, not a Mortgage Statement. The bank statement's date pollutes the generic `statement_date` key, triggering the "Current" stale-check branch even though no Mortgage Statement exists.

**Root cause:** The stale/current mortgage statement check in `review_urla_reo.py` reads `_doc(state, "statement_date")` without first confirming the Mortgage Statement is present via `_efolder_present`. Any document whose extracted fields include a `statement_date` (e.g. a Bank Statement) can populate this key, causing the "Current" info flag to fire when no Mortgage Statement is uploaded at all.

**Fix (applied):** Gate `_doc(state, "statement_date")` on `_efolder_present(state, "Mortgage Statement")` first. If the bucket is empty, skip the date check entirely. The "REO Doc Missing" warning was already correct and continues to fire.

---

### Logic Error #2 — HOA LOE Comms Action for ManufacturedHousing and Detached (Both loans)
**Affected:** Matthews, Satterfield — `hoa_loe_signature` comms action  
**Symptoms:** Both loans trigger `hoa_loe_signature → processor_blend_loe` because they have no HOA Statement in the eFolder and are not condos. However:
- **Matthews** has ManufacturedHousing — manufactured homes in SC frequently have no HOA; sending a "no-HOA attestation" before confirming with the processor is premature.
- **Satterfield** has Detached — many detached single-family homes genuinely have no HOA; the same concern applies.

**Root cause:** `_rule_hoa_loe` in `build_action_items.py` only excludes condos (`_is_condo()`). It has no check for whether the property type is one that commonly lacks an HOA, nor any check for a "property has HOA" LOS field that might confirm HOA existence.

**Recommendation:** Before triggering the Blend LOE:
1. Check if any HOA-related LOS field (e.g. `CX.HOA.*`) is populated — if an HOA dues/name field is set, the property does have an HOA and the Statement is just missing, which is a different action.
2. Consider adding ManufacturedHousing to the excluded property types (alongside condos) or making this action require processor confirmation before firing.

---

### Logic Error #3 — Trivial Gift Amounts Triggering Gift Letter Flags (Both loans)
**Affected:** Matthews 6.4 ($150), Satterfield 6.4 ($347.13)  
**Symptoms:** `review_urla_reo.py` emits "Gift Letter Missing" for gift amounts of $150 and $347.13. These are almost certainly not real gift funds (they may be rounding entries, test data, or unrelated credits in field 231).

**Root cause:** The gift letter check in `review_urla_reo.py` (added in PR #8) reads `_los(state, "gift_amount")` (field 231) and fires for any amount > 0 with no minimum threshold.

**Compare:** The legitimate gift flags in 7.1 come from the `giftsGrants` API collection (real borrower gift funds: $40,000 for Satterfield). Those are the correct data source.

**Fix:** Apply a minimum threshold in `review_urla_reo.py` (e.g. `> 500`) before emitting the Gift Letter flag, or cross-reference against the `giftsGrants` API collection rather than reading flat field 231.

---

### Logic Error #4 — Project Type (1012) Flag for ManufacturedHousing / Detached (Both loans)
**Affected:** Matthews 10.1, Satterfield 10.1  
**Symptoms:** "Project Type (1012) — Unexpected Value" fires because field 1012 = `G_NotInAProjectOrDevelopment`. The flag says the expected value is `Other: G/Not in a Project or Development`.

**Root cause:** These are the same semantic value (`G_NotInAProjectOrDevelopment` is the Encompass enum for `Other: G/Not in a Project or Development`), but the comparison logic in `update_transmittal_summary.py` compared the raw enum string to a display string using a substring check, causing a false mismatch.

**Fix (applied):** Added a `_normalise_1012()` helper in `update_transmittal_summary.py` that strips spaces, underscores, slashes, colons, and hyphens before comparing. `G_NotInAProjectOrDevelopment` normalises to `gnotinaprojectordevelopment` which matches the display string `Other: G/Not in a Project or Development` after the same normalisation. The "Unexpected Value" warning will no longer fire for this enum.

---

### Logic Error #5 — Systemic `CX.NONDEL.INV.APPROVAL` Write Failure (Both loans)
**Affected:** Matthews 11.1, Satterfield 11.1 (likely all Prod loans)  
**Symptoms:** "Field Write Partially Failed — 1 field(s) skipped. Non-Del Inv Approval field rejected by Encompass."

**Root cause:** `update_processor_workflow.py` attempts to write `CX.NONDEL.INV.APPROVAL = "No"` unconditionally on all conforming loans. This field is likely read-only or conditionally locked in Encompass for these loan types/states.

**Impact:** Low for now (the write is skipped gracefully via `_write_fields` resilient retry), but the `warning` flag appears on every Prod run as noise. The field should either be written conditionally (only when writable) or removed from the workflow writes until the correct write path is confirmed.

---

### Logic Error #6 — Manner Held Mismatch False Positive: "Single Man" vs "Sole Ownership" (Satterfield)
**Affected:** Satterfield 3.1  
**Symptoms:** "Manner Held Mismatch" fires — LOS field 33 = `Single Man`, computed value = `Sole Ownership`.

**Encompass dropdown context (verified from screenshots):**  
The field 33 dropdown contains both `Sole Ownership` and `Single Man` / `Single Woman` / `Married Man` / `Married Woman` / `Unmarried Man` / `Unmarried Woman` as separate entries. These marital-status-qualified descriptions all represent title held solely by one person — they are the same ownership structure as `Sole Ownership`, with the borrower's marital status appended per state recording convention.

**Root cause:** `_manner_held_compatible()` in `update_urla_lender.py` used `comp_up in los_up` (i.e. `"SOLE OWNERSHIP" in "SINGLE MAN"`) which is `False`. The function correctly handled "Husband And Wife" variants via `_SPOUSE_VESTING_VARIANTS` but had no equivalent mapping for sole-ownership variants.

**Fix (applied):** Added `_SOLE_OWNERSHIP_VARIANTS` set to `update_urla_lender.py` containing `SINGLE MAN`, `SINGLE WOMAN`, `MARRIED MAN`, `MARRIED WOMAN`, `UNMARRIED MAN`, `UNMARRIED WOMAN`, and their `A/AN` prefix forms. `_manner_held_compatible()` now returns `True` when the computed value is `Sole Ownership` and the LOS value exactly matches any of these variants (case-insensitive).

---

### Observation — `env` Not Stored in `los_fields` (Both loans)
**Affected:** Both loans show `env = None` when reading from `los_fields`  
**Note:** `state.get("env")` resolves correctly (used by `_base_payload`), so comms payloads include the correct env. This is not a runtime bug but confirms that `env` is a top-level state key, not a `los_fields` entry, which is the intended design.

---

## 5. Priority Action Items by Loan

### Matthews — Processor Actions
1. **Run AUS** (DU or LP) and upload results — blocks submission
2. **Upload Assets document** to eFolder
3. **Request EMD check copy** from buyer's agent (comms action queued)
4. **Payoff WESTLAKE** ($14,033): obtain payoff statement and upload
5. **REO docs**: upload HOA Statement and Property Tax Bill for 104 Persivant Dr
6. **Large deposits**: source/explain the three flagged deposits
7. **Click Build Final Vesting** in Encompass after confirming Manner Held
8. **Co-borrower email**: add to LOS

### Satterfield — Processor Actions
1. **1003 URLA not in eFolder** — upload immediately (critical)
2. **Purchase price discrepancy** — LOS shows $610k, PA says $450k. Correct field 136 and field 1821 in Encompass. Down payment calculation will self-correct.
3. **Request Gift Letter for $40,000** gift funds (comms action queued or manual)
4. **FHA Employment Gap explanation** — obtain written LOE from borrower (Oct 2023 – Feb 2024 gap)
5. **Monthly base pay reconciliation** — $1,002/mo discrepancy between 1003 and VOE
6. **Maryland eDisclosures** — upload or confirm eSigned
7. **ID expiry** — manually verify Driver's License expiration
8. **Click Build Final Vesting** in Encompass

---

## 6. Code Fix Summary

| # | File | Status | Fix |
|---|---|---|---|
| 1 | `review_urla_reo.py` | ✅ Fixed | Gate `_doc(state, "statement_date")` on `_efolder_present("Mortgage Statement")` to prevent Bank Statement date contaminating the stale check |
| 2 | `build_action_items.py` | ⏸ Deferred | HOA LOE trigger for ManufacturedHousing/Detached — not confirmed whether these property types need HOA |
| 3 | `review_urla_reo.py` | ⏸ Deferred | Gift Letter flag for trivial amounts — not confirmed if threshold is needed |
| 4 | `update_transmittal_summary.py` | ✅ Fixed | Normalise `G_NotInAProjectOrDevelopment` enum vs display string before comparing field 1012 |
| 5 | `update_processor_workflow.py` | ⏳ Pending | `CX.NONDEL.INV.APPROVAL` write failure — owner to confirm correct write path |
| 6 | `update_urla_lender.py` | ✅ Fixed | Added `_SOLE_OWNERSHIP_VARIANTS` to `_manner_held_compatible()` — `Single Man`, `Single Woman`, etc. now accepted as compatible with `Sole Ownership` |
