"""LangGraph Platform-compatible run endpoints.

Endpoint inventory (matches what DiscOrch clients actually call):
- POST /runs/wait                          threadless sync invoke
- POST /runs/stream                        threadless SSE
- POST /threads/{id}/runs                  background run (create + poll)
- POST /threads/{id}/runs/wait             threaded sync invoke
- POST /threads/{id}/runs/stream           threaded SSE
- GET  /threads/{id}/runs                  list runs
- GET  /threads/{id}/runs/{run_id}         poll background run status
- GET  /threads/{id}/runs/{run_id}/stream  reconnect/join (best-effort — polls
                                            state rather than replaying history)
- POST /threads/{id}/runs/{run_id}/cancel  cooperative cancel (flag checked
                                            between graph supersteps)
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from api.platform.models import RunCreate
from api.services.runs import (
    create_background_run,
    invoke_run,
    reconnect_stream,
    request_run_cancel,
    stream_run,
)
from api.thread_store import get_thread_store

router = APIRouter(tags=["runs"])

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def _reject_threadless_thread_id(body: RunCreate) -> None:
    configurable = (body.config or {}).get("configurable") or {}
    if configurable.get("thread_id"):
        raise HTTPException(
            status_code=422,
            detail="thread_id in config.configurable is not allowed on threadless runs",
        )


def _translate_key_error(exc: KeyError) -> HTTPException:
    detail = exc.args[0] if exc.args else str(exc)
    if str(detail).startswith(("Unknown assistant_id", "Thread not found")):
        return HTTPException(status_code=404, detail=str(detail))
    return HTTPException(status_code=500, detail=str(detail))


@router.post("/runs/wait")
async def runs_wait(body: RunCreate):
    _reject_threadless_thread_id(body)
    try:
        return invoke_run(body.assistant_id, body.model_dump())
    except KeyError as exc:
        raise _translate_key_error(exc) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/runs/stream")
async def runs_stream(body: RunCreate):
    _reject_threadless_thread_id(body)
    try:
        return StreamingResponse(
            stream_run(body.assistant_id, body.model_dump()),
            media_type="text/event-stream",
            headers=_SSE_HEADERS,
        )
    except KeyError as exc:
        raise _translate_key_error(exc) from exc


@router.post("/threads/{thread_id}/runs")
async def create_thread_run(thread_id: str, body: RunCreate):
    """Background (non-blocking) run: returns immediately with a run record.

    Clients poll GET /threads/{thread_id}/runs/{run_id} until status is
    "success" / "error", then read GET /threads/{thread_id}/state.
    """
    if not get_thread_store().get(thread_id):
        raise HTTPException(status_code=404, detail="Thread not found")
    try:
        return create_background_run(body.assistant_id, body.model_dump(), thread_id=thread_id)
    except KeyError as exc:
        raise _translate_key_error(exc) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/threads/{thread_id}/runs/wait")
async def thread_runs_wait(thread_id: str, body: RunCreate):
    try:
        return invoke_run(body.assistant_id, body.model_dump(), thread_id=thread_id)
    except KeyError as exc:
        raise _translate_key_error(exc) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/threads/{thread_id}/runs/stream")
async def thread_runs_stream(thread_id: str, body: RunCreate):
    try:
        return StreamingResponse(
            stream_run(body.assistant_id, body.model_dump(), thread_id=thread_id),
            media_type="text/event-stream",
            headers=_SSE_HEADERS,
        )
    except KeyError as exc:
        raise _translate_key_error(exc) from exc


@router.get("/threads/{thread_id}/runs")
async def list_thread_runs(thread_id: str):
    if not get_thread_store().get(thread_id):
        raise HTTPException(status_code=404, detail="Thread not found")
    return get_thread_store().list_runs(thread_id)


@router.get("/threads/{thread_id}/runs/{run_id}")
async def get_thread_run(thread_id: str, run_id: str):
    record = get_thread_store().get_run(thread_id, run_id)
    if not record:
        raise HTTPException(status_code=404, detail="Run not found")
    return record


@router.get("/threads/{thread_id}/runs/{run_id}/stream")
async def join_thread_run_stream(
    thread_id: str,
    run_id: str,
    # Accepted for SDK compatibility (langgraph-sdk's join_stream sends
    # both); see reconnect_stream()'s docstring for why they're not honored
    # precisely — this polls current state rather than replaying history.
    stream_mode: list[str] | None = Query(default=None),
    cancel_on_disconnect: bool = Query(default=False),
):
    if not get_thread_store().get(thread_id):
        raise HTTPException(status_code=404, detail="Thread not found")
    if not get_thread_store().get_run(thread_id, run_id):
        raise HTTPException(status_code=404, detail="Run not found")
    return StreamingResponse(
        reconnect_stream(thread_id, run_id),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


@router.post("/threads/{thread_id}/runs/{run_id}/cancel")
async def cancel_thread_run(thread_id: str, run_id: str):
    """Cooperative cancellation: flags the run; the Fargate worker / streamed
    Lambda run checks the flag between graph steps and exits early. Not an
    immediate kill — see docs/AWS_DEPLOYMENT.md for the hard-kill follow-up
    (ecs:StopTask) if a run needs to die faster than its next checkpoint.
    """
    record = request_run_cancel(thread_id, run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return record
