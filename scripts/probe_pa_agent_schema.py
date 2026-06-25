#!/usr/bin/env python3
"""Gap C experiment harness — iterate Purchase Agreement agent-schema descriptions
against LandingAI to improve buyer's/seller's agent extraction (esp. the SC layout).

Downloads the PA attachment once (cached to /tmp), then runs LandingAI with a chosen
schema VARIANT and prints the buyers_agent / sellers_agent objects so descriptions can
be tuned quickly. Usage:

    python scripts/probe_pa_agent_schema.py <variant> [loan] [env]

variant: baseline | sc_aware   (see VARIANTS below)
"""
import os, sys, json, requests, urllib.parse
from pathlib import Path

sys.path.insert(0, ".")
sys.path.insert(0, "output")

def _load_env(p):
    if not os.path.exists(p):
        return
    for line in open(p):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

_load_env(".env")
_load_env("/Users/naomi/Desktop/FINTOR/LG-docsOrch/.env")

import shared.ess_contact_bypass as b
from encompass_client import get_encompass_client

VARIANT = sys.argv[1] if len(sys.argv) > 1 else "sc_aware"
LOAN = sys.argv[2] if len(sys.argv) > 2 else "2605968646"
ENV = sys.argv[3] if len(sys.argv) > 3 else "prod"
GUID = "a5d4947c-f364-48a1-87d1-014f48e9bfb0" if LOAN == "2605968646" else None
PA_BUCKETS = {"purchase agreement", "purchase contract", "sales contract", "contract"}


# ── FLAT agent property variants (LandingAI rejects nested objects → 422) ───
def _S(d):
    return {"type": ["string", "null"], "description": d}


def _agent_flat(side: str, variant: str) -> dict:
    """Flat keys for one side. side = 'buyer' | 'seller' (lowercase)."""
    S = side.capitalize()
    if variant == "baseline":
        return {
            f"{side}_agent_company": _S(f"{S}'s agent brokerage company name"),
            f"{side}_agent_name": _S(f"{S}'s agent name"),
            f"{side}_agent_address": _S(f"{S}'s agent office address"),
            f"{side}_agent_phone": _S(f"{S}'s agent phone number"),
            f"{side}_agent_email": _S(f"{S}'s agent email address"),
            f"{side}_agent_license": _S(f"{S}'s agent license number"),
            f"{side}_agent_mls_id": _S(f"{S}'s agent MLS ID"),
        }
    # sc_aware
    return {
        f"{side}_agent_name": _S(
            f"{S}'s agent full PERSON name, printed on the '{S}'s Agent Name/License #' line in "
            f"the signature section of the contract (e.g. 'Joe M Scaturro'). This is the real-estate "
            f"agent, NOT the {side} party/owner and NOT the brokerage company."),
        f"{side}_agent_license": _S(
            f"{S}'s agent INDIVIDUAL real-estate license number, printed right next to the agent "
            f"name on the '{S}'s Agent Name/License #' line (e.g. '121177'). Not the LLR Office Code."),
        f"{side}_agent_office_code": _S(
            f"The 'LLR Office Code' value in the {S}'s agent block (e.g. '27547'). This is the "
            f"brokerage/office (COMPANY) license identifier on South Carolina contracts, distinct "
            f"from the agent's individual license number."),
        f"{side}_agent_company": _S(
            f"{S}'s brokerage/firm company name (e.g. 'Sloan Realty Group', 'Keller Williams'). "
            f"Prefer the registered brokerage name; if only a team name like 'Blake Sloan Team' is "
            f"shown, return that. Do NOT return the {side} party/owner name as the company."),
        f"{side}_agent_email": _S(
            f"{S}'s agent email from the 'Notice Email/Address (Where {S} wants...)' block "
            f"(e.g. 'joe@forturro.com'). Remove stray spaces caused by line wraps."),
        f"{side}_agent_phone": _S(
            f"{S}'s agent phone, labeled 'Phone:' on the '{S}'s Agent Name/License #' row "
            f"(e.g. '(843)798-8333')."),
        f"{side}_agent_address": _S(
            f"{S}'s agent office street address (e.g. '3120 Waccamaw Blvd Ste C, Myrtle Beach, SC "
            f"29579'). May appear in the {S} Notice Email/Address block OR on a separate agent/"
            f"brokerage info page elsewhere in the contract."),
        f"{side}_agent_mls_id": _S(
            "MLS ID if the contract shows one (common on MD/other-state forms). On SC contracts "
            "this is usually absent — the office identifier is the LLR Office Code instead."),
    }


def build_schema(variant: str) -> dict:
    props = {}
    props.update(_agent_flat("buyer", variant))
    props.update(_agent_flat("seller", variant))
    return {"type": "object", "properties": props, "required": []}


def get_pdf() -> bytes:
    cache = Path(f"/tmp/pa_{LOAN}.pdf")
    if cache.exists():
        return cache.read_bytes()
    client = get_encompass_client(state={"env": ENV.upper()})
    guid = GUID
    if not guid:
        raise SystemExit("need GUID for non-default loan")
    url = f"{client.api_base_url}/encompass/v3/loans/{guid}/documents"
    docs = requests.get(url, headers={"Authorization": f"Bearer {client.access_token}",
                                      "Accept": "application/json"}, timeout=20).json()
    att = None
    for d in docs:
        if (d.get("title") or "").strip().lower() in PA_BUCKETS:
            a = d.get("attachments") or []
            if a:
                att = a[0].get("entityId")
            break
    pdf = b._download_attachment(client, guid, att)
    cache.write_bytes(pdf)
    return pdf


def run(variant: str):
    pdf = get_pdf()
    schema = build_schema(variant)
    key = os.getenv("LANDINGAI_API_KEY", "")
    r = requests.post(b._LANDINGAI_API_URL,
                      files={"pdf": ("pa.pdf", pdf, "application/pdf")},
                      data={"fields_schema": json.dumps(schema)},
                      headers={"Authorization": f"Basic {key}"}, timeout=300)
    j = r.json()
    ext = (j.get("data") or {}).get("extracted_schema") or {}
    flat = {k: (v.get("value") if isinstance(v, dict) and "value" in v else v) for k, v in ext.items()}
    print(f"\n===== VARIANT={variant} | HTTP {r.status_code} | err={j.get('extraction_error')} =====")
    for side in ("buyer", "seller"):
        print(f"\n[{side}_agent]")
        for k, v in flat.items():
            if k.startswith(f"{side}_agent_") and v not in (None, "", "null"):
                print(f"  {k.replace(f'{side}_agent_', '')}: {v}")


if __name__ == "__main__":
    print(f"loan={LOAN} env={ENV} variant={VARIANT}")
    run(VARIANT)
