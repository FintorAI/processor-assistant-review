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

    from encompass_client import LoanLockedError, loan_lock

    io_state = {"env": state.get("env", "Prod")}
    try:
        with loan_lock(loan_id, state=io_state):
            written, bad_fields = write_fields_resilient(loan_id, updates, state=io_state)
    except LoanLockedError as exc:
        return {
            "loan_id": loan_id,
            "results": {"written": {}, "failed": {}, "error": str(exc)},
        }
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
        voes:     [{"voe_id": "...",
                    "applicant_type": "borrower"|"coborrower",  # locates the
                                # applicant-scoped employment endpoint
                    "updates": {employer_name?, title?, current_employment?,
                                self_employed?, start_date?, base_pay?, bonus?,
                                overtime?, commissions?, phone?}}]

    Raw v3 passthrough (scripts / power users):
        vods:     [{"id": vodId, ...raw VOD fields (e.g. holderName, items[])}]
        vols:     [{"id": volId, ...raw VOL fields (e.g. unpaidBalanceAmount)}]
        voes:     [{"id": voeId, "applicant_type": "borrower"|"coborrower",
                    ...raw Employment fields (e.g. basePayAmount)}]

    Contacts are always raw v3 rows (the file_contacts channel already holds
    the GET shape):
        contacts: [{"contactType": "...", ...}]
                  → PATCH /loans/{id}/contacts  (upsert by contactType)
    Output:
        results: {
            vods:     {success, updated?/skipped?/error?, rows?: [read_vods rows]},
            vols:     {success, saved: [per-row], rows?: [read_vols rows]},
            voes:     {success, saved: [per-row], rows?: [read_voes rows]},
            contacts: {success, written?, rows?: [raw contact dicts]},
            error?: str,
        }
        Each collection that saved successfully carries a fresh ``rows`` array
        (re-read from Encompass) so the dashboard can refresh instead of showing
        its stale pre-write snapshot. A read-back failure surfaces as
        ``<collection>.rows_error`` and never masks the successful write.
    """
    loan_number: str
    env: str
    vods: NotRequired[list]
    vols: NotRequired[list]
    voes: NotRequired[list]
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


_VOL_FLOAT_KEYS = {"monthly_payment", "unpaid_balance", "credit_limit"}
_VOL_INT_KEYS = {"remaining_months"}
_VOL_BOOL_KEYS = {"exclude_monthly_pay", "payoff_included"}


def _coerce_vol_value(key: str, value) -> tuple[object, str | None]:
    """Coerce a normalised VOL value to the numeric/boolean type the v3 API
    requires → (coerced, error). Cell edits arrive as strings when the user
    typed something the dashboard couldn't parse back (e.g. "15,382")."""
    if key in _VOL_FLOAT_KEYS or key in _VOL_INT_KEYS:
        try:
            num = float(str(value).replace(",", "").strip())
        except (TypeError, ValueError):
            return None, f"invalid numeric value for {key}: {value!r}"
        return (int(num) if key in _VOL_INT_KEYS else num), None
    if key in _VOL_BOOL_KEYS:
        if isinstance(value, bool):
            return value, None
        text = str(value).strip().lower()
        if text in ("true", "1", "yes"):
            return True, None
        if text in ("false", "0", "no", ""):
            return False, None
        return None, f"invalid boolean value for {key}: {value!r}"
    return value, None


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
        coerced, coerce_err = _coerce_vol_value(key, value)
        if coerce_err:
            return None, coerce_err
        body[api_field] = coerced
    if not body:
        return None, "no writable fields in updates"
    return {"id": entry["vol_id"], **body}, None


# Normalised read_voes keys → v3 Employment PATCH fields. Computed income
# fields (monthly_income) are read-only and intentionally NOT writable.
_VOE_KEY_TO_V3 = {
    "employer_name":      "employerName",
    "title":              "title",
    "current_employment": "currentEmploymentIndicator",
    "self_employed":      "selfEmployedIndicator",
    "start_date":         "employmentStartDate",
    "base_pay":           "basePayAmount",
    "bonus":              "bonusAmount",
    "overtime":           "overtimeAmount",
    "commissions":        "commissionsAmount",
    "phone":              "phoneNumber",
}

