#!/usr/bin/env python3
"""inspect_efolder.py — List all eFolder document buckets for a loan.

Uses EncompassConnect (copilotagent) to:
  1. Resolve a loan number to a GUID via search_loans_pipeline
  2. Fetch all eFolder documents via get_loan_documents(loan_guid)
  3. Print every bucket title + cross-check against required processor docs

Usage:
    python scripts/inspect_efolder.py <loan_number> [--env TEST|PROD]
    python scripts/inspect_efolder.py 2604964148 --env PROD
    python scripts/inspect_efolder.py 2604964148 --json        # raw JSON dump
    python scripts/inspect_efolder.py 2604964148 --buckets-only
"""

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from encompass_client import get_encompass_client


# ── Documents to cross-check (excluding items user said to skip) ─────────────
DOCS_TO_CHECK = [
    "1003 URLA",
    "1008 Transmittal Summary",
    "AUS Findings",
    "VOE",
    "Paystubs",
    "W-2s",
    "Tax Returns / Tax Summary",
    "Bank Statements",
    "Assets",
    "Purchase Agreement / Contract",
    "Credit Report",
    "Government ID (Driver's License)",
    "Estimated Settlement Statement (ESS)",
    "Flood Certificate",
    "Evidence of Hazard Insurance",
    "Title Report",
    "Loan Estimate (LE)",
    "Lock Confirmation",
    "Initial Disclosures / Borrower's Certification",
    "Closing Protection Letter (CPL)",
    "Escrow Wire",
]

# Known bucket names from document_type_registry.py
KNOWN_BUCKETS: dict[str, list[str]] = {
    "1003 URLA":                                ["1003", "URLA", "Loan Application"],
    "1008 Transmittal Summary":                 ["1008 Transmittal Summary", "1008", "Transmittal Summary"],
    "AUS Findings":                             ["Underwriting", "DU Findings", "LP Findings"],
    "Purchase Agreement / Contract":            ["Purchase Agreement", "Purchase Contract"],
    "Credit Report":                            ["Credit Report", "Credit"],
    "Government ID (Driver's License)":         ["ID Customer Identification Documentation", "ID Customer Identification"],
    "Estimated Settlement Statement (ESS)":     ["Estimated Settlement Statement", "ESS", "Settlement Statement"],
    "Flood Certificate":                        ["Flood Certificate", "Flood Certification", "Flood Determination"],
    "Evidence of Hazard Insurance":             ["Evidence of Hazard Insurance", "HOI"],
    "Title Report":                             ["Title Report", "Prelim", "Preliminary Title"],
    "Loan Estimate (LE)":                       ["Loan Estimate", "LE", "Initial LE"],
    "Closing Protection Letter (CPL)":          ["Closing Protection Letter (CPL)", "CPL"],
    "Tax Returns / Tax Summary":                ["Tax Summary", "Property Tax"],
}


# ── Step 1: resolve loan number → GUID ───────────────────────────────────────

def resolve_loan_guid(loan_number: str, env: str) -> str:
    """Use search_loans_pipeline to get the GUID for a loan number."""
    client = get_encompass_client(env=env, use_cache=False)
    results = client.search_loans_pipeline(loan_number=loan_number)
    if not results:
        print(f"ERROR: No loan found with number {loan_number} in {env} environment.", file=sys.stderr)
        sys.exit(1)
    # The GUID is usually in 'loanGuid' or 'id'
    guid = (
        results[0].get("loanGuid")
        or results[0].get("id")
        or results[0].get("loan_id")
    )
    if not guid:
        print(f"ERROR: Could not extract GUID from pipeline result: {results[0]}", file=sys.stderr)
        sys.exit(1)
    return guid


# ── Step 2: fetch eFolder documents ──────────────────────────────────────────

def fetch_efolder_documents(loan_guid: str, env: str) -> list[dict]:
    """Call get_loan_documents(loan_guid) and return the list of document dicts."""
    client = get_encompass_client(env=env, use_cache=False)
    return client.get_loan_documents(loan_guid)


# ── Output helpers ────────────────────────────────────────────────────────────

