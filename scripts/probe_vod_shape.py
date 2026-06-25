"""One-off: dump raw VOD object shape for a loan to confirm URLA-2020 vs legacy schema.

Usage:
    cd /Users/naomi/Desktop/FINTOR/processor-assistant-review
    python3.11 scripts/probe_vod_shape.py --loan 2605968646 --env Prod
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

from encompass_client import get_encompass_client, get_vods


def main() -> None:
    parser = argparse.ArgumentParser(description="Probe VOD shape for a loan")
    parser.add_argument("--loan", required=True)
    parser.add_argument("--env", default="Prod")
    args = parser.parse_args()

    state = {"env": args.env}
    client = get_encompass_client(state=state)

    results = client.search_loans_pipeline(loan_number=args.loan)
    if not results:
        print("ERROR: Loan not found.")
        sys.exit(1)
    loan_id = (results[0].get("loanGuid") or results[0].get("loanId") or results[0].get("id", "")).replace("{", "").replace("}", "")
    print(f"loan_id={loan_id}")

    vods = get_vods(loan_id, state=state)
    print("count:", len(vods))
    for i, v in enumerate(vods):
        print(f"\n=== VOD {i} top-level keys ===")
        print(sorted(v.keys()))
        print(json.dumps(v, indent=2, default=str)[:4000])


if __name__ == "__main__":
    main()
