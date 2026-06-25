"""Fire a full `review` workflow run against the local `langgraph dev` server and
dump the final thread state for gap verification.

Usage:
    cd /Users/naomi/Desktop/FINTOR/processor-assistant-review
    ./venv/bin/python scripts/run_local_review.py --loan 2605968646 --env Prod \
        --dump /tmp/review_state_2605968646.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from langgraph_sdk import get_client

LOCAL_URL = "http://127.0.0.1:2024"

# OCR of Roles & Contacts panel (image (14).png). Numbered-role-line format that
# review_file_contacts._merge_role_lines understands. Escrow email is visually
# truncated in the screenshot ("...group.c") — left faithful so the validation /
# ESS-bypass behaviour is exercised.
ROLES_IMAGE_OCR = """Roles & Contacts
**38** Lender
All Western Mortgage Inc. | Eric Gut | 702-850-1721 | ericnotif@allwestern.com
**39** Appraiser
Other
**40** Escrow Company
Grand Strand Law Group | Amy Tush | 843-492-5422 | amy@grandstrandlawgroup.c
**41** Title Insurance Company
**42** Buyer's Attorney
**43** Seller's Attorney
**44** Buyer's Agent
Sloan Realty Group | Scott P Ritter | 843-222-9265 | scottritter@SRGmail.com
**45** Seller's Agent
Home Placer LLC | Joe Scaturro | 843-798-8333 | joe@forturro.com
**46** Seller 1
 | Home Placer LLC
"""

ALMAS_NOTES = """File Summary
Client Name: Cassandra Matthews & James Ervin Martin
Property Address: 5548 Daffodil Dr, Conway, SC 29527
Closing Date: 7/23
AUS Findings: DU
Borrower(s) on Loan: Both
Borrower(s) on Title: Both
Loan Program: FHA Regular 30 year (Manufactured Home)

Employment & Income
VOE Contact Email: Blend & TWN (TBD APPROVED BY CARLI)
Cassie - 2024 average - $3037 a month
James - 2024 average - $4712 a month
Dependents: 0

Assets
Source of Assets: Bank and Asset statements
Available Funds: $18,235.36
Funds for Settlement: Proceeds from sale of home - $70k
Gift Funds: No

Team Contacts
Title Company: Grand Strand Law Group | Amy Tush | amy@grandstrandlawgroup.com

Appraisal
Who will pay for the appraisal: Borrower

Additional Notes
This is contingent upon the sale of her home she inherited from her deceased mother
(her sister is buying Cassie's portion of the house).
Putting down 10%.
South Carolina - no tax bill able to be found but title will provide.
Under Eric Gut's name since it's a South Carolina deal.
Manufactured home - TBD approved by Carli.
Have a debt checked off to be paid on page 3 (Flagship Credit is now Westlake).
"""


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--loan", required=True)
    ap.add_argument("--env", default="Prod")
    ap.add_argument("--dump", default="/tmp/review_state.json")
    ap.add_argument("--assistant", default="review")
    args = ap.parse_args()

    client = get_client(url=LOCAL_URL)

    thread = await client.threads.create()
    thread_id = thread["thread_id"]
    print(f"THREAD_ID={thread_id}", flush=True)

    input_state = {
        "loan_number": args.loan,
        "env": args.env,
        "almas_notes": ALMAS_NOTES,
        "almas_notes_images": [
            {"ocr_status": "ok", "extracted_text": ROLES_IMAGE_OCR}
        ],
        "processor_name": "verification-harness",
    }

    last_event = None
    async for chunk in client.runs.stream(
        thread_id,
        args.assistant,
        input=input_state,
        stream_mode="updates",
    ):
        last_event = chunk.event
        # Print compact progress: node names that produced updates
        if chunk.event == "updates" and isinstance(chunk.data, dict):
            for node, upd in chunk.data.items():
                cs = (upd or {}).get("current_step") if isinstance(upd, dict) else None
                marker = f" step={cs}" if cs else ""
                print(f"  [update] node={node}{marker}", flush=True)
        elif chunk.event in ("error", "interrupt"):
            print(f"  [{chunk.event}] {json.dumps(chunk.data, default=str)[:500]}", flush=True)

    print(f"STREAM_DONE last_event={last_event}", flush=True)

    state = await client.threads.get_state(thread_id)
    Path(args.dump).write_text(json.dumps(state, indent=2, default=str))
    values = state.get("values") or {}
    print(f"STATE_DUMPED={args.dump}", flush=True)
    print(f"  current_step={values.get('current_step')!r}", flush=True)
    print(f"  flags={len(values.get('flags') or [])}  vod_data={len(values.get('vod_data') or []) if isinstance(values.get('vod_data'), list) else values.get('vod_data')!r}", flush=True)
    print(f"  comms_actions={len(values.get('comms_actions') or [])}  field_writes={len(values.get('field_writes_ledger') or [])}", flush=True)
    print("RUN_COMPLETE", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