_VOE_FLOAT_KEYS = {"base_pay", "bonus", "overtime", "commissions"}
_VOE_BOOL_KEYS = {"current_employment", "self_employed"}


def _coerce_voe_value(key: str, value) -> tuple[object, str | None]:
    """Coerce a normalised VOE value to the numeric/boolean type the v3 API
    requires → (coerced, error)."""
    if key in _VOE_FLOAT_KEYS:
        try:
            return float(str(value).replace(",", "").strip()), None
        except (TypeError, ValueError):
            return None, f"invalid numeric value for {key}: {value!r}"
    if key in _VOE_BOOL_KEYS:
        if isinstance(value, bool):
            return value, None
        text = str(value).strip().lower()
        if text in ("true", "1", "yes"):
            return True, None
        if text in ("false", "0", "no", ""):
            return False, None
        return None, f"invalid boolean value for {key}: {value!r}"
    return value, None


def _voe_override_to_v3(entry: dict) -> tuple[str, dict | None, str | None]:
    """Translate a normalised {"voe_id", "applicant_type", "updates"} entry into
    ``(applicant_type, raw_patch_row, error)``. VOEs are applicant-scoped, so the
    applicant_type is returned for routing (never sent in the body)."""
    updates = entry.get("updates") or {}
    applicant = (entry.get("applicant_type") or "borrower").lower()
    body: dict = {}
    for key, value in updates.items():
        api_field = _VOE_KEY_TO_V3.get(key)
        if not api_field:
            continue  # unknown / non-writable / computed key — ignore silently
        coerced, coerce_err = _coerce_voe_value(key, value)
        if coerce_err:
            return applicant, None, coerce_err
        body[api_field] = coerced
    if not body:
        return applicant, None, "no writable fields in updates"
    return applicant, {"id": entry["voe_id"], **body}, None


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
    vods_with_changes: set[str] = set()

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
        # Always resend the current account array (unchanged for header-only
        # edits): the collection PATCH replaces arrays wholesale, so omitting
        # it risks clearing the existing rows.
        patch.setdefault("items" if is_urla else "accountInformation", items)
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
            # Sole-item fallback ONLY when that item carries no identifier of
            # its own — a requested-but-unmatched number must never fall back
            # onto a different account.
            if (
                target is None
                and len(items) == 1
                and not _digits_last4(items[0].get(id_field))
            ):
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
                if "account_number" in item_updates:
                    if _is_masked(item_updates["account_number"]):
                        skipped.append({
                            "vod_id": vod_id,
                            "reason": "account_number is masked — not written",
                        })
                    else:
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

        if changed_fields:
            updated.append({"vod_id": vod_id, "fields": changed_fields})
            vods_with_changes.add(vod_id)

    patches = [p for vid, p in patch_by_vod.items() if vid in vods_with_changes]
    if not patches:
        reasons = "; ".join(s["reason"] for s in skipped) or "no writable fields"
        return {"success": False, "error": f"No VOD changes applied ({reasons})", "skipped": skipped}

    result = _patch_vods(client, loan_id, application_id, patches)
    if result.get("success"):
        result["updated"] = updated
    result["skipped"] = skipped
    return result


# ``depositoryAccountGuid`` is server-assigned and readonly — echoing it back
# from a GET makes the collection PATCH 400 ("The DepositoryAccountGuid field is
# readonly."). ``itemNumber`` is NOT stripped here: unlike merge_duplicate_vods
# (which recombines items and re-numbers them), this path resends the existing
# items in place, where itemNumber is required (1–4) and already valid.
_VOD_ITEM_READONLY_ON_RESEND = {"depositoryAccountGuid"}


