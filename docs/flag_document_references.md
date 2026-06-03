# Flag Document References — Output Format (Frontend Spec)

This document describes the shape of the **document references** attached to review
flags so the frontend can link each flag to the source document(s) it came from.

- **Where it lives:** the `flags.json` artifact for a thread (and the `flags` key in
  graph state). Each flag may carry a `relevant_documents` array.
- **Audience:** frontend / UI integration.
- **Stability:** references are **coordinates**, not signed URLs. Coordinates are stable;
  the frontend mints a fresh download URL on demand (see [Resolving a reference to a URL](#resolving-a-reference-to-a-url)).

---

## 1. Flag object schema

Every flag is a JSON object with these fields:

| Field | Type | Notes |
|---|---|---|
| `substep` | string | Workflow substep id, e.g. `"2.1"`, `"4.1"`. |
| `substep_name` | string | Human-readable substep name (not present on every flag). |
| `title` | string | Short flag title. |
| `severity` | string | One of `"info"`, `"warning"`, `"info-overwrite"`. |
| `details` | string | Detailed explanation / the compared values. |
| `suggestion` | string | Recommended action for the processor. |
| `resolved` | boolean | Whether the flag has been resolved. |
| `timestamp` | string | ISO-8601 (UTC) creation time. |
| `relevant_documents` | array \| absent | Document coordinate refs (see below). **Omitted entirely** when no present document is associated with the flag. |

> **Important:** `relevant_documents` is only present when at least one referenced
> document **actually exists in the eFolder**. If a flag references a document that is
> absent (e.g. a Driver's License that was never uploaded), the key is dropped — do
> **not** assume the array is always present, and do not treat its absence as an error.

---

## 2. `relevant_documents` — coordinate reference object

Each entry in `relevant_documents` is a **document coordinate** object:

```json
{
  "doc_type": "VOE - non service provider",
  "client_id": "AWM-test",
  "doc_id": "docsorchagent/VOE - non service provider_3a9c1320_1780385680",
  "bucket": "encompass",
  "attachment_id": "bab749e3-2dca-48b5-8e44-eb93eecaa10e",
  "encompass_bucket": "VOE - non service provider",
  "copies": [
    {
      "copy_index": 0,
      "doc_id": "docsorchagent/VOE - non service provider_3a9c1320_1780385680",
      "bucket": "encompass",
      "client_id": "AWM-test",
      "attachment_id": "bab749e3-2dca-48b5-8e44-eb93eecaa10e",
      "status": "completed"
    },
    {
      "copy_index": 1,
      "doc_id": "docsorchagent/VOE - non service provider_3a9c1320_1780385649",
      "bucket": "encompass",
      "client_id": "AWM-test",
      "attachment_id": "4dd1c987-3cdf-40a1-8210-c48e7183a16a",
      "status": "completed"
    }
  ],
  "matched_copy_index": 0
}
```

### Top-level fields

| Field | Type | Meaning |
|---|---|---|
| `doc_type` | string | Canonical document type (the agent's name for the bucket). |
| `client_id` | string | DocRepo client/tenant id. Pair with `doc_id` to fetch from DocRepo. |
| `doc_id` | string | DocRepo object id (a.k.a. `DocRepoLocation`). The primary copy's id. |
| `bucket` | string | DocRepo storage bucket (e.g. `"encompass"`). |
| `attachment_id` | string | Encompass eFolder attachment GUID for the primary copy (fallback download path). |
| `encompass_bucket` | string | The **actual Encompass eFolder bucket title** (may differ from `doc_type`, e.g. `"1003 URLA"` → `"1003"`). |
| `copies` | array | Every attachment in the bucket (see below). The top-level `doc_id`/`attachment_id` mirror `copies[0]`. |
| `matched_copy_index` | int \| absent | **Only on some flags.** Index into `copies` of the specific copy that triggered the flag (see [Matched copy](#4-matched_copy_index--which-copy-triggered-the-flag)). |

### `copies[]` entries

Each copy is one physical attachment in the bucket:

| Field | Type | Meaning |
|---|---|---|
| `copy_index` | int | 0-based index of this copy within the bucket. |
| `doc_id` | string | DocRepo object id for this specific copy. |
| `bucket` | string | DocRepo bucket. |
| `client_id` | string | DocRepo client id. |
| `attachment_id` | string | Encompass attachment GUID for this copy. |
| `status` | string | Extraction status, e.g. `"completed"`. Copies with a usable extraction; failed/`not_found` stubs are filtered out. |

> **Display all copies.** Multi-attachment buckets (VOE, Bank Statement, Underwriting,
> 1003, …) commonly have several copies. The agent intentionally includes **all** of
> them so the UI can show the full set, not just the one used for a comparison.

---

## 3. The "Documents Present in eFolder" summary flag (substep 1.1)

Pre-checks emit a single `info` flag titled **`"Documents Present in eFolder"`**. Its
`relevant_documents` lists a coordinate ref (same schema as above) for **every** checked
document that is actually present in the eFolder. Use this for an at-a-glance
"documents on file" panel.

- Missing documents are reported as **separate** individual flags (one per missing
  doc), not inside this summary.
- A present document with no usable extraction can appear here with **empty** `doc_id`
  and `attachment_id` (see next section).

---

## 4. `matched_copy_index` — which copy triggered the flag

For comparison flags against multi-copy buckets (e.g. *Employer Name Mismatch* compares
Encompass against the VOE), the ref includes `matched_copy_index` pointing at the copy
whose extracted value was used. The UI can highlight that copy while still listing all
copies. When a flag concerns the whole bucket (not a single copy), `matched_copy_index`
is omitted.

---

## 5. Resolving a reference to a URL

References are coordinates, not URLs. Two resolution paths:

1. **DocRepo (preferred)** — use `client_id` + `doc_id` (+ `bucket`) to request a fresh
   presigned URL from the DocRepo service. These coordinates come from the extraction
   pipeline (`DocRepoLocation` / `Client` / `Bucket`).
2. **Encompass fallback** — use `attachment_id` (the eFolder attachment GUID) together
   with the loan id to download directly via the Encompass attachment-download API. Use
   this when DocRepo coordinates are empty but `attachment_id` is set.

> Always mint the URL at click-time. Do not cache presigned URLs — they expire.

---

## 6. Edge case — present but no coordinate

A document can be **present in the eFolder yet have no DocRepo coordinate** when the
extraction service produced no usable result for it. In that case the ref looks like:

```json
{
  "doc_type": "Estimated Settlement Statement",
  "client_id": "",
  "doc_id": "",
  "bucket": "",
  "attachment_id": "",
  "encompass_bucket": "Estimated Settlement Statement",
  "copies": []
}
```

Frontend handling:
- Treat empty `doc_id` **and** empty `attachment_id` as "not downloadable yet" — show
  the document name but disable the download/open action.
- This is a known gap being tracked (Encompass `attachmentId` backfill). Most present
  documents resolve to a full coordinate; this only affects buckets the extractor can't
  process.

---

## 7. Quick frontend checklist

- [ ] Read flags from `flags.json`; render `relevant_documents` when present.
- [ ] For each ref, render `copies[]` (all of them); highlight `matched_copy_index` if set.
- [ ] Resolve downloads at click-time: DocRepo (`client_id`+`doc_id`) first, Encompass
      (`attachment_id`) as fallback.
- [ ] Disable the action when both `doc_id` and `attachment_id` are empty.
- [ ] Do not assume `relevant_documents` exists on every flag — absence = no associated
      present document.
