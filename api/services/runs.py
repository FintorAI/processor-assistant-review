"""Graph run execution: sync invoke, SSE streaming, and background runs.

Background runs (POST /threads/{id}/runs) can't execute in the request that
created them — on Lambda, returning the HTTP response freezes the container.
Dispatch order (first match wins):

1. **Fargate** — when ``WORKER_TASK_DEFINITION_ARN`` is set, launch an ECS
   Fargate task that runs ``python -m api.worker``. No 15-min ceiling; used for
   full disclosure workflows.
2. **Lambda self-invoke** — when ``AWS_LAMBDA_FUNCTION_NAME`` is set (and no
   Fargate config), async-invoke this function; the Web Adapter forwards the
   event to POST /events. Capped at the Lambda timeout (900s).
3. **Local thread** — daemon thread for ``uvicorn`` / unit tests.

Run input is persisted on the DynamoDB run record so the Fargate worker only
needs thread_id + run_id (ECS overrides have an 8 KB limit).
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from typing import Any, Iterator

from api.platform.sse import (
    format_end_event,
    format_error_event,
    format_metadata_event,
    format_sse_event,
    normalize_stream_modes,
    serialize_stream_payload,
    stream_event_name,
)
from api.registry import build_graph, validate_assistant_id
from api.thread_store import get_thread_store

logger = logging.getLogger(__name__)

BACKGROUND_RUN_EVENT_KIND = "discorch.background_run"

_TERMINAL_RUN_STATUSES = frozenset({"success", "error", "cancelled"})
_RECONNECT_POLL_SECONDS = 2.0
_UNSET = object()


def _merge_config(run_body: dict[str, Any], thread_id: str | None) -> dict[str, Any]:
    config: dict[str, Any] = dict(run_body.get("config") or {})
    configurable = dict(config.get("configurable") or {})
    if thread_id:
        configurable["thread_id"] = thread_id
    config["configurable"] = configurable
    return config


def _record_thread_run(thread_id: str | None, assistant_id: str, run_body: dict[str, Any]) -> None:
    if not thread_id:
        return
    input_data = run_body.get("input") or {}
    metadata = {
        k: input_data[k]
        for k in ("loan_number", "env", "borrower_name")
        if input_data.get(k) is not None
    }
    get_thread_store().record_run(thread_id, assistant_id=assistant_id, metadata=metadata)


def invoke_run(
    assistant_id: str,
    run_body: dict[str, Any],
    *,
    thread_id: str | None = None,
) -> dict[str, Any]:
    config = _merge_config(run_body, thread_id)
    _record_thread_run(thread_id, assistant_id, run_body)
    graph = build_graph(assistant_id, with_checkpointer=bool(thread_id))
    try:
        result = graph.invoke(run_body.get("input") or {}, config=config)
    except Exception:
        if thread_id:
            get_thread_store().set_status(thread_id, "error")
        raise
    if thread_id:
        get_thread_store().set_status(thread_id, "idle")
    return result


def stream_run(
    assistant_id: str,
    run_body: dict[str, Any],
    *,
    thread_id: str | None = None,
) -> Iterator[str]:
    config = _merge_config(run_body, thread_id)
    _record_thread_run(thread_id, assistant_id, run_body)
    graph = build_graph(assistant_id, with_checkpointer=bool(thread_id))
    input_data = run_body.get("input") or {}
    stream_modes = normalize_stream_modes(run_body)
    store = get_thread_store()

    # Track a run record even for streamed runs so POST .../runs/{id}/cancel
    # has something to flag — without this, a streamed run has no run_id the
    # client could ever pass to /cancel.
    run_id = str(uuid.uuid4())
    if thread_id:
        run = store.create_run(thread_id, assistant_id=assistant_id, input_payload={})
        run_id = run["run_id"]
        store.update_run(thread_id, run_id, status="running")

    yield format_metadata_event(run_id)

    cancelled = False
    try:
        for event in graph.stream(
            input_data,
            config=config,
            stream_mode=stream_modes,
        ):
            if isinstance(event, tuple) and len(event) == 2:
                mode, payload = event
                yield format_sse_event(
                    stream_event_name(mode),
                    serialize_stream_payload(mode, payload),
                )
            else:
                yield format_sse_event("updates", serialize_stream_payload("updates", event))

            if thread_id and store.is_cancel_requested(thread_id, run_id):
                cancelled = True
                break
    except Exception as exc:
        if thread_id:
            store.update_run(thread_id, run_id, status="error", error=str(exc))
            store.set_status(thread_id, "error")
        yield format_error_event(str(exc))
        raise
    else:
        if thread_id:
            if cancelled:
                store.update_run(thread_id, run_id, status="cancelled")
                store.set_status(thread_id, "idle")
                yield format_error_event("Run cancelled")
            else:
                store.update_run(thread_id, run_id, status="success")
                store.set_status(thread_id, "idle")
    finally:
        yield format_end_event()


# ─────────────────────────────────────────────────────────────────────────────
# Background runs
# ─────────────────────────────────────────────────────────────────────────────

def _dispatch_fargate(thread_id: str, run_id: str) -> str:
    """Launch an ECS Fargate task that runs ``python -m api.worker``.

    Returns the launched task's ARN so the caller can persist it — needed
    for cancel() to ``ecs.stop_task`` rather than only setting a flag the
    worker checks on its own schedule.
    """
    import boto3

    cluster = os.environ["WORKER_CLUSTER_ARN"]
    task_def = os.environ["WORKER_TASK_DEFINITION_ARN"]
    container_name = os.environ.get("WORKER_CONTAINER_NAME", "worker")
    subnet_ids = [s.strip() for s in os.environ.get("WORKER_SUBNET_IDS", "").split(",") if s.strip()]
    security_group_id = os.environ.get("WORKER_SECURITY_GROUP_ID", "").strip()
    if not subnet_ids or not security_group_id:
        raise RuntimeError(
            "Fargate worker env incomplete: WORKER_SUBNET_IDS and "
            "WORKER_SECURITY_GROUP_ID are required when WORKER_TASK_DEFINITION_ARN is set"
        )

    resp = boto3.client("ecs").run_task(
        cluster=cluster,
        taskDefinition=task_def,
        launchType="FARGATE",
        count=1,
        platformVersion="LATEST",
        networkConfiguration={
            "awsvpcConfiguration": {
                "subnets": subnet_ids,
                "securityGroups": [security_group_id],
                "assignPublicIp": "ENABLED",
            }
        },
        overrides={
            "containerOverrides": [
                {
                    "name": container_name,
                    "environment": [
                        {"name": "WORKER_RUN_THREAD_ID", "value": thread_id},
                        {"name": "WORKER_RUN_ID", "value": run_id},
                    ],
                }
            ]
        },
        startedBy=f"par-review-{run_id[:8]}",
    )
    failures = resp.get("failures") or []
    if failures:
        raise RuntimeError(f"ecs.run_task failed: {failures}")
    tasks = resp.get("tasks") or []
    task_arn = tasks[0].get("taskArn") if tasks else None
    logger.info(
        "Dispatched background run %s via Fargate task %s",
        run_id,
        task_arn,
    )
    return task_arn


def _dispatch_lambda_self_invoke(payload: dict[str, Any]) -> None:
    import boto3

    function_name = os.environ["AWS_LAMBDA_FUNCTION_NAME"]
    boto3.client("lambda").invoke(
        FunctionName=function_name,
        InvocationType="Event",
        Payload=json.dumps(payload).encode(),
    )
    logger.info("Dispatched background run %s via async Lambda self-invoke", payload["run_id"])


def create_background_run(
    assistant_id: str,
    run_body: dict[str, Any],
    *,
    thread_id: str,
) -> dict[str, Any]:
    validate_assistant_id(assistant_id)
    store = get_thread_store()
    run = store.create_run(
        thread_id,
        assistant_id=assistant_id,
        input_payload={"assistant_id": assistant_id, "run_body": run_body},
    )
    _record_thread_run(thread_id, assistant_id, run_body)

    payload = {
        "kind": BACKGROUND_RUN_EVENT_KIND,
        "thread_id": thread_id,
        "run_id": run["run_id"],
        "assistant_id": assistant_id,
        "run_body": run_body,
        # Shared token so the public POST /events route only accepts payloads
        # originating from our own async self-invoke (or a caller who already
        # holds the API key anyway).
        "_auth": os.environ.get("API_KEY", ""),
    }

    if os.environ.get("WORKER_TASK_DEFINITION_ARN", "").strip():
        task_arn = _dispatch_fargate(thread_id, run["run_id"])
        if task_arn:
            store.set_run_task_arn(thread_id, run["run_id"], task_arn)
    elif os.environ.get("AWS_LAMBDA_FUNCTION_NAME", "").strip():
        _dispatch_lambda_self_invoke(payload)
    else:
        thread = threading.Thread(
            target=execute_background_run,
            args=(payload,),
            daemon=True,
            name=f"bg-run-{run['run_id'][:8]}",
        )
        thread.start()
        logger.info("Dispatched background run %s via local thread", run["run_id"])

    return run


def request_run_cancel(thread_id: str, run_id: str) -> dict[str, Any] | None:
    """Cancel a run: flags it cooperatively (worker/stream loop checks between
    supersteps) and, if it's a Fargate-dispatched run, also calls
    ``ecs.stop_task`` for an immediate kill rather than waiting for the next
    checkpoint boundary — a stuck tool call could otherwise keep a task (and
    its cost) alive indefinitely between cancel-flag checks.
    """
    store = get_thread_store()
    record = store.request_cancel(thread_id, run_id)
    if record is None:
        return None

    task_arn = store.get_run_task_arn(thread_id, run_id)
    if task_arn and record.get("status") not in ("success", "error", "cancelled"):
        try:
            import boto3

            cluster = os.environ.get("WORKER_CLUSTER_ARN", "").strip()
            if cluster:
                boto3.client("ecs").stop_task(
                    cluster=cluster, task=task_arn, reason="Cancelled via API"
                )
                logger.info("Cancel %s: stopped Fargate task %s", run_id, task_arn)
        except Exception:
            # Cooperative flag is already set — worst case the worker
            # notices it on its own at the next superstep instead of dying
            # immediately. Don't fail the cancel request over this.
            logger.exception("Cancel %s: ecs.stop_task failed (flag still set)", run_id)
    return record


def _fire_run_webhook(
    *,
    webhook_url: str,
    thread_id: str,
    run_id: str,
    assistant_id: str,
    status: str,
    error: str | None = None,
) -> None:
    """POST LangGraph Platform-shaped run payload to the client webhook URL.

    Dashboard ``reviewRunWebhook`` expects a Run-like body with
    ``run_id``, ``thread_id``, ``assistant_id``, ``status``, ``values``, ``error``.
    Failures are logged only — webhook delivery must not fail the run.
    """
    import urllib.error
    import urllib.request

    values: dict[str, Any] = {}
    try:
        from api.services.state import get_thread_state

        snapshot = get_thread_state(thread_id, assistant_id=assistant_id)
        values = snapshot.get("values") or {}
    except Exception:
        logger.exception("Webhook: failed to load thread state for %s", run_id)

    body = {
        "run_id": run_id,
        "thread_id": thread_id,
        "assistant_id": assistant_id,
        "status": status,
        "values": values,
        "error": error,
    }
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            logger.info(
                "Webhook POST %s → HTTP %s (run %s status=%s)",
                webhook_url.split("?", 1)[0],
                getattr(resp, "status", "?"),
                run_id,
                status,
            )
    except urllib.error.HTTPError as exc:
        logger.warning("Webhook HTTP %s for run %s: %s", exc.code, run_id, exc.read()[:200])
    except Exception:
        logger.exception("Webhook POST failed for run %s", run_id)


def execute_background_run(payload: dict[str, Any]) -> None:
    """Worker entry point — runs the graph to completion and records status.

    Uses graph.stream() rather than graph.invoke() purely so there's a point
    between LangGraph supersteps to check the cancel flag; the graph's own
    checkpointer persistence behavior is identical either way.

    When ``run_body.webhook`` is set (Dashboard reviewRunWebhook URL), POSTs a
    Platform-shaped completion payload after the run reaches a terminal status.
    """
    thread_id = payload["thread_id"]
    run_id = payload["run_id"]
    assistant_id = payload["assistant_id"]
    run_body = payload.get("run_body") or {}
    webhook_url = (run_body.get("webhook") or "").strip()
    store = get_thread_store()

    store.update_run(thread_id, run_id, status="running")
    logger.info("Background run %s started (thread %s)", run_id, thread_id)
    final_status = "error"
    final_error: str | None = None
    try:
        config = _merge_config(run_body, thread_id)
        graph = build_graph(assistant_id, with_checkpointer=True)
        cancelled = False
        for _ in graph.stream(run_body.get("input") or {}, config=config, stream_mode="updates"):
            if store.is_cancel_requested(thread_id, run_id):
                cancelled = True
                logger.info("Background run %s: cancel flag observed, stopping", run_id)
                break
    except Exception as exc:
        logger.exception("Background run %s failed", run_id)
        final_status = "error"
        final_error = str(exc)
        store.update_run(thread_id, run_id, status="error", error=str(exc))
        store.set_status(thread_id, "error")
    else:
        if cancelled:
            final_status = "cancelled"
            store.update_run(thread_id, run_id, status="cancelled")
            store.set_status(thread_id, "idle")
            logger.info("Background run %s cancelled", run_id)
        else:
            final_status = "success"
            store.update_run(thread_id, run_id, status="success")
            store.set_status(thread_id, "idle")
            logger.info("Background run %s completed", run_id)

    if webhook_url and final_status in ("success", "error", "cancelled"):
        _fire_run_webhook(
            webhook_url=webhook_url,
            thread_id=thread_id,
            run_id=run_id,
            assistant_id=assistant_id,
            status=final_status,
            error=final_error,
        )


# ─────────────────────────────────────────────────────────────────────────────
# Stream reconnect (GET /threads/{id}/runs/{id}/stream — SDK's join_stream)
# ─────────────────────────────────────────────────────────────────────────────

def reconnect_stream(
    thread_id: str,
    run_id: str,
    *,
    assistant_id: str | None = None,
    poll_seconds: float = _RECONNECT_POLL_SECONDS,
) -> Iterator[str]:
    """Best-effort reconnect for a run already in progress.

    Real LangGraph Platform buffers each run's actual event stream so a
    reconnecting client can replay everything from ``Last-Event-ID`` onward.
    We have no such buffer for a Fargate-dispatched run — the worker process
    checkpoints state, it doesn't publish a durable event log. So instead of
    replaying history, this polls ``get_thread_state`` and re-emits a
    ``values`` event whenever the state changes, until the run record hits a
    terminal status. This intentionally does not honor ``Last-Event-ID`` /
    exact-replay semantics — good enough for a monitoring UI reconnecting
    after a page refresh, not a faithful token-by-token replay.
    """
    from api.services.state import get_thread_state  # local import: avoid import cycle

    store = get_thread_store()
    run = store.get_run(thread_id, run_id)
    if run is None:
        yield format_error_event(f"Run not found: {run_id}")
        yield format_end_event()
        return

    yield format_metadata_event(run_id)

    last_values = _UNSET
    try:
        while True:
            try:
                state = get_thread_state(thread_id, assistant_id=assistant_id)
            except Exception as exc:
                yield format_error_event(str(exc))
                break

            values = state.get("values")
            if values != last_values:
                yield format_sse_event("values", values)
                last_values = values

            run = store.get_run(thread_id, run_id) or run
            status = run.get("status")
            if status in _TERMINAL_RUN_STATUSES:
                if status == "error" and run.get("error"):
                    yield format_error_event(run["error"])
                break
            time.sleep(poll_seconds)
    finally:
        yield format_end_event()
