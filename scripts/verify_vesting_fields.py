"""Verify that Encompass field 33 (Manner Held) and URLA.X138 accept the
values the vesting logic now writes.

Two modes:
  --mode read   (default): read-only. Introspects the client for a
       field-definition capability, tries the REST fieldDefinitions endpoint
       for allowed values, and reads the current 33 / URLA.X138 on --loan.
  --mode roundtrip: on a TEST-env loan, write each candidate value, read it
       back, then RESTORE the original. Confirms acceptance definitively.
       Only run against a disposable test loan.

Usage:
    venv/bin/python scripts/verify_vesting_fields.py --loan 2605968646 --env Prod
    venv/bin/python scripts/verify_vesting_fields.py --loan 2605926537 --env Test --mode roundtrip
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
from shared.encompass_io import read_fields, write_field, humanize_write_error

FIELDS = ["33", "URLA.X138", "1867"]

# Candidate (field_id, value) the new vesting logic emits.
CANDIDATES = [
    ("33", "Tenancy In Common"),
    ("33", "Sole Ownership"),
    ("33", "As Joint Tenants"),
    ("33", "Tenancy By The Entirety"),
    ("33", "As Her Sole And Separate Property"),
    ("33", "As His Sole And Separate Property"),
    ("33", "Unmarried Woman"),
    ("33", "Unmarried Man"),
    ("URLA.X138", "TenantsInCommon"),
    ("URLA.X138", "Individual"),
    ("URLA.X138", "JointTenantsWithRightOfSurvivorship"),
    ("URLA.X138", "TenantsByTheEntirety"),
]


def resolve_guid(client, loan_number: str) -> str:
    if "-" in loan_number and len(loan_number) > 20:
        return loan_number
    results = client.search_loans_pipeline(loan_number=loan_number)
    if not results:
        raise RuntimeError(f"No loan found for {loan_number!r}")
    first = results[0]
    return (first if isinstance(first, str) else first.get("loanGuid") or first.get("id") or first.get("loanId")).replace("{", "").replace("}", "")


def introspect(client) -> None:
    meths = [m for m in dir(client) if not m.startswith("_")]
    hits = [m for m in meths if any(k in m.lower() for k in ("field", "schema", "definition", "metadata", "option"))]
    print(f"  client field/schema-ish methods: {hits or '(none)'}")


def try_field_definitions(client) -> None:
    """Attempt the read-only Encompass field-definitions REST endpoint(s)."""
    base = getattr(client, "api_base_url", "")
    token = getattr(client, "access_token", "")
    headers = {"accept": "application/json", "Authorization": f"Bearer {token}"}
    candidates_urls = [
        f"{base}/encompass/v3/settings/loan/fieldDefinitions?fieldIds=33,URLA.X138",
        f"{base}/encompass/v1/settings/loan/fieldDefinitions?fieldIds=33,URLA.X138",
        f"{base}/encompass/v3/loanSchemas/properties?ids=33,URLA.X138",
    ]
    for url in candidates_urls:
        try:
            r = requests.get(url, headers=headers, timeout=20)
            print(f"  GET {url.split('?')[0].split('/encompass')[1]} -> {r.status_code}")
            if r.status_code == 200:
                body = r.json()
                txt = json.dumps(body, indent=2, default=str)
                print(txt[:1800])
                return
        except Exception as e:
            print(f"  GET {url[:60]}... ERROR: {e}")
    print("  (no read-only field-definitions endpoint returned 200 — use --mode roundtrip)")


def mode_read(client, guid: str) -> None:
    print("\n--- Client introspection ---")
    introspect(client)
    print("\n--- Field definitions (read-only allowed values) ---")
    try_field_definitions(client)
    print(f"\n--- Current values on loan {guid[:8]} ---")
    vals = read_fields(guid, FIELDS)
    for fid in FIELDS:
        print(f"  field {fid:10s} = {vals.get(fid)!r}")


def mode_roundtrip(client, guid: str) -> None:
    print(f"\n=== ROUND-TRIP on TEST loan {guid[:8]} (originals will be restored) ===")
    originals = read_fields(guid, FIELDS)
    print(f"  originals: {json.dumps(originals, default=str)}")

    results = []
    try:
        for fid, val in CANDIDATES:
            ok, readback, err = False, None, None
            try:
                ok = write_field(guid, fid, val, state={"env": "Test"})
            except Exception as e:
                err = humanize_write_error(str(e))
            if ok:
                rb = read_fields(guid, [fid])
                readback = rb.get(fid)
            accepted = ok and readback is not None and str(readback).strip().lower() == val.strip().lower()
            canonicalized = ok and readback is not None and not accepted
            verdict = "ACCEPTED" if accepted else ("CANONICALIZED" if canonicalized else "REJECTED")
            results.append((fid, val, verdict, readback, err))
            print(f"  [{verdict:13s}] {fid:10s} <- {val!r:40s} read-back={readback!r}" + (f"  err={err}" if err else ""))
    finally:
        # Restore originals (write back each field's original value, even if None→skip)
        print("\n--- Restoring originals ---")
        for fid in FIELDS:
            orig = originals.get(fid)
            if orig is None:
                print(f"  field {fid}: original was empty — leaving as-is (cannot blank reliably)")
                continue
            try:
                write_field(guid, fid, orig, state={"env": "Test"})
                print(f"  restored field {fid} = {orig!r}")
            except Exception as e:
                print(f"  WARN: could not restore field {fid}: {e}")

    print("\n--- Summary ---")
    for fid, val, verdict, rb, err in results:
        print(f"  {verdict:13s} {fid:10s} {val!r:40s} -> {rb!r}")


def mode_write1867(client, guid: str) -> None:
    """Test whether field 1867 (Final Vesting) is directly writable via the API.

    Writes a sample final-vesting string, reads it back, then restores the
    original (blanking it if it started empty). TEST env only.
    """
    print(f"\n=== FINAL VESTING (1867) WRITE TEST on TEST loan {guid[:8]} ===")
    original = read_fields(guid, ["1867"]).get("1867")
    print(f"  original 1867 = {original!r}")

    test_val = "JANE Q DOE, AN UNMARRIED WOMAN"
    print(f"\n--- Writing test value ---")
    try:
        ok = write_field(guid, "1867", test_val, state={"env": "Test"})
        rb = read_fields(guid, ["1867"]).get("1867")
        accepted = ok and rb is not None and str(rb).strip().lower() == test_val.strip().lower()
        verdict = "ACCEPTED (writable)" if accepted else (
            "CANONICALIZED/PARTIAL" if (ok and rb) else "REJECTED (read-only or blocked)")
        print(f"  [{verdict}] 1867 <- {test_val!r}")
        print(f"  read-back = {rb!r}")
    except Exception as e:
        print(f"  REJECTED — write raised: {humanize_write_error(str(e))}")
    finally:
        print("\n--- Restoring original ---")
        restore_to = original if original is not None else ""
        try:
            write_field(guid, "1867", restore_to, state={"env": "Test"})
            rb2 = read_fields(guid, ["1867"]).get("1867")
            print(f"  restored 1867 = {rb2!r}")
        except Exception as e:
            print(f"  WARN: could not restore 1867: {e}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--loan", required=True)
    ap.add_argument("--env", default="Prod")
    ap.add_argument("--mode", default="read", choices=["read", "roundtrip", "write1867"])
    args = ap.parse_args()

    if args.mode in ("roundtrip", "write1867") and args.env.upper() != "TEST":
        print("REFUSING write modes outside Test env (they mutate the loan). Use --env Test.")
        sys.exit(2)

    reset_encompass_state()
    client = get_encompass_client(env=args.env)
    guid = resolve_guid(client, args.loan)
    print(f"Resolved GUID: {guid} (env={args.env})")

    if args.mode == "read":
        mode_read(client, guid)
    elif args.mode == "roundtrip":
        mode_roundtrip(client, guid)
    else:
        mode_write1867(client, guid)


if __name__ == "__main__":
    main()
