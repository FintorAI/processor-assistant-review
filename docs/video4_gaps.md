# Video 4 Gaps — `processor-assistant-review`

**Source:** `notes.txt` lines 571–636 (File 4 — Cassandra Matthews & James, loan `2605968646`)
**Repo:** `/Users/naomi/Desktop/FINTOR/processor-assistant-review`
**Scope searched:** `definitions/`, `output/tools/`, `shared/`, `output/config/`, sibling `processor-assistant-communications`

**Legend:** ✅ IMPLEMENTED · 🟡 PARTIAL · ❌ NOT IMPLEMENTED · 🔒 FACTORY-LOCK

This video introduced three **document → File Contacts write** gaps (cover-letter image, ESS,
purchase contract), a **vesting write** refinement, an **action-item** decision (remove the HOA
Blend follow-up), and a **bank-statement** verification question. Each is documented below with
current state, evidence, and an implementation plan.

---

## Status matrix

### Gap A — Cover Letter image → File Contacts (`notes.txt:576-579`)

> "Write from image into file Contacts. Deleted file contacts from the cover letter so likely just
> needs to populate it in file contacts. Deleted team contacts."

| Item | Status | Evidence |
|---|---|---|
| OCR the image Almas sends (`additional_info.almas_notes_images`) | ✅ | `extract_almas_images` (substep 0.6), `data_gathering.py:2119-2250`. Claude-vision prompt explicitly asks for a `KEY CONTACTS` section; result stored on `state["almas_notes_images"]`. |
| Image text → Cover Letter (`CX.KM.SUBMISSION.NOTES`) | ✅ | `draft_cover_letter.py:106-142,183-186` appends OCR text to submission notes. |
| Strip team contacts from cover letter | ✅ | `draft_cover_letter.py:31-44` (`_strip_boilerplate` drops `team contacts` lines). |
| Image contacts → **File Contacts** (Encompass) | ❌ | `review_file_contacts.py` never reads `almas_notes_images` / `doc_fields`; it is read-only. The data-gathering docstring (`data_gathering.py:2133-2136`) *claims* substep 1.2 cross-checks the image, but no such code exists. |

### Gap B — Estimated Settlement Statement (ESS) → File Contacts (`notes.txt:581-592`)

> Settlement Agent column → Escrow (Title) Company: ST License ID → Company License #,
> Contact ST License ID → Contact License #, File # → Escrow Case #, check addresses match.
> Real Estate Broker (B) column → Buyer's Agent (same license-# logic); cross-check Purchase
> Agreement Seller's Agent against file contacts' names.