def _strip_vod_readonly(rows: list) -> list:
    """Drop server-assigned readonly fields from every account item so the
    resent (wholesale-replaced) array is accepted by the collection PATCH."""
    cleaned: list = []
    for row in rows:
        r = dict(row)
        for arr_key in ("items", "accountInformation"):
            arr = r.get(arr_key)
            if isinstance(arr, list):
                r[arr_key] = [
                    {k: v for k, v in item.items() if k not in _VOD_ITEM_READONLY_ON_RESEND}
                    if isinstance(item, dict) else item
                    for item in arr
                ]
        cleaned.append(r)
    return cleaned


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
    rows = _strip_vod_readonly(rows)
    try:
        resp = requests.patch(url, json=rows, headers=headers, timeout=30)
    except requests.exceptions.RequestException as exc:
        return {"success": False, "error": f"vods PATCH network error: {exc}"}
    if resp.status_code in (200, 204):
        return {"success": True, "updated": [r.get("id") for r in rows]}
    return {"success": False, "error": f"vods PATCH {resp.status_code}: {resp.text[:300]}"}


def _patch_vols(client, loan_id: str, application_id: str, rows: list) -> dict:
    """Update VOLs via the collection endpoint (V3 Manage VOLs).

    Like VODs, VOLs are managed with ONE PATCH on the whole collection —
    ``?action=update`` in the query, a bare array of ``{"id": volId, ...fields}``
    in the body. There is no per-``{volId}`` PATCH route (hitting it 403s).
    """
    url = (
        f"{client.api_base_url}/encompass/v3/loans/{loan_id}"
        f"/applications/{application_id}/vols?action=update"
    )
    headers = {
        "accept": "application/json",
        "Authorization": f"Bearer {client.access_token}",
        "content-type": "application/json",
    }
    try:
        resp = requests.patch(url, json=rows, headers=headers, timeout=30)
    except requests.exceptions.RequestException as exc:
        return {"success": False, "error": f"vols PATCH network error: {exc}"}
    if resp.status_code in (200, 204):
        return {"success": True, "updated": [r.get("id") for r in rows]}
    return {"success": False, "error": f"vols PATCH {resp.status_code}: {resp.text[:300]}"}


def _patch_voes(client, loan_id: str, application_id: str, applicant_type: str, rows: list) -> dict:
    """Update VOEs via the applicant-scoped Employment collection endpoint.

    VOEs (V3 Manage Employment) are managed with ONE PATCH per applicant —
    ``?action=update`` in the query, a bare array of ``{"id": voeId, ...fields}``
    in the body. The applicant (borrower/coborrower) is part of the PATH, so
    rows for different applicants must be sent in separate calls.
    """
    applicant = (applicant_type or "borrower").lower()
    url = (
        f"{client.api_base_url}/encompass/v3/loans/{loan_id}"
        f"/applications/{application_id}/{applicant}/employment?action=update"
    )
    headers = {
        "accept": "application/json",
        "Authorization": f"Bearer {client.access_token}",
        "content-type": "application/json",
    }
    try:
        resp = requests.patch(url, json=rows, headers=headers, timeout=30)
    except requests.exceptions.RequestException as exc:
        return {"success": False, "error": f"voes PATCH network error: {exc}"}
    if resp.status_code in (200, 204):
        return {"success": True, "updated": [r.get("id") for r in rows]}
    return {"success": False, "error": f"voes PATCH {resp.status_code}: {resp.text[:300]}"}


def _merge_vod_outcomes(outcomes: list[dict]) -> dict:
    """Combine the normalized-override and raw-passthrough VOD batch results
    (mixed payloads) into one dict following the same result conventions."""
    merged: dict = {"success": all(o.get("success") for o in outcomes)}
    errors = [str(o["error"]) for o in outcomes if o.get("error")]
    if errors:
        merged["error"] = "; ".join(errors)
    updated = [u for o in outcomes for u in (o.get("updated") or [])]
    if updated:
        merged["updated"] = updated
    skipped = [s for o in outcomes for s in (o.get("skipped") or [])]
    if skipped:
        merged["skipped"] = skipped
    return merged


