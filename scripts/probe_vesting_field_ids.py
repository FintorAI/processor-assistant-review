"""Read the vesting name/type/description fields straight off a live loan to
confirm which field ID holds the co-borrower vesting DESCRIPTION (1877) vs the
co-borrower vesting NAME (1873). Read-only.

Usage:
    venv/bin/python scripts/probe_vesting_field_ids.py --loan 2605968646 --env Prod
"""
from __future__ import annotations

import argparse
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

from encompass_client import get_encompass_client, reset_encompass_state
from shared.encompass_io import read_fields

LABELS = {
    "1868": "Borrower Vesting Name",
    "1871": "Borrower Vesting Type",
    "1872": "Borrower Vesting Description",
    "1873": "Co-Borrower Vesting Name",
    "1876": "Co-Borrower Vesting Type",
    "1877": "Co-Borrower Vesting Description",
    "33":   "Manner in Which Title Will Be Held",
    "1867": "Final Vesting (built)",
}


def resolve_guid(client, loan_number: str) -> str:
    if "-" in loan_number and len(loan_number) > 20:
        return loan_number
    results = client.search_loans_pipeline(loan_number=loan_number)
    if not results:
        raise RuntimeError(f"No loan found for {loan_number!r}")
    first = results[0]
    return (first if isinstance(first, str) else first.get("loanGuid") or first.get("id") or first.get("loanId")).replace("{", "").replace("}", "")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--loan", required=True)
    ap.add_argument("--env", default="Prod")
    args = ap.parse_args()

    reset_encompass_state()
    client = get_encompass_client(env=args.env)
    guid = resolve_guid(client, args.loan)
    print(f"Resolved GUID: {guid} (env={args.env})\n")

    fields = list(LABELS.keys())
    vals = read_fields(guid, fields)
    print(f"{'Field':10s} {'Label':38s} Value")
    print("-" * 90)
    for fid in fields:
        print(f"{fid:10s} {LABELS[fid]:38s} {vals.get(fid)!r}")


if __name__ == "__main__":
    main()
