"""Probe Encompass otherAssets (EarnestMoney) + field 186 for a loan.

Usage:
    cd /Users/naomi/Desktop/FINTOR/processor-assistant-review
    python3.11 scripts/probe_emd.py --loan 2604964148 --env Prod
"""
from __future__ import annotations

import argparse
import json
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

from encompass_client import get_encompass_client, get_other_assets
from shared.encompass_io import read_fields


def main() -> None:
    parser = argparse.ArgumentParser(description="Probe EMD data for a loan")
    parser.add_argument("--loan", required=True, help="Loan number (e.g. 2604964148)")
    parser.add_argument("--env", default="Prod", help="Prod or Test")
    args = parser.parse_args()

    state = {"env": args.env}
    client = get_encompass_client(state=state)

    # ── 1. Resolve loan number → GUID ────────────────────────────────────────
    print(f"\nSearching for loan {args.loan} in {args.env}...")
    results = client.search_loans_pipeline(loan_number=args.loan)
    if not results:
        print("ERROR: Loan not found in pipeline.")
        sys.exit(1)

    loan_id = (results[0].get("loanGuid") or results[0].get("loanId") or results[0].get("id", "")).replace("{", "").replace("}", "")
    borrower = results[0].get("fields", {}).get("Loan.BorrowerName", "(unknown)")
    print(f"Found: loan_id={loan_id}  borrower={borrower}")

    # ── 2. Read field 186 (EMD Amount — flat field) ───────────────────────────
    print("\n--- Field 186 (LOS flat field) ---")
    try:
        field_val = read_fields(loan_id, ["186"], state=state)
        print(f"  field 186 = {field_val.get('186')!r}")
    except Exception as e:
        print(f"  ERROR reading field 186: {e}")

    # ── 3. Fetch otherAssets (API collection) ─────────────────────────────────
    print("\n--- otherAssets API (all rows) ---")
    try:
        assets = get_other_assets(loan_id, state=state)
        if not assets:
            print("  (empty — no otherAsset rows)")
        else:
            for row in assets:
                marker = " ← EarnestMoney" if row.get("assetType") == "EarnestMoney" else ""
                print(f"  {json.dumps(row)}{marker}")
    except LookupError as e:
        print(f"  LookupError: {e}")
    except Exception as e:
        print(f"  ERROR: {e}")

    # ── 4. Summary ────────────────────────────────────────────────────────────
    print("\n--- Summary ---")
    try:
        emd_row = next((r for r in assets if r.get("assetType") == "EarnestMoney"), None)
        if emd_row:
            print(f"  otherAssets EMD (cashOrMarketValue): {emd_row.get('cashOrMarketValue')!r}")
        else:
            print("  otherAssets: no EarnestMoney row found")
    except Exception:
        pass


if __name__ == "__main__":
    main()