def _attach_rows(result: dict, reader) -> None:
    """Best-effort: attach fresh read-back rows to a successful write result.

    The collection PATCHes return no (or partial) row data, so without this the
    dashboard re-renders its stale pre-write snapshot (looks like the edit
    reverted). On success we re-read from Encompass and expose the authoritative
    rows under ``result["rows"]``; a read failure is surfaced as
    ``result["rows_error"]`` and never masks the successful write.
    """
    if not isinstance(result, dict) or not result.get("success"):
        return
    try:
        result["rows"] = reader()
    except Exception as exc:  # noqa: BLE001 — read-back is best-effort
        result["rows_error"] = str(exc)


def _write_collections_node(state: CollectionWriteState) -> dict:
    from encompass_client import (
        LoanLockedError,
        get_encompass_client,
        get_loan_applications,
        loan_lock,
    )

    vods = state.get("vods") or []
    vols = state.get("vols") or []
    voes = state.get("voes") or []
    contacts = state.get("contacts") or []
    if not (vods or vols or voes or contacts):
        return {"results": {"error": "No collection payload provided (vods/vols/voes/contacts)"}}

    loan_id, err = _resolve_loan(state.get("loan_number", ""), state.get("env", "Prod"))
    if err:
        return {"results": {"error": err}}

    io_state = {"env": state.get("env", "Prod")}

    application_id = None
    if vods or vols or voes:
        apps = get_loan_applications(loan_id, state=io_state)
        application_id = apps[0].get("id") if apps else None
        if not application_id:
            return {"loan_id": loan_id, "results": {"error": "Could not resolve application id"}}

    client = get_encompass_client(state=io_state)

    # One lock for the whole action (VODs + VOLs + VOEs + contacts), same
    # "hold for the whole action, not per write-substep" rule as the review
    # agent — see LoanLockMiddleware in proc_agent.py and
    # processor-assistant-orchestrator/docs/encompass_resource_locking_plan.md.
    try:
        with loan_lock(loan_id, state=io_state):
            results = _apply_collection_writes(
                client, io_state, loan_id, application_id, vods, vols, voes, contacts,
            )
    except LoanLockedError as exc:
        return {"loan_id": loan_id, "results": {"error": str(exc)}}

    logger.info(
        f"[WRITE_LOS_COLLECTIONS] loan {loan_id[:8]}: "
        f"vods={len(vods)}, vols={len(vols)}, voes={len(voes)}, contacts={len(contacts)} "
        f"(source={state.get('source') or 'dashboard-override'})"
    )
    return {"loan_id": loan_id, "results": results}


