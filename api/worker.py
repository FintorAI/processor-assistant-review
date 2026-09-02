"""Fargate worker entrypoint for long-running background review runs.

Invoked as ``python -m api.worker`` with ECS container overrides:

- ``WORKER_RUN_THREAD_ID`` — thread whose run to execute
- ``WORKER_RUN_ID`` — run record id (payload lives in DynamoDB)

The worker loads secrets, reconstructs the run payload from the thread store,
and calls :func:`api.services.runs.execute_background_run`. A wall-clock
guard (``REVIEW_MAX_RUN_SECONDS``, default 3h) marks the run ``error`` and
exits if the agent loop overruns — Fargate has no hard timeout of its own.
"""

from __future__ import annotations

import logging
import os
import sys
import threading

# Secrets MUST load before anything that imports disc_orch_agent / tools —
# several modules capture env vars (e.g. LANGCHAIN_API_KEY) at import time.
from api.secrets import load_secrets

load_secrets()

from api.services.runs import execute_background_run  # noqa: E402
from api.thread_store import get_thread_store  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
    force=True,
)
logger = logging.getLogger("api.worker")

DEFAULT_MAX_RUN_SECONDS = 3 * 60 * 60  # 3 hours


def main() -> int:
    thread_id = os.environ.get("WORKER_RUN_THREAD_ID", "").strip()
    run_id = os.environ.get("WORKER_RUN_ID", "").strip()
    if not thread_id or not run_id:
        logger.error(
            "Missing WORKER_RUN_THREAD_ID / WORKER_RUN_ID "
            "(got thread=%r run=%r)",
            thread_id,
            run_id,
        )
        return 2

    try:
        max_seconds = int(os.environ.get("REVIEW_MAX_RUN_SECONDS", DEFAULT_MAX_RUN_SECONDS))
    except ValueError:
        max_seconds = DEFAULT_MAX_RUN_SECONDS

    store = get_thread_store()
    stored = store.get_run_payload(thread_id, run_id)
    if not stored:
        logger.error("No input_payload for thread=%s run=%s", thread_id, run_id)
        store.update_run(
            thread_id,
            run_id,
            status="error",
            error="Worker could not load run input_payload from thread store",
        )
        store.set_status(thread_id, "error")
        return 1

    payload = {
        "thread_id": thread_id,
        "run_id": run_id,
        "assistant_id": stored["assistant_id"],
        "run_body": stored.get("run_body") or {},
    }

    def _wall_clock_kill() -> None:
        msg = f"Wall-clock limit of {max_seconds}s exceeded"
        logger.error("Background run %s: %s — forcing exit", run_id, msg)
        try:
            store.update_run(thread_id, run_id, status="error", error=msg)
            store.set_status(thread_id, "error")
        finally:
            # Hard exit: graph.invoke may be stuck in a non-interruptible call.
            os._exit(1)

    timer = threading.Timer(max_seconds, _wall_clock_kill)
    timer.daemon = True
    timer.start()
    logger.info(
        "Worker starting run %s (thread %s, max %ss)",
        run_id,
        thread_id,
        max_seconds,
    )
    try:
        execute_background_run(payload)
    finally:
        timer.cancel()

    final = store.get_run(thread_id, run_id) or {}
    status = final.get("status", "unknown")
    logger.info("Worker finished run %s with status=%s", run_id, status)
    return 0 if status == "success" else 1


if __name__ == "__main__":
    sys.exit(main())
