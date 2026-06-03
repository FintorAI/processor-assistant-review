# Flag Document References — Output Format (Frontend Spec)

This document describes the shape of the **document references** attached to review
flags so the frontend can link each flag to the source document(s) it came from.

- **Where it lives:** the `flags.json` artifact for a thread (and the `flags` key in
  graph state). Each flag may carry a `relevant_documents` array.
- **Audience:** frontend / UI integration.
- **Stability:** references are **coordinates**, not signed URLs. Coordinates are stable;
  the frontend mints a fresh download URL on demand (see [§5 Resolving a reference to a URL](#5-resolving-a-reference-to-a-url)).

---

## Table of contents

**Flag document references (eFolder documents)**
- [1. Flag object schema](#1-flag-object-schema)
- [2. `relevant_documents` — coordinate reference object](#2-relevant_documents--coordinate-reference-object)
- [3. The "Documents Present in eFolder" summary flag (substep 1.1)](#3-the-documents-present-in-efolder-summary-flag-substep-11)
- [4. `matched_copy_index` — which copy triggered the flag](#4-matched_copy_index--which-copy-triggered-the-flag)
- [5. Resolving a reference to a URL](#5-resolving-a-reference-to-a-url)
- [6. Edge case — present but no coordinate](#6-edge-case--present-but-no-coordinate)

**Almas-notes image references**
- [7. Almas-notes image references (Cover Letter, substep 7.1)](#7-almas-notes-image-references-cover-letter-substep-71)

**General**
- [8. Quick frontend checklist](#8-quick-frontend-checklist)

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
| `url` | string \| absent | **Only on Almas-notes image refs** (see §7). The DocRepo URL the frontend supplied; can be used directly to display/download. |
| `source` | string \| absent | **Only on Almas-notes image refs** — value `"almas_notes_image"`. A discriminator so the UI can distinguish these from eFolder-sourced docs. |

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

## 7. Almas-notes image references (Cover Letter, substep 7.1)

Images attached to Almas' notes are **not** eFolder documents — the frontend uploads
them to DocRepo and passes them in at invocation (see `docs/schema.md` →
`additional_info.almas_notes_images`). The agent OCRs each image (Claude vision) and:

1. **Appends the transcribed text into the cover letter** (`CX.KM.SUBMISSION.NOTES`) —
   the text is **not** in the ref, it's in the notes field itself.
2. **Attaches each image as a reference** on the `"Cover Letter — Submission Notes
   Written"` flag (`substep: "7.1"`), so the UI can show the source image.

### Input contract (what the frontend sends)

Pass the images at invocation under `additional_info.almas_notes_images` (an array).
Each item:

```json
{
  "filename": "purchase_agreement_p3.png",
  "url": "https://.../docrepo-signed-url",
  "client_id": "AWM-test",
  "doc_id": "docsorchagent/almas_notes_<...>",
  "bucket": "encompass"
}
```

| Field | Required | Meaning |
|---|---|---|
| `url` | ✅ | Fetchable image URL (the DocRepo/S3 link). Used for the vision OCR **and** echoed back on the ref. `signed_url` / `s3_url` are also accepted as fallbacks. |
| `filename` | optional | Display name; becomes the ref's `doc_type` (defaults to `"Almas Notes Image N"`). |
| `client_id` | optional | DocRepo client id — echoed onto the ref so the UI can re-mint a URL. |
| `doc_id` | optional | DocRepo object id — echoed onto the ref. |
| `bucket` | optional | DocRepo bucket — echoed onto the ref. |

Notes:
- Supported image types: PNG, JPEG, GIF, WebP.
- If only `url` is sent (no DocRepo coordinates), the ref still carries that `url`, so
  the image remains displayable/downloadable.
- An item with no `url` (and no `signed_url`/`s3_url`) is skipped for OCR.
- The full invocation contract lives in `docs/schema.md` → `additional_info` keys.

### Output ref shape

These refs use the same object as §2 but with two differences — a `source`
discriminator and a passthrough `url`, an **empty `attachment_id`**, and an **empty
`copies`** array (there's no eFolder attachment and no multi-copy concept):

```json
{
  "doc_type": "purchase_agreement_p3.png",
  "client_id": "AWM-test",
  "doc_id": "docsorchagent/almas_notes_<...>",
  "bucket": "encompass",
  "attachment_id": "",
  "url": "https://.../docrepo-signed-url",
  "source": "almas_notes_image",
  "copies": []
}
```

Frontend handling:
- Branch on `source === "almas_notes_image"`. For these, **prefer the `url` field** to
  display/download directly (it's the URL you uploaded). You may still re-mint from
  `client_id`+`doc_id` if your supplied `url` has expired.
- `doc_type` is the image filename (or `"Almas Notes Image N"` if no filename was sent).
- `copies` is always `[]` and `attachment_id` is always `""` — don't treat the empty
  `attachment_id` as the §6 "not downloadable" case here, because `doc_id`/`url` are set.

---

## 8. Quick frontend checklist

- [ ] Read flags from `flags.json`; render `relevant_documents` when present.
- [ ] For each ref, render `copies[]` (all of them); highlight `matched_copy_index` if set.
- [ ] Resolve downloads at click-time: DocRepo (`client_id`+`doc_id`) first, Encompass
      (`attachment_id`) as fallback.
- [ ] For `source === "almas_notes_image"` refs (on the 7.1 flag), use the `url` field
      directly; their `attachment_id`/`copies` are intentionally empty.
- [ ] Disable the action when `doc_id`, `attachment_id`, **and** `url` are all empty.
- [ ] Do not assume `relevant_documents` exists on every flag — absence = no associated
      present document.
