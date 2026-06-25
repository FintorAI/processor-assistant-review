# Video 4 Gap Verification — Loan `2605968646` (Cassandra Matthews & James Ervin Martin)

Live run against the **local `langgraph dev`** server (graph `review`, env **Prod**) to confirm the
Gap A–F solutions actually fire on the real File-4 loan, with findings pulled from the resulting
thread state.

- **Property:** 5548 Daffodil Dr, Conway, SC 29527 · FHA Purchase (Manufactured Home) · 2 borrowers
- **Inputs:** `almas_notes` = File-4 summary; `almas_notes_images` = OCR of the Roles & Contacts panel
  (image (14).png): Lender (All Western/Eric Gut), Escrow (Grand Strand Law Group/Amy Tush),
  Buyer's Agent (Sloan Realty Group/Scott P Ritter), Seller's Agent (Home Placer LLC/Joe Scaturro).
- **Run:** thread `019eff49-a604-7572-8edb-7c90132ade33` · status **COMPLETED** (73 flags, 2 comms actions)
- **State dump:** `/tmp/review_state_2605968646.json`

## What to check (affirms each gap's solution on this loan)

| # | Gap | Check | Result |
|---|-----|-------|--------|
| A1 | A — Cover-letter image → File Contacts | Buyer's Agent parsed from image: Sloan Realty Group / Scott P Ritter / 843-222-9265 / scottritter@SRGmail.com | ✅ (parser) / ⚠️ pipeline — see note |
| A2 | A | Seller's Agent parsed: Home Placer LLC / Joe Scaturro / 843-798-8333 / joe@forturro.com | ✅ (parser) / ⚠️ pipeline |
| A3 | A | Escrow parsed: Grand Strand Law Group / Amy Tush; image email is truncated (`amy@grandstrandlawgroup.c`) → correctly **dropped** | ✅ |
| B1 | B — ESS (pre-CD) → File Contacts | ESS bucket is pre-CD (`Estimated Settlement Statement: NOT FOUND in DynamoDB`) → bypass ran; Escrow Company written from settlement statement | ✅ |
| B2 | B | ESS preferred over image: Escrow → `Grand Strand Law Group, LLC` / c/o `Bailey R. Gordon` / Myrtle Beach SC / `bgordon@grandstrandlawgroup.com` / (843) 492-5422 (full company, full address, valid email) | ✅ |
| C1 | C — Purchase Agreement (SC) → File Contacts | PA bypass enriched agents from the purchase agreement | ✅ |
| C2 | C | PA fills/overrides: Buyer's Agent contact → `Denise Zrakas` + full address; Seller's Agent → `Keller Williams The Forturro Group`, bizLicense `26733`, full address | ✅ |
| D1 | D — Vesting writes | Manner 33 = **Tenancy in Common** (verified matches computed); URLA.X138 auto-set `TenantsInCommon`; Final Vesting 1867 = `CASSANDRA MATTHEWS AND JAMES ERVIN MARTIN, TENANCY IN COMMON` | ✅ |
| D2 | D | Vesting desc 1872 = `AN UNMARRIED WOMAN`, 1877 = `AN UNMARRIED MAN`; vesting type 1871/1876 = `Individual` | ✅ |
| D3 | D | Occupancy intent auto-set: Borr + CoBorr `Will Occupy` (1811 = PrimaryResidence) | ✅ |
| E1 | E — Blend follow-up / No-HOA | `comms_actions` = `[order_title_report, emd_request]` — **no HOA action item** | ✅ |
| F1 | F — Bank stmt vs 2a/VOD | `read_vods` returned all 3 URLA-2020 (2a) rows — confirmed by `Total Assets … sum of VOD balances = $18,235.36` (= Fidelity 18,125.18 + TD 42.42 + Woodforest 67.76) | ✅ |
| F2 | F | TD Bank ($42.42) + Woodforest ($67.76) → **info** "Matches 2a/VOD" | ⚠️→✅ after fix (see note) |
| F3 | F | No spurious balance-mismatch; nothing missing → no auto-populate (`to_add=[]`) | ✅ after fix |

## Findings

### Gap A — Cover-letter image → File Contacts  ✅ (parser) / ⚠️ not exercised by harness
The full run logged `[0.6] Almas-Notes Images Transcribed … 0 succeeded, 1 failed`. Substep 0.6
(`extract_almas_images`) runs **its own Claude-vision OCR** and reads the image from
`item["url"]/signed_url/s3_url` — it *ignores* any pre-supplied `extracted_text`. The harness passed
text but no fetchable URL, so OCR returned `no_url` and the image path didn't contribute in-pipeline.
**This is a harness limitation, not a Gap A defect.** Verified the path directly instead: ran the real
`image (14).png` through the same `llm_vision_call`, then `_parse_image_contacts` — it cleanly produced
ESCROW_COMPANY / BUYERS_AGENT / SELLERS_AGENT with the right names/contacts/phones/emails, and the
genuinely truncated escrow email (`amy@grandstrandlawgroup.c`) is the one dropped by validation.
The same three contacts were authoritatively populated by Gaps B & C in the run regardless.
> To exercise Gap A end-to-end in the harness, pass `almas_notes_images=[{"url": "<fetchable image URL>"}]`.

