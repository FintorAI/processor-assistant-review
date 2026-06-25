"""Probe a deployed LangGraph thread to inspect whether bank-statement
extraction + verification ran for a given loan.

Searches the deployment's threads for one whose input/state references the
target loan number, then dumps the bank-statement-relevant slices of state:
  - doc_fields (bank_* keys)
  - efolder_documents["Bank Statement"] copy inventory
  - flags emitted at substeps 1.1 (run_pre_checks) and 6.1 (review_urla_assets)
  - which tools ran (fetch_doc_fields / review_urla_assets / fetch_vod_data)

Usage:
    cd /Users/naomi/Desktop/FINTOR/processor-assistant-review
    venv/bin/python scripts/probe_thread.py --loan 2605968646
    venv/bin/python scripts/probe_thread.py --thread <thread_id>
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except Exception:
    pass

from langgraph_sdk import get_client

URL = os.environ.get("PROCESSOR_ASSISTANT_REVIEW_URL", "").strip()
API_KEY = (os.environ.get("LANGGRAPH_API_KEY") or os.environ.get("LANGSMITH_API_KEY") or "").strip()

BANK_KEYS = (
    "institution_name", "bank_institution_name", "account_holder_name", "account_type",
    "bank_account_number", "account_number", "statement_period_start", "statement_period_end",
    "bank_statement_date", "bank_statement_months", "beginning_balance", "ending_balance",
    "bank_balance", "average_daily_balance", "total_deposits", "total_withdrawals",
    "bank_large_deposits", "bank_zel_deposits", "payroll_deposits",
    "nsf_overdraft_count", "nsf_overdraft_fees",
)


def _loan_in_obj(obj: Any, loan: str) -> bool:
    try:
        return loan in json.dumps(obj, default=str)
    except Exception:
        return False


async def find_thread_for_loan(client, loan: str) -> Optional[Dict[str, Any]]:
    """Page through threads, return first whose metadata/values mention the loan."""
    offset = 0
    page = 100
    scanned = 0
    while True:
        threads = await client.threads.search(limit=page, offset=offset)
        if not threads:
            break
        for t in threads:
            scanned += 1
            md = t.get("metadata") or {}
            vals = t.get("values") or {}
            if _loan_in_obj(md, loan) or _loan_in_obj(vals.get("loan_number"), loan) or _loan_in_obj(vals, loan):
                print(f"  matched thread after scanning {scanned} threads")
                return t
        if len(threads) < page:
            break
        offset += page
    print(f"  scanned {scanned} threads, no match for loan {loan}")
    return None


def _doc_field_summary(doc_fields: Dict[str, Any]) -> None:
    if not doc_fields:
        print("  doc_fields: EMPTY (extraction did not populate any fields)")
        return
    present = {k: doc_fields.get(k) for k in BANK_KEYS if k in doc_fields}
    print(f"  doc_fields total keys: {len(doc_fields)}")
    if not present:
        print("  bank-statement keys in doc_fields: NONE")
    else:
        print("  bank-statement keys in doc_fields:")
        for k, v in present.items():
            sval = json.dumps(v, default=str)
            if len(sval) > 200:
                sval = sval[:200] + "…"
            print(f"    {k} = {sval}")


def _efolder_summary(efolder: Dict[str, Any]) -> None:
    if not efolder:
        print("  efolder_documents: EMPTY")
        return
    keys = list(efolder.keys())
    print(f"  efolder_documents buckets: {keys}")
    for name in efolder:
        if "bank" in name.lower():
            entry = efolder[name]
            mode = entry.get("extraction_mode") if isinstance(entry, dict) else None
            copies = entry.get("copies") if isinstance(entry, dict) else None
            cc = entry.get("copy_count") if isinstance(entry, dict) else None
            print(f"  -> {name!r}: mode={mode} copy_count={cc} copies={len(copies) if isinstance(copies, list) else copies}")


def _flags_summary(flags: List[Dict[str, Any]]) -> None:
    if not flags:
        print("  flags: NONE")
        return
    bank_substeps = {"1.1", "6.1"}
    print(f"  total flags: {len(flags)}")
    for f in flags:
        ss = str(f.get("substep", ""))
        title = f.get("title", "")
        sev = f.get("severity", "")
        tl = title.lower()
        if ss in bank_substeps or "bank" in tl or "vod" in tl or "deposit" in tl or "zel" in tl:
            print(f"    [{ss}] ({sev}) {title}")


def _tools_ran(messages: List[Dict[str, Any]]) -> None:
    names = []
    for m in messages or []:
        # tool calls live on AI messages
        for tc in (m.get("tool_calls") or []):
            n = tc.get("name")
            if n:
                names.append(n)
        # also additional_kwargs style
        ak = m.get("additional_kwargs") or {}
        for tc in (ak.get("tool_calls") or []):
            fn = (tc.get("function") or {}).get("name")
            if fn:
                names.append(fn)
        if m.get("type") == "tool" and m.get("name"):
            names.append(m.get("name"))
    interesting = ("fetch_doc_fields", "review_urla_assets", "run_pre_checks", "fetch_vod_data")
    counts = {n: names.count(n) for n in interesting}
    print(f"  tool invocations (relevant): {counts}")
    return counts


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--loan", help="Loan number to search threads for")
    ap.add_argument("--thread", help="Thread id (skip search)")
    ap.add_argument("--dump", help="Optional path to dump full state JSON")
    args = ap.parse_args()

    if not URL:
        print("ERROR: PROCESSOR_ASSISTANT_REVIEW_URL not set in .env")
        sys.exit(1)
    print(f"Deployment: {URL}")
    client = get_client(url=URL, api_key=API_KEY or None)

    if args.thread:
        thread_id = args.thread
    else:
        if not args.loan:
            print("ERROR: pass --loan or --thread")
            sys.exit(1)
        print(f"\nSearching threads for loan {args.loan}...")
        t = await find_thread_for_loan(client, args.loan)
        if not t:
            sys.exit(2)
        thread_id = t.get("thread_id")
        print(f"  thread_id={thread_id}")
        print(f"  status={t.get('status')}  created={t.get('created_at')}")

    print(f"\nFetching state for thread {thread_id}...")
    state = await client.threads.get_state(thread_id)
    values = state.get("values") or {}

    if args.dump:
        Path(args.dump).write_text(json.dumps(state, indent=2, default=str))
        print(f"  full state written to {args.dump}")

    print(f"\n=== loan_number={values.get('loan_number')!r}  borrower={values.get('borrower_name')!r}  loan_id={values.get('loan_id')!r} ===")

    print("\n--- Tools that ran ---")
    _tools_ran(values.get("messages") or [])

    print("\n--- doc_fields (bank statement) ---")
    _doc_field_summary(values.get("doc_fields") or {})

    print("\n--- efolder_documents ---")
    _efolder_summary(values.get("efolder_documents") or {})

    print("\n--- vod_data ---")
    vod = values.get("vod_data")
    print(f"  vod_data rows: {len(vod) if isinstance(vod, list) else vod!r}")

    print("\n--- flags (bank/VOD/1.1/6.1) ---")
    _flags_summary(values.get("flags") or [])

    print("\n--- step_reports (6.1 / assets) ---")
    for rep in (values.get("step_reports") or []):
        if not isinstance(rep, dict):
            continue
        ss = str(rep.get("substep", rep.get("step", "")))
        if ss.startswith("6") or "asset" in json.dumps(rep, default=str).lower():
            print(f"    {json.dumps(rep, default=str)[:300]}")


if __name__ == "__main__":
    asyncio.run(main())
