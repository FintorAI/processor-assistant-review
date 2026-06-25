"""Probe Encompass File Contacts to learn the exact contact object schema and
the available contactType values, and to discover the write endpoint/shape.

Read-only by default. With --discover-write it issues a harmless PATCH probe of
the contacts collection on a TEST loan to observe which verb/shape the API
accepts (no destructive change — it re-writes an existing contact's own value).

Usage:
    venv/bin/python scripts/probe_contacts.py --loan 2605968646 --env Prod
    venv/bin/python scripts/probe_contacts.py --loan 2605926537 --env Test --discover-write
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except Exception:
    pass

from encompass_client import get_encompass_client, reset_encompass_state


def resolve_guid(client, loan_number: str) -> str:
    if "-" in loan_number and len(loan_number) > 20:
        return loan_number
    results = client.search_loans_pipeline(loan_number=loan_number)
    if not results:
        raise RuntimeError(f"No loan found for {loan_number!r}")
    first = results[0]
    return (first if isinstance(first, str) else first.get("loanGuid") or first.get("id") or first.get("loanId")).replace("{", "").replace("}", "")


def _headers(client) -> dict:
    return {
        "accept": "application/json",
        "Authorization": f"Bearer {client.access_token}",
        "content-type": "application/json",
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--loan", required=True)
    ap.add_argument("--env", default="Prod")
    ap.add_argument("--discover-write", action="store_true")
    args = ap.parse_args()

    reset_encompass_state()
    client = get_encompass_client(env=args.env)
    guid = resolve_guid(client, args.loan)
    base = client.api_base_url
    print(f"Resolved GUID: {guid} (env={args.env})\n")

    url = f"{base}/encompass/v3/loans/{guid}/contacts"
    r = requests.get(url, headers=_headers(client), timeout=30)
    print(f"GET /v3/loans/{{id}}/contacts -> {r.status_code}")
    contacts = r.json() if r.status_code == 200 else []
    if not isinstance(contacts, list):
        contacts = []
    print(f"  {len(contacts)} contact(s)\n")
    for c in contacts:
        ct = c.get("contactType")
        keys = sorted(c.keys())
        print(f"  --- contactType={ct!r} ({len(keys)} keys) ---")
        print("  " + json.dumps(c, indent=2, default=str).replace("\n", "\n  "))
        print()

    # Show the union of all keys seen, to inform the write schema.
    all_keys: set[str] = set()
    for c in contacts:
        all_keys.update(c.keys())
    print(f"Union of contact keys observed: {sorted(all_keys)}")

    if args.discover_write:
        if args.env.upper() != "TEST":
            print("\nREFUSING --discover-write outside Test env.")
            return
        print("\n=== WRITE DISCOVERY (Test, non-destructive) ===")
        # Re-write an EXISTING contact with its OWN current values so we observe
        # the accepted verb/shape without changing any data.
        existing = next((c for c in contacts if c.get("contactType")), None)
        if not existing:
            print("  No existing contact to echo-write; skipping.")
            return
        echo = {k: v for k, v in existing.items() if k != "contactRef"}
        ct = echo.get("contactType")
        print(f"  Echo-writing existing contactType={ct!r} back with its own values.\n")
        attempts = [
            ("PATCH", url, [echo]),
            ("PATCH", url, {"contacts": [echo]}),
            ("POST", url, echo),
            ("PATCH", f"{url}/{ct}", echo),
        ]
        for verb, u, payload in attempts:
            try:
                resp = requests.request(verb, u, headers=_headers(client), json=payload, timeout=30)
                body = resp.text[:200].replace("\n", " ")
                tail = u.split("/contacts")[-1] or "(collection)"
                print(f"  {verb} contacts{tail}  payload={type(payload).__name__} -> {resp.status_code}  {body}")
            except Exception as e:
                print(f"  {verb} contacts -> ERROR {e}")


if __name__ == "__main__":
    main()