def _apply_collection_writes(
    client, io_state: dict, loan_id: str, application_id: str | None,
    vods: list, vols: list, voes: list, contacts: list,
) -> dict:
    """Write span for write_los_collections — runs inside the loan_lock held
    by the caller. Extracted so the whole span (all four collection types,
    one dashboard "Save" action) is wrapped by a single lock/finally, not one
    per collection type."""
    from encompass_client import get_loan_contacts, write_loan_contacts
    from shared.encompass_io import read_vods, read_vols, read_voes

    results: dict = {}

    if vods:
        # Normalised dashboard overrides carry vod_id + updates; raw v3 rows
        # carry id. Reject rows with neither (nothing to address them by).
        normalized = [r for r in vods if r.get("vod_id")]
        raw_rows = [r for r in vods if not r.get("vod_id") and r.get("id")]
        bad = [r for r in vods if not r.get("vod_id") and not r.get("id")]
        if bad:
            results["vods"] = {
                "success": False,
                "error": f"{len(bad)} VOD row(s) missing both 'vod_id' and 'id'",
            }
        else:
            # Mixed payloads run both partitions and combine their outcomes.
            outcomes: list[dict] = []
            if normalized:
                outcomes.append(
                    _apply_vod_overrides(
                        client, io_state, loan_id, application_id, normalized
                    )
                )
            if raw_rows:
                outcomes.append(
                    _patch_vods(client, loan_id, application_id, raw_rows)
                )
            results["vods"] = (
                outcomes[0] if len(outcomes) == 1 else _merge_vod_outcomes(outcomes)
            )
        _attach_rows(results["vods"], lambda: read_vods(loan_id, state=io_state))
    if vols:
        vol_results: list[dict] = []
        translated: list[dict] = []  # {id, ...fields} rows for the batch PATCH
        for row in vols:
            if row.get("vol_id"):
                v3_row, err = _vol_override_to_v3(row)
                if err:
                    vol_results.append(
                        {"success": False, "vol_id": row["vol_id"], "error": err}
                    )
                else:
                    translated.append(v3_row)
            elif row.get("id"):
                translated.append(row)
            else:
                vol_results.append(
                    {"success": False, "error": "VOL row missing 'vol_id'"}
                )
        # One collection PATCH for all writable rows (V3 Manage VOLs contract).
        if translated:
            batch = _patch_vols(client, loan_id, application_id, translated)
            for r in translated:
                if batch.get("success"):
                    vol_results.append({
                        "success": True,
                        "vol_id": r.get("id"),
                        "fields": sorted(k for k in r if k != "id"),
                    })
                else:
                    vol_results.append({
                        "success": False,
                        "vol_id": r.get("id"),
                        "error": batch.get("error"),
                    })
        vol_success = bool(vol_results) and all(r.get("success") for r in vol_results)
        results["vols"] = {"success": vol_success, "saved": vol_results}
        _attach_rows(results["vols"], lambda: read_vols(loan_id, state=io_state))
    if voes:
        voe_results: list[dict] = []
        # VOEs are applicant-scoped, so group translated rows by applicant and
        # send one collection PATCH per applicant (borrower / coborrower).
        by_applicant: dict[str, list[dict]] = {}
        for row in voes:
            if row.get("voe_id"):
                applicant, v3_row, err = _voe_override_to_v3(row)
                if err:
                    voe_results.append(
                        {"success": False, "voe_id": row["voe_id"], "error": err}
                    )
                else:
                    by_applicant.setdefault(applicant, []).append(v3_row)
            elif row.get("id"):
                applicant = (row.get("applicant_type") or "borrower").lower()
                by_applicant.setdefault(applicant, []).append(
                    {k: v for k, v in row.items() if k != "applicant_type"}
                )
            else:
                voe_results.append(
                    {"success": False, "error": "VOE row missing 'voe_id'"}
                )
        for applicant, rows_ in by_applicant.items():
            batch = _patch_voes(client, loan_id, application_id, applicant, rows_)
            for r in rows_:
                if batch.get("success"):
                    voe_results.append({
                        "success": True,
                        "voe_id": r.get("id"),
                        "applicant_type": applicant,
                        "fields": sorted(k for k in r if k != "id"),
                    })
                else:
                    voe_results.append({
                        "success": False,
                        "voe_id": r.get("id"),
                        "applicant_type": applicant,
                        "error": batch.get("error"),
                    })
        voe_success = bool(voe_results) and all(r.get("success") for r in voe_results)
        results["voes"] = {"success": voe_success, "saved": voe_results}
        _attach_rows(results["voes"], lambda: read_voes(loan_id, state=io_state))
    if contacts:
        contacts_result = write_loan_contacts(loan_id, contacts, state=io_state)
        # Read the contacts back from Encompass so the dashboard renders the
        # just-saved values instead of its pre-write snapshot (the PATCH is a
        # 204 with no body, so without this the UI shows stale rows).
        if contacts_result.get("success"):
            try:
                contacts_result["rows"] = get_loan_contacts(loan_id, state=io_state)
            except Exception as exc:  # noqa: BLE001 — read-back is best-effort
                contacts_result["rows_error"] = str(exc)
        results["contacts"] = contacts_result

    return results


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
