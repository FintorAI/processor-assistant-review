#!/usr/bin/env python3
"""Validate Gap B: download the ESS-bucket attachment (a "pre-CD") and run it
through LandingAI with the ESS schema directly, bypassing the catchingDoc
finder/LLM bucket classification. Prints the page-5 Contact Information fields.
"""
import json
import sys
from pathlib import Path

LG = Path("/Users/naomi/Desktop/FINTOR/LG-docsOrch")
sys.path.insert(0, str(LG))
sys.path.insert(0, str(LG / "devTool"))
sys.path.insert(0, str(LG / "devTool" / "catchingDoc"))

from dotenv import load_dotenv
load_dotenv(LG / ".env")

import os
import requests
from extract_local import (
    find_loan_guid,
    list_efolder_documents,
    download_attachment,
    fetch_extraction_schema,
    LANDINGAI_API_URL,
)
from encompass_client import get_encompass_client


def landingai_extract(pdf_bytes, schema, filename="ess.pdf"):
    """LandingAI call that surfaces the raw response on failure."""
    key = os.getenv("LANDINGAI_API_KEY", "")
    headers = {"Authorization": f"Basic {key}"}
    files = {"pdf": (filename, pdf_bytes, "application/pdf")}
    data = {"fields_schema": json.dumps(schema)}
    resp = requests.post(LANDINGAI_API_URL, files=files, data=data, headers=headers, timeout=300)
    resp.raise_for_status()
    result = resp.json()
    extracted = (result.get("data") or {}).get("extracted_schema")
    if extracted is None:
        print("RAW LandingAI response (truncated):")
        print(json.dumps(result, indent=2)[:2000])
        return {}
    return extracted

LOAN = sys.argv[1] if len(sys.argv) > 1 else "2605968646"
ENV = sys.argv[2] if len(sys.argv) > 2 else "prod"
BUCKET = "Estimated Settlement Statement"

client = get_encompass_client(state={"env": ENV.upper()})
guid = find_loan_guid(client, LOAN)
print(f"loan={LOAN} env={ENV} guid={guid}")

docs = list_efolder_documents(client, guid)
att_id = None
for d in docs:
    if (d.get("title") or "").strip().lower() == BUCKET.lower():
        atts = d.get("attachments", [])
        if atts:
            att_id = atts[0].get("entityId")
            print(f"bucket={d.get('title')!r} attachment={atts[0].get('entityName')!r} id={att_id}")
        break
if not att_id:
    print("No attachment found in ESS bucket"); sys.exit(1)

pdf = download_attachment(client, guid, att_id)
print(f"downloaded PDF bytes={len(pdf):,}")

# Use the FULL ESS schema (contact table). The /efolder/schemas/ESS alias returns
# a stale 9-field schema, so load the rich one exported from catchingDoc instead.
rich = json.load(open("/tmp/ess_schema.json"))
extraction = rich["extraction_schema"]
schema = {"type": "object", "properties": extraction["properties"], "required": extraction.get("required", [])}
print(f"schema props={len(schema['properties'])} (using full ESS schema)")

print("Calling LandingAI (30-80s)...")
fields = landingai_extract(pdf, schema, filename="ess.pdf")

def val(k):
    v = fields.get(k)
    return v.get("value") if isinstance(v, dict) else v

contact = {k: val(k) for k in fields if k.startswith("contact_") and val(k) not in (None, "", "null")}
print(f"\n=== non-empty contact_* fields: {len(contact)} ===")
print(json.dumps(contact, indent=2))

# group by column for readability
print("\n=== grouped ===")
for col in ("settlement_agent", "real_estate_broker_buyer", "real_estate_broker_seller", "lender", "mortgage_broker"):
    grp = {k.replace(f"contact_{col}_", ""): v for k, v in contact.items() if k.startswith(f"contact_{col}_")}
    if grp:
        print(f"\n[{col}]")
        for k, v in grp.items():
            print(f"  {k}: {v}")
