"""write_graphs — dashboard-facing LangGraph endpoints for manual Encompass writes.

Two single-node, LLM-free graphs registered in langgraph.json:

  write_los_fields       — flat field overrides (the dashboard Field Writes tab).
                           Writes through write_fields_resilient (same audited
                           path as agent tools), returns per-field results and
                           a field_writes_ledger in the same shape the review
                           agent emits (source="dashboard-override").

  write_los_collections  — collection edits (VOD / VOL rows, file contacts).
                           Thin wrapper over the verified collection PATCH
                           endpoints; raw Encompass schema keys pass through so
                           the dashboard can render/edit exactly what GET
                           returns.

Input contract (both graphs): {"loan_number": "...", "env": "Prod"|"Test", ...}
plus the payload documented on each state class. ``loan_number`` may also be a
loan GUID — resolution is skipped when it already looks like one.

These are NOT workflow substep tools — they never run inside the review agent.
They exist so the dashboard can trigger writes with the same audit trail.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Annotated, NotRequired

import requests
from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict

logger = logging.getLogger(__name__)


def _last_value(existing, new):
    return new if new is not None else existing


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resolve_loan(loan_number: str, env: str) -> tuple[str | None, str | None]:
    """Resolve a loan number (or pass through a GUID) → (loan_guid, error)."""
    from encompass_client import get_encompass_client
    from shared.encompass_io import is_guid, sanitize_guid

    state = {"env": env}
    candidate = sanitize_guid(str(loan_number or "").strip())
    if is_guid(candidate):
        return candidate, None

    client = get_encompass_client(state=state)
    results = client.search_loans_pipeline(loan_number=str(loan_number).strip())
    if not results:
        return None, f"No loan found for {loan_number!r} in {env}"
    raw = results[0] if isinstance(results[0], str) else results[0].get("loanGuid", results[0].get("id"))
    guid = sanitize_guid(str(raw)) if raw else ""
    if not is_guid(guid):
        return None, f"Encompass returned non-GUID identifier {raw!r} for {loan_number!r}"
    return guid, None


# ═══════════════════════════════════════════════════════════════════════
# write_los_fields — flat field overrides
# ═══════════════════════════════════════════════════════════════════════

class FieldWriteState(TypedDict):
    """State for the write_los_fields graph.

    Input:
        loan_number: loan number or GUID
        env: "Prod" | "Test"
        updates: {"field_id": value, ...} — values as fieldWriter expects them
                 (checkbox "X"/"", YN "True"/"False", dates ISO yyyy-MM-dd)
        source: optional audit label (e.g. dashboard user email)
    Output:
        results: {written: {...}, failed: {field_id: reason}, error?: str}
        field_writes_ledger: audit rows (same shape as the review agent's ledger)
    """
    loan_number: str
    env: str
    updates: dict
    source: NotRequired[str]
    loan_id: Annotated[NotRequired[str], _last_value]
    results: Annotated[NotRequired[dict], _last_value]
    field_writes_ledger: Annotated[NotRequired[list], _last_value]


def _write_fields_node(state: FieldWriteState) -> dict:
    from shared.encompass_io import write_fields_resilient

    updates = {
        str(fid): val
        for fid, val in (state.get("updates") or {}).items()
        if str(fid).strip()
    }
    source = state.get("source") or "dashboard-override"
    if not updates:
        return {"results": {"written": {}, "failed": {}, "error": "No updates provided"}}

    loan_id, err = _resolve_loan(state.get("loan_number", ""), state.get("env", "Prod"))
    if err:
        return {"results": {"written": {}, "failed": {}, "error": err}}

    io_state = {"env": state.get("env", "Prod")}
    try:
        written, bad_fields = write_fields_resilient(loan_id, updates, state=io_state)
    except Exception as exc:  # noqa: BLE001 — surface, don't crash the endpoint
        from shared.encompass_io import humanize_write_error
        return {
            "loan_id": loan_id,
            "results": {"written": {}, "failed": {}, "error": humanize_write_error(str(exc))},
        }

    ledger = [
        {
            "field_id": fid,
            "value": val,
            "substep": "manual-override",
            "source": source,
            "dry_run": False,
            "timestamp": _now(),
        }
        for fid, val in written.items()
    ]
    logger.info(
        f"[WRITE_LOS_FIELDS] loan {loan_id[:8]}: wrote {sorted(written)}; "
        f"rejected {sorted(bad_fields)} (source={source})"
    )
    return {
        "loan_id": loan_id,
        "results": {"written": written, "failed": bad_fields},
        "field_writes_ledger": ledger,
    }


_fields_builder = StateGraph(FieldWriteState)
_fields_builder.add_node("write_fields", _write_fields_node)
_fields_builder.add_edge(START, "write_fields")
_fields_builder.add_edge("write_fields", END)
field_write_graph = _fields_builder.compile()
field_write_graph.name = "write_los_fields"


# ═══════════════════════════════════════════════════════════════════════
# write_los_collections — VOD / VOL / contacts edits
# ═══════════════════════════════════════════════════════════════════════

class CollectionWriteState(TypedDict):
    """State for the write_los_collections graph.

    Input (all payload lists optional). Two shapes are accepted per list:

    Normalised overrides (what the dashboard collections editor sends —
    rows in the thread state come from read_vods/read_vols, so edits arrive
    with those snake_case keys, NOT raw v3 keys):
        vods:     [{"vod_id": "...",
                    "account_number": "<original — locates the item>",
                    "updates": {institution_name?, borrower_type?,
                                account_type?, account_holder?,
                                account_number?, balance?}}]
        vols:     [{"vol_id": "...",
                    "updates": {holder_name?, liability_type?, owner?,
                                account_number?, monthly_payment?,
                                unpaid_balance?, credit_limit?,
                                exclude_monthly_pay?, payoff_included?,
                                remaining_months?}}]

    Raw v3 passthrough (scripts / power users):
        vods:     [{"id": vodId, ...raw VOD fields (e.g. holderName, items[])}]
        vols:     [{"id": volId, ...raw VOL fields (e.g. unpaidBalanceAmount)}]

    Contacts are always raw v3 rows (the file_contacts channel already holds
    the GET shape):
        contacts: [{"contactType": "...", ...}]
                  → PATCH /loans/{id}/contacts  (upsert by contactType)
    Output:
        results: {vods: {...}, vols: [...], contacts: {...}, error?: str}
    """
    loan_number: str
    env: str
    vods: NotRequired[list]
    vols: NotRequired[list]
    contacts: NotRequired[list]
    source: NotRequired[str]
    loan_id: Annotated[NotRequired[str], _last_value]
    results: Annotated[NotRequired[dict], _last_value]


# Normalised read_vols keys → v3 VOL PATCH fields. Values are written as-is
# (the dashboard preserves the original primitive type when editing a cell).
_VOL_KEY_TO_V3 = {
    "holder_name": "holderName",
    "liability_type": "liabilityType",
    "owner": "owner",
    "account_number": "accountIdentifier",
    "monthly_payment": "monthlyPaymentAmount",
    "unpaid_balance": "unpaidBalanceAmount",
    "credit_limit": "creditLimit",
    "exclude_monthly_pay": "excludedFromTotalMonthlyPaymentIndicator",
    "payoff_included": "payoffIncludedIndicator",
    "remaining_months": "remainingTermMonths",
}


def _is_masked(value) -> bool:
    """Account numbers read back masked ("****2286") must never be written."""
    return "*" in str(value or "")


def _digits_last4(value) -> str:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return digits[-4:]


def _vol_override_to_v3(entry: dict) -> tuple[dict | None, str | None]:
    """Translate a normalised {"vol_id", "updates"} entry into a raw PATCH row."""
    updates = entry.get("updates") or {}
    body: dict = {}
    for key, value in updates.items():
        api_field = _VOL_KEY_TO_V3.get(key)
        if not api_field:
            continue  # unknown / non-writable key — ignore silently
        if key == "account_number" and _is_masked(value):
            continue
        body[api_field] = value
    if not body:
        return None, "no writable fields in updates"
    return {"id": entry["vol_id"], **body}, None


def _apply_vod_overrides(
    client, io_state: dict, loan_id: str, application_id: str, entries: list
) -> dict:
    """Apply normalised per-account VOD overrides via one collection PATCH.

    read_vods flattens each VOD object into one row per account item, so an
    override carries ``vod_id`` (the object) + the ORIGINAL ``account_number``
    (locates the item within it). The v3 collection PATCH replaces the items
    array wholesale, so the current VOD is fetched first and the full array is
    resent with only the targeted edits applied. Both the URLA-2020 (items[])
    and legacy (accountInformation[]) schemas are handled.
    """
    from encompass_client import _VOD_ACCOUNT_TYPE_ENUM, get_vods

    try:
        raw_vods = get_vods(loan_id, application_id=application_id, state=io_state)
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "error": f"could not fetch VODs: {exc}"}
    by_id = {v.get("id", ""): v for v in raw_vods}

    updated: list[dict] = []
    skipped: list[dict] = []
    patch_by_vod: dict[str, dict] = {}

    for entry in entries:
        vod_id = entry.get("vod_id", "")
        updates = entry.get("updates") or {}
        raw = by_id.get(vod_id)
        if not raw:
            skipped.append({"vod_id": vod_id, "reason": "VOD not found on loan"})
            continue

        urla_items = raw.get("items") or []
        legacy_items = raw.get("accountInformation") or []
        is_urla = bool(urla_items) or not legacy_items
        items = urla_items if is_urla else legacy_items
        patch = patch_by_vod.setdefault(vod_id, {"id": vod_id})
        changed_fields: list[str] = []

        if "institution_name" in updates:
            field = "holderName" if is_urla else "depInstitution"
            patch[field] = str(updates["institution_name"]).strip()
            changed_fields.append("institution_name")
        if "borrower_type" in updates:
            field = "owner" if is_urla else "for"
            patch[field] = updates["borrower_type"]
            changed_fields.append("borrower_type")

        item_updates = {
            k: updates[k]
            for k in ("account_type", "account_holder", "account_number", "balance")
            if k in updates
        }
        if item_updates:
            want_last4 = _digits_last4(entry.get("account_number"))
            id_field = "accountIdentifier" if is_urla else "accountNumber"
            target = None
            if want_last4:
                for item in items:
                    if _digits_last4(item.get(id_field)) == want_last4:
                        target = item
                        break
            if target is None and len(items) == 1:
                target = items[0]
            if target is None:
                skipped.append({
                    "vod_id": vod_id,
                    "reason": "could not locate account item "
                    f"(account_number …{want_last4 or '????'})",
                })
            else:
                if "account_type" in item_updates:
                    raw_type = str(item_updates["account_type"])
                    enum_type = _VOD_ACCOUNT_TYPE_ENUM.get(
                        raw_type.replace(" ", "").lower()
                    )
                    target["type" if is_urla else "accountType"] = enum_type or raw_type
                    changed_fields.append("account_type")
                if "account_holder" in item_updates:
                    field = "depositoryAccountName" if is_urla else "accountInNameOf"
                    target[field] = str(item_updates["account_holder"]).strip()
                    changed_fields.append("account_holder")
                if "account_number" in item_updates and not _is_masked(
                    item_updates["account_number"]
                ):
                    target[id_field] = str(item_updates["account_number"]).strip()
                    changed_fields.append("account_number")
                if "balance" in item_updates:
                    field = (
                        "urla2020CashOrMarketValueAmount"
                        if is_urla
                        else "cashOrMarketValue"
                    )
                    target[field] = item_updates["balance"]
                    changed_fields.append("balance")
                # PATCH replaces arrays wholesale — resend every sibling item.
                patch["items" if is_urla else "accountInformation"] = items

        if changed_fields:
            updated.append({"vod_id": vod_id, "fields": changed_fields})

    patches = [p for p in patch_by_vod.values() if len(p) > 1]
    if not patches:
        reasons = "; ".join(s["reason"] for s in skipped) or "no writable fields"
        return {"success": False, "error": f"No VOD changes applied ({reasons})", "skipped": skipped}

    result = _patch_vods(client, loan_id, application_id, patches)
    if result.get("success"):
        result["updated"] = updated
    result["skipped"] = skipped
    return result


def _patch_vods(client, loan_id: str, application_id: str, rows: list) -> dict:
    url = (
        f"{client.api_base_url}/encompass/v3/loans/{loan_id}"
        f"/applications/{application_id}/vods?action=update"
    )
    headers = {
        "accept": "application/json",
        "Authorization": f"Bearer {client.access_token}",
        "content-type": "application/json",
    }
    resp = requests.patch(url, json=rows, headers=headers, timeout=30)
    if resp.status_code in (200, 204):
        return {"success": True, "updated": [r.get("id") for r in rows]}
    return {"success": False, "error": f"vods PATCH {resp.status_code}: {resp.text[:300]}"}


def _patch_vol(client, loan_id: str, application_id: str, row: dict) -> dict:
    vol_id = row.get("id")
    body = {k: v for k, v in row.items() if k != "id"}
    url = (
        f"{client.api_base_url}/encompass/v3/loans/{loan_id}"
        f"/applications/{application_id}/vols/{vol_id}"
    )
    headers = {
        "accept": "application/json",
        "Authorization": f"Bearer {client.access_token}",
        "content-type": "application/json",
    }
    resp = requests.patch(url, json=body, headers=headers, timeout=30)
    if resp.status_code in (200, 204):
        return {"success": True, "vol_id": vol_id, "fields": sorted(body.keys())}
    return {"success": False, "vol_id": vol_id, "error": f"vol PATCH {resp.status_code}: {resp.text[:300]}"}


def _write_collections_node(state: CollectionWriteState) -> dict:
    from encompass_client import (
        get_encompass_client,
        get_loan_applications,
        write_loan_contacts,
    )

    vods = state.get("vods") or []
    vols = state.get("vols") or []
    contacts = state.get("contacts") or []
    if not (vods or vols or contacts):
        return {"results": {"error": "No collection payload provided (vods/vols/contacts)"}}

    loan_id, err = _resolve_loan(state.get("loan_number", ""), state.get("env", "Prod"))
    if err:
        return {"results": {"error": err}}

    io_state = {"env": state.get("env", "Prod")}
    results: dict = {}

    application_id = None
    if vods or vols:
        apps = get_loan_applications(loan_id, state=io_state)
        application_id = apps[0].get("id") if apps else None
        if not application_id:
            return {"loan_id": loan_id, "results": {"error": "Could not resolve application id"}}

    client = get_encompass_client(state=io_state)

    if vods:
        # Normalised dashboard overrides carry vod_id + updates; raw v3 rows
        # carry id. Reject rows with neither (nothing to address them by).
        normalized = [r for r in vods if r.get("vod_id")]
        raw_rows = [r for r in vods if not r.get("vod_id") and r.get("id")]
        bad = [r for r in vods if not r.get("vod_id") and not r.get("id")]
        if bad:
            results["vods"] = {
                "success": False,
                "error": f"{len(bad)} VOD row(s) missing 'vod_id'",
            }
        elif normalized:
            results["vods"] = _apply_vod_overrides(
                client, io_state, loan_id, application_id, normalized
            )
        else:
            results["vods"] = _patch_vods(client, loan_id, application_id, raw_rows)
    if vols:
        vol_results: list[dict] = []
        for row in vols:
            if row.get("vol_id"):
                v3_row, err = _vol_override_to_v3(row)
                if err:
                    vol_results.append(
                        {"success": False, "vol_id": row["vol_id"], "error": err}
                    )
                else:
                    vol_results.append(
                        _patch_vol(client, loan_id, application_id, v3_row)
                    )
            elif row.get("id"):
                vol_results.append(_patch_vol(client, loan_id, application_id, row))
            else:
                vol_results.append(
                    {"success": False, "error": "VOL row missing 'vol_id'"}
                )
        results["vols"] = vol_results
    if contacts:
        results["contacts"] = write_loan_contacts(loan_id, contacts, state=io_state)

    logger.info(
        f"[WRITE_LOS_COLLECTIONS] loan {loan_id[:8]}: "
        f"vods={len(vods)}, vols={len(vols)}, contacts={len(contacts)} "
        f"(source={state.get('source') or 'dashboard-override'})"
    )
    return {"loan_id": loan_id, "results": results}


_collections_builder = StateGraph(CollectionWriteState)
_collections_builder.add_node("write_collections", _write_collections_node)
_collections_builder.add_edge(START, "write_collections")
_collections_builder.add_edge("write_collections", END)
collection_write_graph = _collections_builder.compile()
collection_write_graph.name = "write_los_collections"


if __name__ == "__main__":
    # Smoke test: compile check + input echo (no network).
    print(json.dumps({
        "write_los_fields": bool(field_write_graph),
        "write_los_collections": bool(collection_write_graph),
    }))
