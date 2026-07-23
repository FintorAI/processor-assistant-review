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

    Input (all payload lists optional; raw Encompass v3 schema keys):
        vods:     [{"id": vodId, ...raw VOD fields (e.g. holderName, items[])}]
                  → PATCH /applications/{appId}/vods?action=update
        vols:     [{"id": volId, ...raw VOL fields (e.g. unpaidBalanceAmount)}]
                  → PATCH /applications/{appId}/vols/{volId}   (one PATCH per row)
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
        bad = [r for r in vods if not r.get("id")]
        results["vods"] = (
            {"success": False, "error": f"{len(bad)} VOD row(s) missing 'id'"}
            if bad else _patch_vods(client, loan_id, application_id, vods)
        )
    if vols:
        results["vols"] = [
            _patch_vol(client, loan_id, application_id, row) if row.get("id")
            else {"success": False, "error": "VOL row missing 'id'"}
            for row in vols
        ]
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
