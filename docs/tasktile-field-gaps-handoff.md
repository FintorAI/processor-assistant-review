# TaskTile field-gaps — handoff (how the analysis was done)

Handoff for continuing the **TaskTile / SBIQ field-gap** work. It explains what the gap
analysis found, **how** it was produced (data sources + probe scripts), and what to change on the
SBIQ side. Source analysis lives in the **LG-LOAEncompass** repo:

- `docs/tasktile-field-gaps-tasktile-side.md` — fields the agent needs that are **not in the SBIQ
  `content_schema` at all** (SBIQ must add them).
- `docs/tasktile-field-gaps-our-side.md` — fields SBIQ **already** delivers that the agent just
  isn't mapping yet (fixed in our config, no SBIQ change).

Probe scripts referenced below are in `LG-LOAEncompass/devTool/`. Verified against SBIQ `staging`,
2026-09-09.

---

## The one insight everything rests on

TaskTile extracts each classified doc against its **SBIQ category `content_schema`** and delivers
that verbatim as the doc's `metadata` in the job manifest. So:

> **"in the SBIQ `content_schema`" == "in the manifest metadata, available to the agent for free."**

Therefore every agent-needed field is exactly one of:
1. **In `content_schema`** → already in the manifest → our-side fix (map it in config), OR
2. **Absent from `content_schema`** → TaskTile can never emit it → SBIQ schema addition (this handoff),
   and until added the agent falls back to a **LandingAI OCR** pass for that field.

The whole analysis is just classifying each field the agent consumes into bucket (1) or (2).

---

## Method (4 data sources, cross-referenced)

For each doc type the LOA agent consumes, we compared:

1. **What the agent requires** — the LandingAI extraction contract per doc type:
   `GET {EFOLDER_API_BASE_URL}/efolder/schemas/{doc_type}` (this is what downstream nodes consume,
   so it defines "required"). Plus what the agent already maps: `LG-LOAEncompass/config/tasktile_field_map.json`
   (`replaceable`, `landingai_types`, `partially_replaceable`).
2. **What SBIQ can extract** — the category `content_schema` (a JSON Schema):
   `GET https://sbiqai.staging.cybersoftbpo.ai/api/categories/{id}?fields=content_schema` (no auth, GET only).
   The manifest's `category_id` **is** the SBIQ id.
3. **What the manifest actually carries today** — real TaskTile manifests sampled across many
   LangGraph threads (`/threads/search` → each thread's `tasktile_job_ids` → `get_job_manifest`), to
   confirm the schema fields really populate in practice (schema-present but always-empty = still a gap).
4. **Per-customer overrides** — TaskTile's `awm` category set vs. the shared SBIQ base, to confirm
   which additions the TT team already shipped for AWM.

Field-by-field: required (1) − provided (2/3) = **the gap**. Gaps that are absent from `content_schema`
are the SBIQ schema additions.

### Probe scripts (LG-LOAEncompass/devTool)

| Script | What it does |
|---|---|
| `probe_sbiq_schema.py` | Resolve each doc type → SBIQ category id; dump/flatten `content_schema` field paths. `... .py` = summary; `... .py 2168` = dump one id. This is the primary in-schema vs gap check. |
| `probe_tasktile_field_gaps.py` | The full cross-reference: samples real manifests (`GAP_MAX_JOBS` cap), pulls the eFolder LandingAI schema per type, and prints, per doc type, required vs. manifest-emitted vs. **GAP**. |
| `probe_tasktile_awm_schema.py` | TaskTile `awm` set vs. shared SBIQ base — confirms the fields the TT team says they added. |
| `probe_walker_awm_fields.py` | Confirms the awm additions actually **populate** on a real customer's (Walker) manifests, not just exist in schema. |

Run (from `LG-LOAEncompass`, needs its `.env` for the LangGraph/eFolder/TaskTile creds):
```bash
.venv/bin/python devTool/probe_sbiq_schema.py           # id + field-count per doc type
.venv/bin/python devTool/probe_sbiq_schema.py 2168      # dump one category's field paths
.venv/bin/python devTool/probe_tasktile_field_gaps.py   # full required-vs-provided gap report
```
Sample manifests captured during the analysis: `LG-LOAEncompass/devTool/sample_tasktile_manifests/`.

---

## What SBIQ needs to add (the deliverable)

Add these to each category's `content_schema` in the SBIQ repo
(`sbiq-ai/src/utils/categories/{id}.json`, `content_schema`). Full detail + who consumes each field is
in `LG-LOAEncompass/docs/tasktile-field-gaps-tasktile-side.md`; summary:

| Doc (SBIQ id) | Fields to ADD | Agent consumer (node → checklist) |
|---|---|---|
| **ALTA Settlement Statement (2168)** *(biggest ask)* | `escrow_company`, `title_company`, `settlement_agent{name,contact,phone,email,address,st_license_id}`, `title_charges[]{description,buyer_debit,paid_to}`, `recording_charges[]{description,buyer_debit}` | `file_contacts.py` (05.2), `itemization_2015.py` (title/recording fees → 390, 647) |
| **Purchase Contract (200)** | `purchase_price`, `earnest_money`, `seller_credit_amount`, `closing_date`, `buyers_agent.*` (9), `sellers_agent.*` (9) | `origination.py` (03.1), `urla_1003_part3.py` (04.5), `itemization_2015.py` (02.5/10.4), `file_contacts.py` (05.1) |
| **Loan Estimate (984)** | `Application Fee`, `Processing Fee`, `Underwriting Fee`, `Escrow Property Taxes Monthly`, `Escrow Homeowners Insurance Monthly` | `itemization_2015.py` (L228, 1621, 367, 231, 230) |
| **PMI / FHA MI Certificate (816 / 1838)** | `certificate_number` | `itemization_2015.py` `_check_mortgage_insurance` (02.6 → CD1.X71) |
| **Credit Report (117)** | `credit_reference_number` (reissue/reorder id) | credit node (reissue) |
| **Closing Disclosure (819)** | `closing_date`, `disbursement_date` *(low priority; PA fallback)* | `itemization_2015.py` (02.5 fallback) |
| **Property Tax (1481)** | annual tax total **(optional)** — agent can sum installments instead | `origination.py` (borrower profile) |

**Priority:** ALTA Settlement Statement (2168) is the largest and highest-value (unblocks file
contacts + title/recording fees). Then Purchase Contract (200) and Loan Estimate (984). MI
`certificate_number` and Credit `credit_reference_number` are small, single-field, high-signal adds.
Closing Disclosure dates and Property Tax annual are optional/low-priority.

**Not needed:** VOE (436) — employer address is already in the VOE schema and `job_title` comes from
the URLA schema (349), so no VOE schema change. See the our-side doc.

---

## Verify each addition

1. Add the field to the category's `content_schema`, deploy to SBIQ `staging`.
2. Confirm it's in the schema: `python devTool/probe_sbiq_schema.py {id}` (LG-LOAEncompass) — the new
   path should appear.
3. Confirm it **populates**: reclassify a real doc of that type and check the manifest metadata
   (`probe_tasktile_field_gaps.py`, or inspect a fresh manifest) — schema-present but always-empty is
   still a gap.
4. On the agent side, the field then flows automatically once mapped in
   `config/tasktile_field_map.json` (moving the doc out of `landingai_types` / dropping its
   `partially_replaceable` entries), which stops the LandingAI fallback for it.
