# Dashboard Updates — Video 6 Feedback (2026-07-21)

Companion to `docs/feedback6.md`. Covers backend changes just shipped that the
dashboard should reflect, plus items that are **dashboard-only fixes** (no
backend change needed) and items still **open / needs a decision**.

Updated `workflow_steps.csv` and `workflow_substeps_detailed.csv` (repo root)
are regenerated from the current `output/config/workflow_config.json` /
`definitions/*.yaml` — re-import both for the latest field/flag lists.

---

## 1. Backend changes shipped this round (dashboard should just pick these up)

These ship through the normal `flags` / `los_fields` / `field_updates` state —
no dashboard schema change required, but listed so you know what new flag
titles / field writes can now appear in a run.

| Area | What changed | New flag titles you may see |
|---|---|---|
| **FHA Management (12.1)** | Field **2996** ("FHA Management — Property Type") is now written `"1 Unit"` (verified live value) whenever the subject property is confirmed single-unit (Property Type 1041 / Units 16, no HOA, not condo/PUD/2-4 unit). **Runs for every loan type**, not just FHA — it's a shared field that reflects on other forms. | `FHA Management Property Type Set to 1 Unit` (info) / `FHA Management Property Type — Not Confirmed 1 Unit` (warning) |
| **HUD Transmittal (12.2)** | Field **1067** ("Construction Status") is now written `"ExistingConstruction"` when blank (99% of loans). Moved from Transmittal Summary (11.1) to HUD Transmittal (12.2) per processor feedback, but — like 2996 — **runs for every loan type**, since it's shared with Transmittal Summary/other forms, not FHA-gated. | `Construction Status Set to Existing` (info) / `Construction Status — Confirm Value` (info) |
| **Transmittal Summary (11.1)** | Unchanged behavior, just moved: still writes field **16** ("# of Units") = `"1"` for a confirmed single-family / no-HOA / non-PUD property. (This is a *different* field from 2996 above — same underlying "1 unit" determination, written to two different screens.) | `Number of Units Set to 1` (info) / `Number of Units — Unexpected Value` (warning) |
| **Manner in Which Title Will Be Held (3.1 / 10.1)** | Cross-checks **URLA.X136 ("Title Names")**, not just the borrower/co-borrower names, when detecting a non-borrowing spouse (NBS) for Tenancy by the Entirety. A name on title that matches neither borrower nor co-borrower now raises an info flag even if `CX.NBSFLAG` was never set. | `Additional Party on Title Names (Non-Borrowing Spouse)` (info) |
| **Processor Closing (14.2)** | Signing Date / Wire Requested Date / Est. Closing Date are set to the same date for Purchase loans — confirmed this already matches for Maryland. **Michigan is now excluded** from the auto-set Wire Requested Date (`CX.WIREDATELO`); it's flagged for manual confirmation instead, since Michigan's wire timing (e.g. 8/13 vs. closing date) differs from Maryland's same-day pattern. | `Michigan Wire Date Needs Manual Confirmation` (info) |
| **VOD coverage (URLA Assets, 6.1)** | Multiple unmatched VOD accounts at the *same bank* are now lumped into **one flag per institution** instead of one flag per account row. | `{N} VOD accounts at '{Bank}' ... have no matching bank statement` (single row per bank) |
| **Credit Reference Number** | Already cross-checked before this round — confirmed working: Encompass field 300 vs. the credit report's extracted reference number (mismatch / missing / stale checks). No change needed here. | `Credit Reference Number Mismatch` / `Credit Reference Number Missing` (pre-existing) |

---

## 2. Dashboard-only fix — PDF "cannot access" (Almas notes images)

**Root cause confirmed: not a backend data problem.** The backend already
does the right thing — see `docs/dashboard_items_processor_06032026.md` §5–7,
which is the existing frontend contract doc for this exact case.

- `almas_notes_images` refs carry a `url` **plus** DocRepo coordinates
  (`client_id`, `doc_id`, `bucket`) whenever the frontend supplied them at
  invocation (`output/tools/data_gathering.py::extract_almas_images`).
- The `url` is a **presigned S3 link that expires**. Regular eFolder documents
  work in the dashboard because their viewer re-mints a fresh presigned URL
  from the coordinates *at click time*; the Almas-image viewer appears to be
  using the cached `url` directly instead, so it 404s/"cannot access" once
  the link has expired.
- **Fix is entirely dashboard-side:** when opening an Almas-notes image,
  re-mint the URL from `client_id` + `doc_id` (+ `bucket`) via DocRepo the
  same way the eFolder document viewer already does, rather than opening the
  stored `url` directly. If `client_id`/`doc_id` are empty for an image (only
  raw `url` was sent), that image was never given DocRepo coordinates and the
  cached `url` is the only option — those should be treated as "may go stale"
  rather than a broken link.

---

## 3. Open items — need a product/UX decision before implementing

These came up in the feedback but are bigger structural changes than a field
write, so flagging for a decision rather than shipping silently.

### 3.1 Step reordering (Cover Letter first; property check → Pre-Checks)

- Cover Letter is currently **STEP_09** (after all six 1003/Flood-Insurance
  data-review steps), not first. Processors said it's the first thing they
  actually do.
- The property-listing / Zillow verification check currently lives inside
  **Transmittal Summary (11.1)** (PUD detection via HasData) — the feedback
  asks whether this should move to **Pre-Checks (STEP_01)** instead, so the
  property is verified up front rather than deep into form updates.
- **Not implemented yet** — reordering steps changes the substep numbering
  scheme (`X.Y` ids) that flags/CSVs/dashboard already key off of, so this
  needs sign-off before a broader renumbering pass. Recommend scoping as its
  own follow-up rather than bundling with today's field-level fixes.

### 3.2 Processor Workflow steps "missing" in dashboard

- Confirmed **not missing from agent state** — `STEP_14` ("Processor Workflow
  and Closing") has 3 substeps (`Processor Workflow Update`,
  `Processor Closing Update`, `Build Action Items`) all present in
  `workflow_config.json` and all run with real tools attached.
- If they're not visible in the dashboard, it's a **dashboard rendering gap**
  (this step/phase isn't being surfaced in whatever step list the UI reads),
  not a backend data gap. Worth checking which phase filter the dashboard
  uses — `STEP_14` is phase `FORM_UPDATES`, same phase as Cover Letter,
  Vesting, Transmittal Summary, and FHA forms.

### 3.3 Fannie Mae Additional Data — HomeReady (08 Home Ready)

- Feedback item: "Fannie Mae's Community Lending Product: 08 Home Ready."
  Not investigated/implemented this round — no corresponding YAML/tool exists
  yet. Needs its own scoping pass (which field, which form, what triggers
  HomeReady eligibility) before a dashboard change is meaningful.

### 3.4 Dry vs. Wet states / Refinance closing dates

- Feedback flagged a difference between Dry and Wet closing states, and asked
  about Refinance-specific closing-date handling. `update_processor_closing.py`
  currently only special-cases MD (same-day) vs. MI (manual confirm) for
  Purchase loans; Dry/Wet state logic and Refi-specific dates are **not yet
  implemented**. No dashboard change until that logic exists.

---

## 4. CSV re-exports

Regenerated after today's YAML changes:

- `workflow_steps.csv` — step/substep/tool map (source: `workflow_config.json`)
- `workflow_substeps_detailed.csv` — per-substep fields read/written + flags
  (source: `definitions/*.yaml`)

Regenerate anytime with:

```bash
python3.11 scripts/export_workflow_csv.py
python3.11 scripts/export_workflow_substeps_detailed_csv.py
```
