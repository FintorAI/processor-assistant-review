"""LangGraph Platform-compatible thread endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from api.platform.models import (
    ThreadCreate,
    ThreadHistoryRequest,
    ThreadImport,
    ThreadSearch,
)
from api.registry import DEFAULT_ASSISTANT_ID, validate_assistant_id
from api.services.state import get_thread_history, get_thread_state, import_thread_state
from api.thread_store import get_thread_store


def _resolve_assistant(*candidates: str | None) -> str:
    """Pick the first candidate that's a known assistant, else the default.

    Cloud threads carry ``metadata.assistant_id`` as a *Cloud assistant UUID*
    (not one of this deployment's ids like ``review``), while ``metadata.graph_id``
    holds the graph name that matches our registry — so graph_id is the reliable
    signal. Falling back to the default keeps import robust to either shape.
    """
    for candidate in candidates:
        if not candidate:
            continue
        try:
            return validate_assistant_id(candidate)
        except KeyError:
            continue
    return DEFAULT_ASSISTANT_ID

router = APIRouter(tags=["threads"])


@router.post("/threads")
async def create_thread(body: ThreadCreate | None = None):
    metadata = body.metadata if body else None
    return get_thread_store().create(metadata)


@router.post("/threads/import")
async def import_thread(body: ThreadImport):
    """Migrate a thread from another deployment into this one.

    Recreates the thread record under the caller-supplied ``thread_id`` (so the
    dashboard's stored id keeps resolving and the loan is rerunnable) and, when
    ``values`` are given, writes the head state through the checkpointer so
    ``GET /threads/{id}/state`` returns the prior run. State-write failures don't
    fail the request: the thread record is already saved (still rerunnable) and
    the error is reported back per-thread for the migration manifest.

    Guarded by the API_KEY middleware like every other non-health route.
    """
    store = get_thread_store()
    existing = store.get(body.thread_id)
    if existing and not body.overwrite:
        return {"thread_id": body.thread_id, "skipped": True, "reason": "exists"}

    md = dict(body.metadata or {})
    assistant_id = _resolve_assistant(
        body.assistant_id, md.get("graph_id"), md.get("assistant_id")
    )
    # Overwrite any Cloud-UUID assistant_id so later GET /state reads (which
    # resolve the assistant from stored metadata) map to our review graph.
    md["assistant_id"] = assistant_id
    store.put_thread(body.thread_id, metadata=md, status=body.status or "idle")

    result: dict[str, object] = {
        "thread_id": body.thread_id,
        "thread_written": True,
        "state_written": False,
    }
    if body.values:
        try:
            import_thread_state(
                body.thread_id,
                body.values,
                assistant_id=assistant_id,
                as_node=body.as_node,
            )
            result["state_written"] = True
        except Exception as exc:  # noqa: BLE001 - reported per-thread, not fatal
            result["state_error"] = str(exc)
    return result


@router.post("/threads/search")
async def search_threads(body: ThreadSearch | None = None):
    body = body or ThreadSearch()
    return get_thread_store().search(
        body.metadata,
        status=body.status,
        limit=body.limit,
        offset=body.offset,
    )


@router.get("/threads/{thread_id}")
async def get_thread(thread_id: str):
    record = get_thread_store().get(thread_id)
    if not record:
        raise HTTPException(status_code=404, detail="Thread not found")
    return record


@router.get("/threads/{thread_id}/state")
async def get_thread_state_endpoint(
    thread_id: str,
    assistant_id: str | None = Query(default=None),
    # Accepted for LangGraph SDK compatibility (nested-graph state expansion);
    # DiscOrch's graph is flat, so subgraph values are a no-op here.
    subgraphs: bool = Query(default=False),
):
    try:
        return get_thread_state(thread_id, assistant_id=assistant_id)
    except KeyError as exc:
        detail = exc.args[0] if exc.args else str(exc)
        if str(detail).startswith(("Unknown assistant_id", "Thread not found")):
            raise HTTPException(status_code=404, detail=str(detail)) from exc
        raise HTTPException(status_code=500, detail=str(detail)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/threads/{thread_id}/history")
async def get_thread_history_endpoint(
    thread_id: str,
    body: ThreadHistoryRequest | None = None,
    assistant_id: str | None = Query(default=None),
):
    if not get_thread_store().get(thread_id):
        raise HTTPException(status_code=404, detail="Thread not found")
    body = body or ThreadHistoryRequest()
    before = body.before
    if isinstance(before, dict):
        before = before.get("checkpoint_id")
    try:
        return get_thread_history(
            thread_id, assistant_id=assistant_id, limit=body.limit, before=before
        )
    except KeyError as exc:
        detail = exc.args[0] if exc.args else str(exc)
        if str(detail).startswith(("Unknown assistant_id", "Thread not found")):
            raise HTTPException(status_code=404, detail=str(detail)) from exc
        raise HTTPException(status_code=500, detail=str(detail)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