| Item | Status | Evidence |
|---|---|---|
| ESS document presence check | ✅ | `run_pre_checks.py` reads `ess_present`; `required_docs.json:428-438`. |
| ESS extraction of **contact/agent fields** (settlement agent, escrow co., license IDs, file #, broker) | ❌ | `required_docs.json` ESS schema is fees/charges only (`ess_total_closing_costs`, `ess_cash_to_close`, …) — **no contact fields**. Registry `document_type_registry.py:209-221` likewise charge-line only. Schemas live server-side (CatchingDoc) per `docs/EFOLDER_EXTRACTION.md`. |
| ESS Settlement Agent → **Escrow Company** file contact (Company License #, Contact License #, Escrow Case #) | ❌ | No contact-write tool. `encompass_client.py` has **no** `/v3/loans/{id}/contacts` write method (GET only). |
| ESS Real Estate Broker (B) → **Buyer's Agent** file contact | ❌ | Same as above. |
| Address match check (ESS vs LOS) | ❌ | Not implemented. |
| Title Report already extracts `settlement_agent` / `escrow_company` / `title_company` | 🟡 | `document_type_registry.py:167-175`, `required_docs.json:500-521` normalize these into `state["doc_fields"]`, but **no tool maps them to File Contacts**. Useful source for the Escrow Company write. |

### Gap C — Purchase Contract → File Contacts (MD/SC variants) (`notes.txt:588-606`)

> Selling brokerage → Buyer's Agent (company name, office address, broker/sales-associate MLS ID
> → company state license #, sales associate name → agent name, phone/email → agent phone/email,
> license # → contact state license #). Listing brokerage → Seller's Agent (same logic). MD and SC
> purchase contracts have different "Contact Information" layouts — consider in extraction. Sometimes
> the company/associate is googled when blank.

| Item | Status | Evidence |
|---|---|---|
| Purchase Agreement extraction (transaction fields) | ✅ | `required_docs.json:378-394` — price, dates, EMD, seller/buyer name, etc. Used by EMD review (`review_urla_emd.py`). |
| Purchase Agreement extraction of **agent/brokerage** fields | 🟡 | Registry `document_type_registry.py:189-197` *expects* `buyer_agent_company` / `seller_agent_company`, but these are **not** in `required_docs.json` and not in `FIELD_MAP` — they never reach `state`. |
| Selling brokerage → **Buyer's Agent** file contact | ❌ | No contact-write path. |
| Listing brokerage → **Seller's Agent** file contact | ❌ | No contact-write path. |
| MD / SC purchase-contract layout variants | ❌ | Only in `notes.txt:594-606`. No state-specific schema or branch. (MD handling in repo today is limited to eDisclosure presence checks, not purchase-contract contacts.) Layouts now captured below from real File-4 contracts. |
| Cross-check Purchase Agreement Seller's Agent vs existing file contacts | ❌ | Not implemented. |
| Google fallback when company/associate blank | ❌ | Not implemented (out of scope for first pass). |

#### MD vs SC contact-block layouts (from File-4 contracts)

The two states present agent/brokerage contacts very differently, so extraction must be
**state-aware**. Confirmed samples below (loan `2605968646`).

**Maryland** — brokerage blocks (`SELLING BROKERAGE` / `LISTING BROKERAGE`) with an `ACTING AS`
checkbox that disambiguates the role. The screenshot is the *Selling* brokerage acting as **Buyer
Agent** → maps to the **Buyer's Agent** file contact. (A separate `LISTING BROKERAGE` block on the
form maps to the Seller's Agent.)

![MD selling-brokerage contact block](assets/purchase_contract_md_selling_brokerage.png)

| Contract field (MD) | Sample value | → File Contact field |
|---|---|---|
| SELLING / LISTING BROKERAGE COMPANY NAME | `Revol Real Estate, LLC` | Agent **Company Name** |
| SALES ASSOCIATE NAME | `Samantha Chance` | Agent **Name** |
| SALES ASSOCIATE LICENSE NUMBER | `629147` | **Contact** State License # |
| BROKER / SALES ASSOCIATE MLS ID | `3011917` | **Company** State License # (per `notes.txt:600`) |
| OFFICE ADDRESS | `980 Mercantile DR STE L Hanover MD 21076` | Company **Address** fields |
| OFFICE PHONE | `(240) 356-1044` | Company / Office **Phone** |
| SALES ASSOCIATE PHONE | `(410) 963-6613` | Agent **Phone** |
| SALES ASSOCIATE E-MAIL | `sam@revolhomes.com` | Agent **Email** |
| ACTING AS (☒ BUYER AGENT / SELLER AGENT / SUBAGENT / DUAL) | `BUYER AGENT` | **Role selector** → Buyer's vs Seller's Agent slot |

> MD key: the `ACTING AS` radio is what routes a brokerage block to Buyer's vs Seller's Agent — do
> not assume "selling brokerage = buyer's agent" blindly; read the checkbox. License numbers split:
> the **sales-associate license** is the *contact* license, the **MLS ID** is the *company* license.

**South Carolina** — the signature/notice page directly labels **Buyer's Agent** and **Seller's
Agent** rows (Name/License #, `LLR Office Code`, Phone) plus a separate **Notice Email/Address**
block carrying the team/company name + email.

![SC buyer/seller agent block](assets/purchase_contract_sc_agent_block.png)

| Contract field (SC) | Sample value | → File Contact field |
|---|---|---|
| Notice Email/Address — team/company line | `Blake Sloan Team` / `Home Placer LLC` | Agent **Company Name** |
| Buyer's / Seller's Agent **Name** | `Joe M Scaturro` (seller side) | Agent **Name** |
| Buyer's / Seller's Agent **License #** | `121177` (seller) | **Contact** State License # |
| LLR Office Code | `27547` (seller) | **Company** State License # (SC office code) |
| Phone | Buyer `(843)222-9265` / Seller `(843)798-8333` | Agent **Phone** |
| Notice Email | `Mail@SRGmail.com` / `joe@forturro.com` | Agent **Email** |
| Seller address line | `1801 N Oak St., MB, SC 29577` | Company **Address** fields |

> SC key: role is explicit (the form has labeled "Buyer's Agent" and "Seller's Agent" rows), so no
> `ACTING AS` interpretation is needed. `LLR Office Code` is SC's company-license equivalent; the
> agent's own License # is the contact license. Company name/email come from the **Notice
> Email/Address** block, not a "brokerage company name" label like MD.

#### Probe of the live extraction (loan `2605968646`)

Confirmed the SC contract **is** in the bucket and already partially extracted (thread
`019ed0bf-c84b-7973-b094-70fd20a05700`):

- `efolder_documents["Purchase Agreement"]`: 1 copy, `James_Martin__Cassandra_Matthews (4) (1).pdf`,
  **28 extracted fields**, `ExtractionMethod: landingai`.
- The schema **already returns nested `buyers_agent` / `sellers_agent` objects** (11 subfields each:
  `mls_id, contact_name, address, city, phone, company_name, state, postal_code, fax, email,
  license_number`) **and** the state via `purchase_property_address = "5548 Daffodil Dr, Conway,
  South Carolina 29526"`, `seller.state = "SC"`, `buyers_agent.state = "SC"`.
- **Quality was uneven on the SC layout:**
  - `buyers_agent` ✅ good — `Sloan Realty Group`, `3120 Waccamaw Blvd Ste C`, `(843)222-9265`,
    `Mail@SRGmail.Com` — **but** `license_number` and `mls_id` were `null`.
  - `sellers_agent` ❌ poor — only `phone = (843)798-8333` captured; **name, company, email, and
    license were missed** (the form shows `Joe M Scaturro`, license `121177`, LLR office code
    `27547`, `joe@forturro.com`).

**So state is already available in one pass — no separate "extract state first" call is needed.**

**Extraction implication / how to choose the schema:** CatchingDoc selects schemas **strictly by the
exact doc-type string** (`"Purchase Agreement"`) — there is **no** runtime state-based selection and
**no** two-pass "extract state → swap schema" mechanism on the platform. MD and SC share the same
eFolder bucket, so two sibling schemas keyed by state is **not** supported. The supported pattern is a
**single superset schema** (LandingAI JSON Schema) whose per-field `description` strings carry
**state-conditional instructions** — e.g. on `buyers_agent`/`sellers_agent`:

> "If a Maryland form (SELLING/LISTING BROKERAGE blocks with an ACTING AS checkbox): route the block
> to buyer/seller per ACTING AS; map BROKER/SALES ASSOCIATE MLS ID → company license, SALES ASSOCIATE
> LICENSE NUMBER → contact license. If a South Carolina form (labeled Buyer's/Seller's Agent rows +
> Notice Email/Address block): map LLR Office Code → company license, agent License # → contact
> license, Notice team/company line + email → company_name/email."

Optionally add a `purchase_contract_state` discriminator field to the schema so downstream code can
branch role assignment deterministically. The extracted property state is then used by the agent
**only to validate / disambiguate role routing** (critical for MD's ACTING AS), not to pick a schema.
Canonical normalized keys downstream: `{buyer,seller}_agent_company`, `_agent_name`,
`_contact_license`, `_company_license`, `_agent_phone`, `_agent_email`, `_office_address`.

> ⚠️ **Normalization gap:** the live schema returns nested `buyers_agent.company_name`, but downstream
> tools/registry expect flat keys like `buyer_agent_company` (`document_type_registry.py:196`). The
> flatten step must be added in `data_gathering.py` (or via schema `field_mappings`) or the agent
> values never reach `state["doc_fields"]`. The schema edits themselves are **server-side** in
> `LG-docsOrch/devTool/catchingDoc` (DynamoDB), not in this repo.

### Gap D — Borrower Information – Vesting writes (`notes.txt:622-631`)

> Read Borrower Information – Summary marital status. Unmarried → vesting type (1872/1873) =
> "unmarried woman", manner held = Tenancy in Common (unmarried). Married → Tenancy by the Entirety
> or JTROS. Married but buying alone → Sole Ownership. Click Build Final Vesting. Final manner may
> only be known once title work arrives.

| Item | Status | Evidence |
|---|---|---|
| Occupancy intent (Will/Currently/Will-Not Occupy) | ✅ | `update_borrower_vesting.py:99-106` (`_compute_occupancy_intent`). Confirmed on thread `2605968646`: "Auto-corrected: Borrower/Co-Borrower Occupancy Intent". |
| Vesting **description** (field 1872 / 1877) per marital + gender | ✅ **(updated)** | `update_borrower_vesting.py` `_compute_vesting_desc` now takes the **applicant's** gender + the primary borrower's gender. Unmarried co-owners get per-person status (`AN UNMARRIED MAN` / `AN UNMARRIED WOMAN`) instead of a shared `JOINT TENANTS`; co-borrower description now uses the co-borrower's gender (fixes a latent bug). Married-couple phrase unchanged (both slots keyed to the borrower → `HUSBAND AND WIFE`). |
| Vesting **type** (field 1871 / 1876) | 🟡 | Hardcoded `"Individual"` always; no marital/gender logic (unchanged — correct for individual title holders). |
| Manner Held (field 33 / `URLA.X138`) computed | ✅ (in STEP_03) | `update_urla_lender.py` `_determine_manner_held`. Vesting tool reads field 33 read-only and flags if empty. |
| Manner Held = **Tenancy in Common** for unmarried co-owners / siblings | ✅ **(implemented)** | `_determine_manner_held`: `both_on_title and not married` → `"Tenancy in Common"` (was `As Joint Tenants`) → `URLA.X138 = TenantsInCommon`. Verified: File-4 (unmarried co-borrowers, SC) → live value `Tenancy in Common`. |
| Manner Held = **Sole Ownership** when married buying alone | ✅ **(implemented)** | `_determine_manner_held`: married + no co-borrower/NBS in a non-CP state → `"Sole Ownership"` (was `Married Woman/Man`) → `URLA.X138 = Individual`. Community-property states keep `As His/Her Sole And Separate Property` (more correct there). |
| Marital-status source | ✅ **(implemented)** | Both tools now prefer **field 52** (Borrower Summary) and fall back to **479** (Vesting form): `marital_status = summary_marital or vesting_marital`. Emits an `info` "Marital Status Source Divergence" flag when 52 and 479 disagree. Field 52 added to `step_03`/`step_09` `los_fields_read` (already global in `FIELD_MAP`). |
| Build Final Vesting (field 1867) | 🟡 | Not auto-clicked (no API button); still flags "Final Vesting Empty" when 1867 blank. Auto-fills unmarried suffix only. Unchanged — final manner may depend on title work (`notes.txt:630`). |

### Gap E — Blend Follow-Up / No-HOA action item (`notes.txt:608-619`)

> Create Blend Follow up — Action Failed. HOA letter is only for refi / 2nd-3rd property / already
> owns property — not universal, very file-specific. HOA is tricky; "maybe just remove rn".

| Item | Status | Evidence |
|---|---|---|
| No-HOA Blend follow-up action item (`hoa_loe_signature`) | ✅ → **REMOVED** | Was emitted by `_rule_hoa_loe` in `build_action_items.py` (STEP_11.3) → triggered sibling graph `processor_blend_loe`. Now removed from the `RULES` registry (function retained for future re-enable). File set to `FACTORY-LOCK: true`. |
| Other action items (title order, lock desk, EMD) | ✅ | Unchanged — still in `RULES`. |

The downstream graph `processor-assistant-communications/graphs/processor_blend_loe.py` is left
deployed but is now unreachable from review (no action item references it). See "Action taken" below.

### Gap F — Bank Statement extraction & verification (`notes.txt:632-634`)

> "Did we extract info from bank statements? Write as needed. Verify if everything matches
> (URLA Part 3, 2a)."

| Item | Status | Evidence (thread `2605968646`) |
|---|---|---|
| Extraction ran (Step 0.3 `fetch_doc_fields`, `selectionMode=All`) | ✅ | `doc_fields` populated: TD Bank, JAMES ERVIN MARTIN, Checking, acct `441-1856391`, period `05/04–06/03/2026`, begin `963.14` / end `42.42`, avg daily `383.65`, deposits `2715.55`, withdrawals `4757.25`, NSF `0`. `efolder_documents["Bank Statement"]` copy_count=2. |
| Recency check (60d Conv / 30d FHA) | ✅ | `review_urla_assets.py:237-313`. Statement end `06/03/2026` recent → no stale flag (loan is Conventional). |
| Large / green deposit sourcing flag | ✅ | Flag fired: `6.1 (warning) Large / Green Deposit Requires Sourcing`. |
| ZEL / Zelle deposit flag | 🟡 **BUG** | `bank_zel_deposits` was **populated** (`"05/06: 19.50, 05/07: 29.42, 05/19: 98.05, 05/28: 98.05"`) yet **no Zelle flag fired**. `review_urla_assets.py:315-332` substring-matches the keywords `zel/zelle/klarna/firm` *inside the value*; the value holds dates+amounts only, so the dedicated Zelle field never trips. |
| Cross-check vs VOD ("verify everything matches", URLA 2a) | ❌ | `vod_data = None`. `fetch_vod_data` exists (`data_gathering.py:2039-2094`) but is **not registered** in `__init__.py` / `workflow_config.json`, so VOD balance/account/type checks never run. |
| Write bank info back to Encompass ("write as needed") | ❌ | `review_urla_assets` is verify-only (no `_write_fields`). |
| Sufficient-months check | ⚠️ | `bank_statement_months = 1` for a Conventional loan (needs 2), but **no** "Insufficient Bank Statements" flag at 1.1 was emitted — possible gap in `run_pre_checks` month-count logic (copy_count=2 may have masked it). Worth a follow-up probe. |

---

## Bank statement investigation — conclusion (`notes.txt:632`)

**Yes, bank statements were extracted and partially verified on thread
`019ed0bf-c84b-7973-b094-70fd20a05700` (loan `2605968646`).** `fetch_doc_fields`, `run_pre_checks`,
and `review_urla_assets` all ran (twice each). Extraction quality is good (all structured fields
populated at confidence 1.0 across 2 copies).

**What worked:** field extraction, recency check, large-deposit sourcing flag.

**What did NOT happen (the "verify everything matches" part):**
1. **VOD cross-check never ran** — `fetch_vod_data` is unwired, so `state["vod_data"]` is empty and
   the balance/account/type reconciliation in `review_urla_assets.py:347-362` is a no-op. This is the
   core "matches URLA Part 3 / 2a" verification and is effectively missing in deployed runs.
2. **Zelle flag suppressed by a keyword bug** — the populated `bank_zel_deposits` field should flag
   regardless of its textual content.
3. **No write-back** — "write as needed" (e.g. asset amounts to VOD/Encompass) is not implemented.
4. **Months-sufficiency** — 1 month on a Conventional file did not raise the expected flag.

Probe script: `scripts/probe_thread.py` (added). Re-run with
`venv/bin/python scripts/probe_thread.py --loan 2605968646`.

---

## Action taken in this pass

- **Implemented the vesting writes (Gap D)** — see the Gap D matrix above. `_determine_manner_held`
  (`update_urla_lender.py`) now returns `Tenancy in Common` for unmarried co-owners and
  `Sole Ownership` for a married borrower buying alone (non-CP); both tools prefer Borrower Summary
  marital status (field 52) over the Vesting-form copy (479) and flag divergence; and
  `_compute_vesting_desc` (`update_borrower_vesting.py`) now produces per-person unmarried
  descriptions using each applicant's gender. `factory validate` PASSED; behavior verified against
  the File-4 scenario (unmarried co-borrowers, SC → `Tenancy in Common`, `AN UNMARRIED MAN` /
  `AN UNMARRIED WOMAN`).
- **Fixed a factory-reset data-loss bug** — `factory/agent_generator.py` stale-file cleanup deleted
  the hand-wired `build_action_items.py` (STEP_11.3 is not defined in YAML) because the cleanup only
  honored a hardcoded basename allowlist. Cleanup now **skips any tool file containing
  `# FACTORY-LOCK: true`**, enforcing the documented lock contract (a locked file must never be
  overwritten *or* deleted). `build_action_items.py` confirmed to survive `factory-reset` after the
  fix. ⚠️ Known remaining gap: factory-reset still regenerates `output/tools/__init__.py` and
  `output/config/workflow_config.json` **without** the hand-wired `build_action_items` registration
  (it's not in YAML), so those two files must be restored (`git checkout`) after any factory-reset
  until STEP_11.3 is either defined in YAML or added to the init/workflow templates.
- **Removed the No-HOA Blend follow-up** from Action Items: dropped `_rule_hoa_loe` from the `RULES`
  list in `output/tools/build_action_items.py` (function kept, commented rationale added) and set the
  file to `FACTORY-LOCK: true`. This is the single edit that stops the dashboard from surfacing
  "Create Blend Follow-Up — No-HOA Letter". The `processor_blend_loe` graph in the comms repo is left
  intact but unreferenced. Re-enable later by re-adding the rule once the trigger is made
  loan-specific/configurable (refi / 2nd-3rd property / already owns property).
- **No Dashboard-Officer change needed.** Verified `FINTOR/Dashboard-Officer` is fully data-driven:
  the Action Items panel renders whatever `comms_actions` the review agent emits and invokes graphs
  via `trigger.graph_id` (not `action_type`). There is **no** hardcoded `hoa_loe_signature` reference
  and no independent path that triggers `processor_blend_loe` outside the action-item dialog
  (`ReviewActionTriggerDialog.tsx` → `startWorkflow` → `assistant_id: graphId`). Once review stops
  emitting the item, the dashboard simply never shows or invokes it. The generic `processor_blend_loe`
  / `blend_loe` plumbing (types, UI labels, Lambda `VALID_GRAPHS` allowlist) can stay — it's not
  HOA-specific and may serve other LOE types later.

---

## Implementation plans

### Plan D — Vesting writes — ✅ DONE (this pass)

Implemented as described in "Action taken" and the Gap D matrix. Summary of what shipped:

1. **`update_urla_lender.py` `_determine_manner_held`** — added branches: unmarried co-owners →
   `Tenancy in Common`; married solo (non-CP) → `Sole Ownership`. CP states and Tenancy-by-the-Entirety
   unchanged. `_manner_to_urla_x138` already maps both (`TenantsInCommon` / `Individual`).
2. **Marital-status source** — both tools read **field 52 (Summary) preferred, 479 (Vesting) fallback**
   and flag divergence. Field 52 added to `step_03`/`step_09` `los_fields_read`.
3. **Vesting description (1872/1877)** — `_compute_vesting_desc` now per-applicant gender; unmarried
   co-owners get `AN UNMARRIED MAN` / `AN UNMARRIED WOMAN` (was shared `JOINT TENANTS`).
4. **Build Final Vesting (1867)** — unchanged (flag-only; no API button).

**Live value acceptance — VERIFIED (2026-06-25).** Read-only field-definition endpoints
(`/v3/settings/loan/fieldDefinitions`, `/v1/...`, `/v3/loanSchemas/properties`) all return `403`,
so allowed values were confirmed two ways instead:

1. **Prod read** of the actual File-4 loan `2605968646`: the processor had already set
   field 33 = `Tenancy in Common`, `URLA.X138` = `TenantsInCommon`, field 1867 =
   `... TENANCY IN COMMON` — so both target values are live-accepted.
2. **Round-trip write/read** on Test loan `2605926537` (`scripts/verify_vesting_fields.py
   --mode roundtrip`, originals restored): **all** candidate strings ACCEPTED by both fields:

| Field 33 written | Read-back |
|---|---|
| `Tenancy In Common` / `Sole Ownership` / `As Joint Tenants` / `Tenancy By The Entirety` | stored **verbatim** |
| `As His/Her Sole And Separate Property` | stored verbatim |
| `Unmarried Woman` / `Unmarried Man` | canonicalized → `Unmarried woman` / `Unmarried man` |

| `URLA.X138` written | Read-back |
|---|---|
| `TenantsInCommon` / `Individual` / `JointTenantsWithRightOfSurvivorship` / `TenantsByTheEntirety` | stored verbatim |

**Action taken:** field 33 stores values **verbatim** (no canonicalization for the tenancy options),
so `_determine_manner_held` now returns `"Tenancy in Common"` (lowercase "in") to match the
processor's live convention on loan `2605968646`, rather than `"Tenancy In Common"`. The
`_MANNER_TO_URLA_X138` map is keyed on lowercased values, so the X138 mapping is unaffected.
Writes are still gated to **empty** field 33 only (`_manner_held_compatible` avoids clobbering).

### Plans A/B/C — Document → File Contacts writes (shared infrastructure)

These three share the **same missing primitive**: there is no Encompass contacts write path, and the
extraction schemas lack contact fields. Sequence:

1. **Add a contacts write helper to `encompass_client.py`** — `POST`/`PATCH`
   `/encompass/v3/loans/{loanId}/contacts` (verify the exact verb/shape against the EncompassConnect
   contacts API; confirm `contactType` enums: `ESCROW_COMPANY`, `BUYERS_AGENT`, `SELLERS_AGENT`,
   `SELLER`). Wrap writes so they are auditable like `_write_fields`.
2. **Extend extraction schemas (server-side CatchingDoc + `required_docs.json` contract):**
   - ESS: add `settlement_agent_name/license_id`, `contact_license_id`, `file_number`,
     `company_address`, `real_estate_broker_*` (Buyer's Agent block).
   - Purchase Agreement: the live schema **already** returns nested `buyers_agent` / `sellers_agent`
     objects (11 subfields) + state — so the work is **improving** the single schema, not adding new
     top-level fields. Enhance the server-side schema (`LG-docsOrch/devTool/catchingDoc`) with
     **state-conditional `description` instructions** on the agent + license fields (one superset
     schema — runtime state-based schema selection is NOT supported; selection is by doc-type string).
     **MD:** route `SELLING`/`LISTING BROKERAGE` blocks via the `ACTING AS` checkbox; MLS ID → company
     license, sales-associate license → contact license. **SC:** labeled `Buyer's`/`Seller's Agent`
     rows + `Notice Email/Address` block; `LLR Office Code` → company license, agent License # →
     contact license. Add a `purchase_contract_state` discriminator field. Then **flatten** the nested
     objects to canonical keys (`{buyer,seller}_agent_company`, `_agent_name`, `_contact_license`,
     `_company_license`, `_agent_phone`, `_agent_email`, `_office_address`) in `data_gathering.py` —
     today's nested→flat normalization gap means agent values don't reach `state["doc_fields"]`.
     See the MD/SC layout tables, screenshots, and live-probe findings under Gap C.
3. **New tool `populate_file_contacts` (or extend `review_file_contacts`)** — reads `doc_fields`
   (ESS + Purchase Agreement) and `almas_notes_images`, builds contact records, and writes them via
   the new helper. Gate writes behind a presence check (don't overwrite a populated contact);
   cross-check names against existing file contacts before creating duplicates. Emit `info-overwrite`
   audit flags. Register per `.cursor/rules/tool-registration-checklist.mdc`.
4. **Cover-letter image (Plan A)** rides on the same tool: the OCR text is already in
   `state["almas_notes_images"]`; parse its `KEY CONTACTS` section as a fallback/secondary source.
5. **Address-match checks** become `warning` flags (ESS vs LOS subject property) rather than writes.

**Suggested priority:** D (vesting, self-contained) → B (ESS → Escrow Company, leveraging Title
Report fields already in state) → C (Purchase Agreement agents + MD/SC) → A (image parse as
supplementary source). Plans A–C are blocked on the contacts write helper + server-side schema work.

---

## File index

| File | Role |
|---|---|
| `output/tools/build_action_items.py` | Action-item rules (HOA rule removed; `FACTORY-LOCK: true`) |
| `output/tools/review_file_contacts.py` | Read-only File Contacts check (1.2) — target for write extension |
| `output/tools/update_borrower_vesting.py` | Vesting writes (STEP_09) |
| `output/tools/update_urla_lender.py` | `_determine_manner_held` (STEP_03) |
| `output/tools/review_urla_assets.py` 🔒 | Bank statement verification (6.1) |
| `output/tools/data_gathering.py` | `fetch_doc_fields` (0.3), `extract_almas_images` (0.6), `fetch_vod_data` (unwired) |
| `output/config/required_docs.json` | Extraction contracts (ESS / Purchase Agreement / Bank Statement) |
| `shared/document_type_registry.py` | Broader doc field expectations |
| `encompass_client.py` | Field/condition APIs — **no contacts write** (gap) |
| `scripts/probe_thread.py` | Deployed-thread probe (added this pass) |
| `processor-assistant-communications/graphs/processor_blend_loe.py` | No-HOA Blend graph (now unreferenced) |
