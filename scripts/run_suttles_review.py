"""Fire a full `review` run for the Suttles FHA loan (2605968111) against the local
`langgraph dev` server and dump the final thread state for video5 fix verification.

Usage:
    cd /Users/naomi/Desktop/FINTOR/processor-assistant-review
    ./venv/bin/python scripts/run_suttles_review.py --loan 2605968111 --env Prod \
        --dump /tmp/suttles_state.json
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

# OCR of the Purchase Agreement "For informational purposes only" contacts panel
# (Outlook-xt2sxrcj (1).png). Listing Company = Seller's Agent; Selling Company =
# Buyer's Agent. Transcribed faithfully.
CONTACTS_IMAGE_OCR = """For informational purposes only:
Date of Ratification (see DEFINITIONS): 06/17/2026

Seller's Address:
Buyer's Address:
Seller's Email Address:
Buyer's Email Address: jcsuttles21@gmail.com
Seller's Telephone Number:
Buyer's Telephone Number:

Listing Company's Name and Address:
Grand Elm
20251 Century Blvd Ste 140  Germantown  MD 20874
Office # 240-669-2345
Agent Name Ed Kao
Agent Cell # 240-899-8489
Agent Email Address edkao@grandelm.com
Agent License # 622355-MD
Broker License # 622355-MD

Selling Company's Name and Address:
Keller Williams Preferred Properties.
1441 McCormick Drive #1020  Upper Marlboro MD 20774
Office # 240-737-5000
Agent Name Renee Amponsah
Agent Cell # 240-882-7896
Agent Email Address renee.amponsah@kw.com
Agent License # 654177
Broker License # 101380
"""

ALMAS_NOTES = """File Summary
Client Name: Jhonel & Jonathan Suttles
Property Address: 12859 Climbing Ivy Dr, Germantown, MD 20874
Closing Date: 7/15
AUS Findings: DU
Borrower(s) on Loan: Both
Borrower(s) on Title: Both
Loan Program: FHA MMP 5%

Employment & Income
VOE Contact Email: Jhonel - TWN
Jonathan:
PVOE - admin@starboardtransportation.com
VOE current - nfeinstone@arklineinc.com
Income Details:
Jhonel - Capital One | $6518.39 x 26 / 12 = $14,123.18 Base a month + Bonus 2 year avg $1,366.48
Jonathan - ARK Line | 2-year average $4611.46 base
Dependents: 2
Gabriella Suttles - 6 years old 9/18/19 Female
Aria Suttles - 4 years old 1/12/22 Female

Assets
Source of Assets (Blend, Bank Statements, etc.): Bank Statements
Available Funds: $1131.48
Funds for Settlement: gift funds and seller help
Gift Funds: $39k - gift letter in efolder already

Team Contacts
Title Company: Classic Settlements | Andrea Conte | aconte@settlements.com

Appraisal
Who will pay for the appraisal: Borrower

Additional Notes
Seller help is 3% (13,800)
HOA is 88 a month
Currently under the MMP income limit with your income calc
Clients are making up .5% in agent commission
Requested case # & SSN Verif - Please order appraisal
"""


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--loan", default="2605968111")
    ap.add_argument("--env", default="Prod")
    ap.add_argument("--dump", default="/tmp/suttles_state.json")
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
            {"ocr_status": "ok", "extracted_text": CONTACTS_IMAGE_OCR}
        ],
        "processor_name": "video5-verification-harness",
    }

    last_event = None
    async for chunk in client.runs.stream(
        thread_id,
        args.assistant,
        input=input_state,
        stream_mode="updates",
    ):
        last_event = chunk.event
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
    print(f"  flags={len(values.get('flags') or [])}", flush=True)
    print("RUN_COMPLETE", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
