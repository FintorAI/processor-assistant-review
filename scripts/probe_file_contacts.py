"""
Probe Encompass File Contacts + Blend IDs for a loan, to verify the
processor-comms role lookups resolve BEFORE wiring action items.

Why this lives in processor-assistant-review: the comms repo has no venv, but
this repo does and already has a working EncompassConnect client + creds.

It re-implements the comms `find_contact_by_role` matcher verbatim (the comms
function is pure Python; copied here to avoid importing the comms package) so we
can see whether the role substrings actually match Encompass's enum contactType
values (e.g. ESCROW_COMPANY / SELLERS_AGENT / LOAN_OFFICER).

Usage:
    cd /Users/naomi/Desktop/FINTOR/processor-assistant-review
    source venv/bin/activate
    python scripts/probe_file_contacts.py --loan 2604964148 --env Prod
"""
from __future__ import annotations

import argparse
import json
import sys
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

from encompass_client import get_encompass_client, reset_encompass_state


# ── verbatim copy of processor-assistant-communications/graphs/processor_shared.py:find_contact_by_role (fixed) ──
_ROLE_ALIASES = {
    "escrow":         ["ESCROW_COMPANY", "SETTLEMENT_AGENT", "TITLE_COMPANY"],
    "title":          ["TITLE_COMPANY", "SETTLEMENT_AGENT", "ESCROW_COMPANY"],
    "loan officer":   ["LOAN_OFFICER"],
    "loan processor": ["LOAN_PROCESSOR"],
    "emd":            ["BUYERS_AGENT", "SELLERS_AGENT"],
    "buyers agent":   ["BUYERS_AGENT", "SELLERS_AGENT"],
    "sellers agent":  ["SELLERS_AGENT", "BUYERS_AGENT"],
}


def _norm_role(s: Optional[str]) -> str:
    return (s or "").strip().lower().replace("_", " ")


def find_contact_by_role(file_contacts: List[Dict[str, Any]], role: str) -> Optional[Dict[str, Any]]:
    if not file_contacts:
        return None
    role_n = _norm_role(role)
    by_type = {_norm_role(c.get("contactType") or c.get("type")): c for c in file_contacts}
    for enum_val in _ROLE_ALIASES.get(role_n, []):
        hit = by_type.get(_norm_role(enum_val))
        if hit:
            return hit
    if role_n:
        for norm_type, c in by_type.items():
            if role_n in norm_type:
                return c
    return None


# Roles each comms graph looks up (graph -> role string it passes today)
ROLE_LOOKUPS = {
    "title_order -> escrow (To)": "escrow",
    "title_order/lock_desk -> loan officer (CC)": "loan officer",
    "emd -> buyer's agent (To, new alias)": "emd",
    "emd -> selling real estate (legacy)": "selling real estate",
    "emd -> realtor (legacy fallback)": "realtor",
    "title (misc)": "title",
}

CUSTOM_FIELDS = ["CX.BLEND.LOANID", "CX.BLEND.B1.ID", "CX.BLEND.C1.ID",
                 "1041", "CX.PROCESSOR.NAME", "2400", "11", "12", "14", "15"]


def resolve_guid(client, loan_number: str) -> str:
    if "-" in loan_number and len(loan_number) > 20:
        return loan_number  # already a GUID
    results = client.search_loans_pipeline(loan_number=loan_number)
    if not results:
        raise RuntimeError(f"No loan found for loan_number={loan_number!r}")
    first = results[0]
    guid = first if isinstance(first, str) else first.get("loanGuid", first.get("id"))
    if not guid:
        raise RuntimeError(f"Pipeline returned no GUID for {loan_number!r}: {first!r}")
    return guid


def get_contacts(client, guid: str) -> List[Dict[str, Any]]:
    url = f"{client.api_base_url}/encompass/v3/loans/{guid}/contacts"
    headers = {"accept": "application/json", "Authorization": f"Bearer {client.access_token}"}
    r = requests.get(url, headers=headers, timeout=30)
    if r.status_code == 401:
        client.refresh_token()
        headers["Authorization"] = f"Bearer {client.access_token}"
        r = requests.get(url, headers=headers, timeout=30)
    r.raise_for_status()
    body = r.json()
    return body if isinstance(body, list) else []


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--loan", required=True, help="Encompass loan number or GUID")
    ap.add_argument("--env", default="Prod", help="Prod | Test")
    args = ap.parse_args()

    print(f"\n=== Probe: loan={args.loan} env={args.env} ===")
    reset_encompass_state()
    client = get_encompass_client(env=args.env)

    guid = resolve_guid(client, args.loan)
    print(f"Resolved GUID: {guid}\n")

    contacts = get_contacts(client, guid)
    print(f"--- File Contacts ({len(contacts)}) ---")
    for c in contacts:
        ctype = c.get("contactType") or c.get("type")
        name = c.get("name") or c.get("contactName")
        print(f"  contactType={str(ctype)!r:32} name={str(name)!r:30} email={str(c.get('email'))!r}")

    print("\n--- Role lookups (comms find_contact_by_role) ---")
    for label, role in ROLE_LOOKUPS.items():
        hit = find_contact_by_role(contacts, role)
        if hit:
            print(f"  MATCH    [{label}]  role={role!r} -> contactType={hit.get('contactType') or hit.get('type')!r} email={hit.get('email')!r}")
        else:
            print(f"  NO MATCH [{label}]  role={role!r}")

    print("\n--- Custom fields (Blend IDs, property type, lock, address) ---")
    try:
        cf = client.get_field(guid, CUSTOM_FIELDS)
        print(json.dumps(cf, indent=2, default=str))
    except Exception as e:
        print(f"  get_field failed: {e}")


if __name__ == "__main__":
    main()