### Gap B — Estimated Settlement Statement (pre-CD) → File Contacts  ✅
ESS was `NOT FOUND in DynamoDB` (pre-CD in the ESS bucket) — exactly the case the download→LandingAI
bypass exists for. The Escrow Company contact was written from the settlement statement:
`1.2 (info-overwrite) File Contact Updated from Settlement Statement / Purchase Agreement: Escrow Company` —
`name '(empty)' → 'Grand Strand Law Group, LLC'`, `contactName 'Amy Tush' → 'Bailey R. Gordon'`, plus full
address and the valid email `bgordon@grandstrandlawgroup.com`. ESS (richer) correctly won over the image.

### Gap C — Purchase Agreement (SC) → File Contacts  ✅
Buyer's & Seller's Agents were enriched from the purchase agreement:
`Buyer's Agent` contactName `Scott P Ritter → Denise Zrakas` + full Waccamaw Blvd address;
`Seller's Agent` `Home Placer LLC → Keller Williams The Forturro Group`, `bizLicenseNumber 27547 → 26733`,
full address. The doc sources (ESS/PA) are preferred over the image per design.

### Gap D — Vesting writes  ✅ (the cleanest pass)
Unmarried co-borrowers, SC → all vesting writes fired:
- Manner Held (33) verified `Tenancy in Common`; URLA.X138 auto-set `TenantsInCommon`.
- Final Vesting (1867) = `CASSANDRA MATTHEWS AND JAMES ERVIN MARTIN, TENANCY IN COMMON`.
- Per-person descriptions: 1872 = `AN UNMARRIED WOMAN`, 1877 = `AN UNMARRIED MAN` (via loan-entity PATCH).
- Vesting type 1871/1876 = `Individual`; Borr + CoBorr occupancy intent = `Will Occupy`.

### Gap E — Blend follow-up / No-HOA action item  ✅
`comms_actions` contains only `Order Title Report` and `Email Agent — EMD Check Copy`. **No HOA
follow-up action item** was generated (no HOA on this loan). (The separate `6.4 REO Doc Missing — HOA
Statement` flag is REO document tracking because the borrower owns the inherited property — not the
action-item graph Gap E removed.)

### Gap F — Bank statement vs 2a/VOD  ⚠️ → ✅ (bug found & fixed during this verification)
`read_vods` correctly returned all 3 URLA-2020 rows (the `Total Assets … sum of VOD balances =
$18,235.36` flag is the proof: 18,125.18 + 42.42 + 67.76). **But** the expected per-account
"Matches 2a/VOD" info flags did **not** appear. Root cause: eFolder copies store fields under
`extracted_fields` with nested `{"value": …}` entries, while `_compare_with_vod` (and the coverage
matcher) read `copy["fields"]` as flat strings — so every per-copy comparison silently skipped. This
was latent before because VODs never loaded; the `read_vods` fix exposed it.

**Fixed** (`review_urla_assets.py`): added `_copy_field()` / `_copy_has_fields()` that read
`extracted_fields` (fallback `fields`) and unwrap nested values. Re-verified offline against the run's
**real** bank-statement copies → both accounts now emit:
```
Bank Statement — Matches 2a/VOD  [info]  Account '441-1856391' (TD Bank): $42.42 matches $42.42
Bank Statement — Matches 2a/VOD  [info]  Account '8024083696' (Woodforest National Bank): $67.76 matches $67.76
```
`to_add=[]` (nothing missing → no populate). Fidelity (retirement) is correctly flagged
`VOD Account Has No Supporting Document` (no bank statement covers a retirement fund).

## Summary

| Gap | Verdict |
|-----|---------|
| A — image → contacts | Parser ✅ on the real image; not exercised in-pipeline (harness needs a fetchable image URL). Contacts populated anyway via B & C. |
| B — ESS (pre-CD) → contacts | ✅ bypass ran, escrow written from settlement statement |
| C — Purchase Agreement (SC) → contacts | ✅ agents enriched from PA |
| D — vesting writes | ✅ all writes fired (Tenancy in Common, per-person unmarried, occupancy) |
| E — no-HOA action item | ✅ no HOA action item generated |
| F — bank stmt vs 2a/VOD | ✅ after fixing a copy field-shape bug uncovered here (now emits "Matches 2a/VOD") |
