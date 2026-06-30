"""Probe: is the File Contacts API ``referenceNumber`` the same data as loan field 186?

Runs against the TEST Encompass instance. Strategy:
  1. List the test pipeline, find a loan that has an ESCROW_COMPANY file contact.
  2. Read that contact's ``referenceNumber`` and loan field 186 (read-only first).
  3. Definitive check: write a unique sentinel to field 186, re-GET /contacts, and
     see whether the escrow contact's ``referenceNumber`` now equals the sentinel.
     Restore field 186 to its original value afterwards.

Usage:
    cd /Users/naomi/Desktop/FINTOR/processor-assistant-review
    ./venv/bin/python scripts/probe_186_vs_reference.py            # auto-discover
    ./venv/bin/python scripts/probe_186_vs_reference.py --loan 1234567890
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except Exception:
    pass

from encompass_client import get_encompass_client, reset_encompass_state  # noqa: E402


def _auth_headers(client) -> Dict[str, str]:
    return {"accept": "application/json", "Authorization": f"Bearer {client.access_token}",
            "content-type": "application/json"}


def _get(client, url: str) -> requests.Response:
    r = requests.get(url, headers=_auth_headers(client), timeout=30)
    if r.status_code == 401:
        client.refresh_token()
        r = requests.get(url, headers=_auth_headers(client), timeout=30)
    return r


def list_pipeline_guids(client, limit: int = 100) -> List[str]:
    url = f"{client.api_base_url}/encompass/v3/loanPipeline?limit={limit}"
    body = {
        "fields": ["Loan.Guid", "Loan.LoanNumber"],
        "filter": {"canonicalName": "Loan.LoanNumber", "value": "0",
                   "matchType": "greaterThanOrEquals"},
        "includeArchivedLoans": True,
        "loanOwnership": "AllLoans",
    }
    r = requests.post(url, headers=_auth_headers(client), json=body, timeout=60)
    if r.status_code == 401:
        client.refresh_token()
        r = requests.post(url, headers=_auth_headers(client), json=body, timeout=60)
    if r.status_code != 200:
        # Fallback filter variants for "list all".
        for f in (
            {"canonicalName": "Loan.LoanNumber", "matchType": "isNotEmpty"},
            {"canonicalName": "Fields.364", "matchType": "isNotEmpty"},
            {"operator": "and", "terms": [
                {"canonicalName": "Loan.LoanNumber", "matchType": "isNotEmpty"}]},
        ):
            body2 = dict(body, filter=f)
            r2 = requests.post(url, headers=_auth_headers(client), json=body2, timeout=60)
            print(f"  [pipeline retry filter={json.dumps(f)[:60]}] -> {r2.status_code}")
            if r2.status_code == 200:
                r = r2
                break
        else:
            print(f"  pipeline 400 body: {r.text[:400]}")
            r.raise_for_status()
    rows = r.json()
    guids = []
    for row in rows if isinstance(rows, list) else []:
        guid = row.get("loanId") or row.get("loanGuid") or row.get("id")
        if guid:
            guids.append(guid)
    return guids


def get_contacts(client, guid: str) -> List[Dict[str, Any]]:
    url = f"{client.api_base_url}/encompass/v3/loans/{guid}/contacts"
    r = _get(client, url)
    if r.status_code != 200:
        return []
    body = r.json()
    return body if isinstance(body, list) else []


def get_field_186(client, guid: str) -> Any:
    try:
        res = client.get_field(guid, ["186"])
        return res.get("186")
    except Exception as e:
        return f"<get_field error: {e}>"


def write_field_186(client, guid: str, value: str) -> bool:
    # Use EncompassConnect.write_fields if available (handles schema), else raw PATCH.
    try:
        client.write_fields(guid, {"186": value})
        return True
    except Exception:
        pass
    url = f"{client.api_base_url}/encompass/v3/loans/{guid}?view=id"
    r = requests.patch(url, headers=_auth_headers(client),
                       json={"customFields": [], "fields": {"186": value}}, timeout=30)
    return r.status_code in (200, 204)


def escrow_of(contacts: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    for c in contacts:
        if (c.get("contactType") or "").upper() == "ESCROW_COMPANY":
            return c
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--loan", default=None, help="Specific test loan number/GUID (optional)")
    ap.add_argument("--env", default="Test")
    ap.add_argument("--mutate", action="store_true", default=True,
                    help="Perform the definitive write-sentinel test (default on)")
    args = ap.parse_args()

    print(f"\n=== Probe 186 vs referenceNumber — env={args.env} ===")
    reset_encompass_state()
    client = get_encompass_client(env=args.env)
    print(f"api_base_url: {client.api_base_url}")

    # Build candidate GUID list.
    candidates: List[str] = []
    if args.loan:
        if "-" in args.loan and len(args.loan) > 20:
            candidates = [args.loan]
        else:
            try:
                res = client.search_loans_pipeline(loan_number=args.loan)
                first = res[0] if res else None
                guid = first if isinstance(first, str) else (first or {}).get("loanGuid") or (first or {}).get("id")
                if guid:
                    candidates = [guid]
            except Exception as e:
                print(f"  loan lookup failed: {e}")
    if not candidates:
        print("Listing test pipeline…")
        candidates = list_pipeline_guids(client, limit=150)
        print(f"  {len(candidates)} loans in pipeline")

    # Find a loan with an escrow contact.
    target = None
    escrow = None
    for guid in candidates:
        contacts = get_contacts(client, guid)
        e = escrow_of(contacts)
        if e:
            target, escrow = guid, e
            break

    if not target:
        print("No loan with an ESCROW_COMPANY contact found in the test pipeline. "
              "Pass --loan <number> for a loan that has one.")
        return

    print(f"\nTarget loan GUID: {target}")
    ref_before = escrow.get("referenceNumber")
    f186_before = get_field_186(client, target)
    print(f"  Escrow contact name      : {escrow.get('name')!r}")
    print(f"  contacts.referenceNumber : {ref_before!r}")
    print(f"  field 186                : {f186_before!r}")
    print(f"  equal (read-only)        : {str(ref_before) == str(f186_before)}")

    if not args.mutate:
        return

    sentinel = f"PRB{int(time.time()) % 100000}"
    print(f"\nDefinitive test: writing sentinel {sentinel!r} to field 186 …")
    if not write_field_186(client, target, sentinel):
        print("  field 186 write FAILED — cannot complete definitive test.")
        return
    time.sleep(2)
    contacts2 = get_contacts(client, target)
    escrow2 = escrow_of(contacts2) or {}
    ref_after = escrow2.get("referenceNumber")
    f186_after = get_field_186(client, target)
    print(f"  field 186 after write       : {f186_after!r}")
    print(f"  contacts.referenceNumber now: {ref_after!r}")
    same = str(ref_after) == sentinel
    print(f"\n  >>> referenceNumber reflects field 186 write? {'YES — SAME FIELD' if same else 'NO — DIFFERENT FIELDS'}")

    # Restore.
    restore = "" if f186_before in (None, "", "None") else str(f186_before)
    print(f"\nRestoring field 186 to {restore!r} …")
    write_field_186(client, target, restore)
    print("Done.")


if __name__ == "__main__":
    main()
