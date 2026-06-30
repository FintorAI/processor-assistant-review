"""Probe the LangGraph deployment thread state for the Driver's License / ID
expiry investigation (Video 5, loan 2605968111).

Usage:
    ./venv/bin/python scripts/probe_thread_dl.py [THREAD_ID]
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except Exception:
    pass

from langgraph_sdk import get_sync_client  # noqa: E402

THREAD_ID = sys.argv[1] if len(sys.argv) > 1 else "019f02d0-c57e-71e1-9994-138a0a72a584"

DL_DOC_TYPES = ["Driver's License", "Passport", "Permanent Resident Card"]
DL_FIELD_KEYS = [
    "dl_present", "dl_expiry", "dl_name", "dl_borrower_name",
    "dl_ethnicity_indicator", "borrower_first_name", "borrower_last_name",
    "borrower_dob",
]


def main() -> None:
    url = os.environ.get("PROCESSOR_ASSISTANT_REVIEW_URL")
    api_key = os.environ.get("LANGGRAPH_API_KEY") or os.environ.get("LANGGRAPH_TOKEN")
    if not url:
        print("PROCESSOR_ASSISTANT_REVIEW_URL not set")
        return
    print(f"Deployment: {url}")
    print(f"Thread:     {THREAD_ID}\n")

    client = get_sync_client(url=url, api_key=api_key)
    state = client.threads.get_state(THREAD_ID)
    values = state.get("values", {}) if isinstance(state, dict) else {}

    loan_id = values.get("loan_id")
    print(f"loan_id: {loan_id}\n")

    # ── eFolder presence for ID docs ──
    efolder = values.get("efolder_documents", {}) or {}
    print("=== eFolder documents (ID-related) ===")
    for dt in DL_DOC_TYPES:
        entry = efolder.get(dt)
        if entry is None:
            print(f"  {dt}: <not in efolder_documents>")
        elif isinstance(entry, dict):
            keys = {k: entry.get(k) for k in
                    ("status", "copy_count", "efolder_attachment_count", "bucket")
                    if k in entry}
            print(f"  {dt}: {keys}")
        else:
            print(f"  {dt}: {entry!r}")
    print()

    # ── Full Driver's License eFolder entry (copies + extracted fields) ──
    dl_entry = efolder.get("Driver's License")
    print("=== Driver's License efolder_documents entry (full) ===")
    print(json.dumps(dl_entry, indent=2, default=str)[:4000])
    print()

    # ── Extracted doc fields for DL ──
    doc_fields = values.get("doc_fields", {}) or {}
    print("=== doc_fields (DL-related) ===")
    for k in DL_FIELD_KEYS:
        entry = doc_fields.get(k)
        if entry is None:
            print(f"  {k}: <absent>")
        elif isinstance(entry, dict):
            print(f"  {k}: value={entry.get('value')!r} status={entry.get('status')!r} "
                  f"source={entry.get('source_doc') or entry.get('doc_type')!r}")
        else:
            print(f"  {k}: {entry!r}")
    print()

    # ── Borrower-summary flags about ID/expiry ──
    flags = values.get("flags", []) or []
    print(f"=== flags mentioning ID / license / expir (of {len(flags)} total) ===")
    hit = False
    for f in flags:
        if not isinstance(f, dict):
            continue
        blob = (f.get("title", "") + " " + f.get("details", "")).lower()
        if any(w in blob for w in ("expir", "license", "government id", "borrower id", "photo id")):
            hit = True
            print(f"  [{f.get('substep')}] {f.get('title')} ({f.get('severity')})")
            print(f"      {f.get('details')}")
    if not hit:
        print("  <none>")


if __name__ == "__main__":
    main()