def _match(title: str, known: list[str]) -> bool:
    t = title.strip().lower()
    return any(k.strip().lower() in t or t in k.strip().lower() for k in known)


def _doc_title(d: dict) -> str:
    return str(d.get("title") or d.get("description") or d.get("name") or "")


def print_results(loan_number: str, loan_guid: str, env: str, docs: list[dict]) -> None:
    sorted_docs = sorted(
        [d for d in docs if _doc_title(d)],
        key=lambda d: _doc_title(d).lower(),
    )

    print(f"\nLoan {loan_number}  GUID={loan_guid[:8]}...  [{env.upper()}]")
    print("=" * 80)
    print(f"  ALL eFOLDER BUCKETS ({len(sorted_docs)} buckets)")
    print("=" * 80)
    for d in sorted_docs:
        title = _doc_title(d)
        doc_id = d.get("id", "")
        status = d.get("documentStatus", "")
        attachments = d.get("attachments") or []
        active_atts = [a for a in attachments if a.get("isActive", True)]

        status_str = f"  [{status}]" if status else ""
        print(f"\n  • {title}{status_str}")
        print(f"    bucket_id : {doc_id}")
        if active_atts:
            for a in active_atts:
                att_id = a.get("entityId", "")
                att_name = a.get("entityName", "")
                size = a.get("fileSize", "")
                size_str = f"  {size//1024}KB" if size else ""
                print(f"    attachment: {att_id}  |  {att_name}{size_str}")
        else:
            print(f"    attachment: (none)")

    titles = [_doc_title(d) for d in sorted_docs]

    print()
    print("=" * 80)
    print("  CROSS-CHECK: Required Processor Submission Documents")
    print("=" * 80)
    print(f"  {'Document':<48}  {'Status':<9}  Matched bucket")
    print(f"  {'-'*48}  {'-'*9}  {'-'*30}")

    for doc in DOCS_TO_CHECK:
        known = KNOWN_BUCKETS.get(doc, [])
        matched = [t for t in titles if _match(t, known)] if known else []

        if not known:
            chk_status = "NO REF"
            detail = "(no known bucket — check raw list above)"
        elif matched:
            chk_status = "FOUND"
            detail = " | ".join(matched)
        else:
            chk_status = "MISSING"
            detail = f"expected one of: {known}"

        marker = "✓" if chk_status == "FOUND" else ("?" if chk_status == "NO REF" else "✗")
        print(f"  {marker} {doc:<47}  {chk_status:<9}  {detail}")

    print()
    no_ref = [d for d in DOCS_TO_CHECK if d not in KNOWN_BUCKETS]
    if no_ref:
        print(f"  Tip: {len(no_ref)} doc(s) marked NO REF have unknown bucket names.")
        print("  Match against the raw list above and update notes.txt +")
        print("  document_type_registry.py once confirmed.")
        print()


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="List eFolder buckets for a loan via Encompass get_loan_documents."
    )
    parser.add_argument("loan_number", help="Encompass loan number (e.g. 2604964148)")
    parser.add_argument(
        "--env", default="PROD", choices=["TEST", "PROD"],
        help="Encompass environment (default: PROD)",
    )
    parser.add_argument(
        "--json", dest="as_json", action="store_true",
        help="Dump raw document list as JSON instead of formatted output",
    )
    parser.add_argument(
        "--buckets-only", action="store_true",
        help="Print only the sorted bucket title list (one per line)",
    )
    args = parser.parse_args()

    print(f"\nResolving loan {args.loan_number} in [{args.env}]...")
    loan_guid = resolve_loan_guid(args.loan_number, args.env)
    print(f"GUID: {loan_guid}")

    print("Fetching eFolder documents...")
    docs = fetch_efolder_documents(loan_guid, args.env)

    if args.as_json:
        print(json.dumps(docs, indent=2))
        return

    if args.buckets_only:
        titles = sorted({_doc_title(d) for d in docs if _doc_title(d)})
        for t in titles:
            print(t)
        return

    print_results(args.loan_number, loan_guid, args.env, docs)


if __name__ == "__main__":
    main()
