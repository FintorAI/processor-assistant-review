"""Thread state helpers for LangGraph Platform compatibility.

Wire shapes here must match ``langgraph_sdk.schema.ThreadState`` /
``Checkpoint`` / ``ThreadTask`` — the dashboard's ``@langchain/langgraph-sdk``
client parses responses against those TypedDicts, which use ``checkpoint`` /
``parent_checkpoint`` (a ``{thread_id, checkpoint_ns, checkpoint_id,
checkpoint_map}`` object), not the raw LangGraph Python ``config`` /
``parent_config`` RunnableConfig shape.
"""

from __future__ import annotations

from typing import Any

from api.checkpointer import get_checkpointer
from api.registry import DEFAULT_ASSISTANT_ID, build_graph, validate_assistant_id
from api.thread_store import get_thread_store


def _config_to_checkpoint(config: Any) -> dict[str, Any] | None:
    if not config:
        return None
    configurable = (config or {}).get("configurable") or {}
    thread_id = configurable.get("thread_id")
    if not thread_id:
        return None
    return {
        "thread_id": thread_id,
        "checkpoint_ns": configurable.get("checkpoint_ns", ""),
        "checkpoint_id": configurable.get("checkpoint_id"),
        "checkpoint_map": configurable.get("checkpoint_map"),
    }


def _serialize_interrupt(interrupt: Any) -> dict[str, Any]:
    if isinstance(interrupt, dict):
        return interrupt
    return {"value": getattr(interrupt, "value", None), "id": getattr(interrupt, "id", None)}


def _serialize_task(task: Any) -> dict[str, Any]:
    if isinstance(task, dict):
        return task
    state = getattr(task, "state", None)
    if state is not None and not isinstance(state, dict):
        # `state` is either a RunnableConfig (nested graph pointer) or a full
        # StateSnapshot (rare) — normalize both to the checkpoint pointer the
        # SDK expects rather than recursively re-serializing a full snapshot.
        state = _config_to_checkpoint(state) or _serialize_snapshot(state)
    return {
        "id": getattr(task, "id", None),
        "name": getattr(task, "name", None),
        "error": str(task_error) if (task_error := getattr(task, "error", None)) else None,
        "interrupts": [_serialize_interrupt(i) for i in (getattr(task, "interrupts", None) or [])],
        "checkpoint": state if isinstance(state, dict) and "checkpoint_id" in state else None,
        "state": state,
        "result": getattr(task, "result", None),
    }


def _serialize_snapshot(snapshot: Any) -> dict[str, Any]:
    if hasattr(snapshot, "model_dump"):
        return snapshot.model_dump()
    config = getattr(snapshot, "config", None)
    parent_config = getattr(snapshot, "parent_config", None)
    return {
        "values": getattr(snapshot, "values", {}),
        "next": list(getattr(snapshot, "next", []) or []),
        "checkpoint": _config_to_checkpoint(config),
        "metadata": getattr(snapshot, "metadata", {}) or {},
        "created_at": getattr(snapshot, "created_at", None),
        "parent_checkpoint": _config_to_checkpoint(parent_config),
        "tasks": [_serialize_task(t) for t in (getattr(snapshot, "tasks", None) or [])],
        "interrupts": [
            _serialize_interrupt(i) for i in (getattr(snapshot, "interrupts", None) or [])
        ],
    }


def _assistant_for_thread(thread_id: str, assistant_id: str | None) -> str:
    if assistant_id:
        return validate_assistant_id(assistant_id)
    thread = get_thread_store().get(thread_id)
    if not thread:
        raise KeyError(f"Thread not found: {thread_id}")
    stored = (thread.get("metadata") or {}).get("assistant_id")
    if isinstance(stored, str) and stored.strip():
        return validate_assistant_id(stored.strip())
    return DEFAULT_ASSISTANT_ID


def get_thread_state(thread_id: str, *, assistant_id: str | None = None) -> dict[str, Any]:
    aid = _assistant_for_thread(thread_id, assistant_id)
    if get_checkpointer() is None:
        raise RuntimeError("Thread state requires a configured checkpointer (CHECKPOINT_TABLE_NAME)")
    graph = build_graph(aid, with_checkpointer=True)
    snapshot = graph.get_state({"configurable": {"thread_id": thread_id}})
    return _serialize_snapshot(snapshot)


def _messages_from_wire(values: dict[str, Any]) -> dict[str, Any]:
    """Rebuild ``messages`` from the Platform's JSON dicts.

    The default dict coercion keeps only content/tool_calls and moves every other
    field (``usage_metadata``, ``invalid_tool_calls``, ...) into ``additional_kwargs``.
    """
    from langchain_core.messages import messages_from_dict

    raw = values.get("messages")
    if not isinstance(raw, list) or not all(isinstance(m, dict) and "type" in m for m in raw):
        return values
    return {**values, "messages": messages_from_dict([{"type": m["type"], "data": m} for m in raw])}


def _bypass_reducers(graph: Any, values: dict[str, Any]) -> dict[str, Any]:
    """Wrap values of reducer channels in ``Overwrite`` so they are stored as given.

    Reducers also normalise what they merge (``dedupe_flags`` adds ``substep_name``,
    ``truncate_messages`` trims), so an imported value would otherwise differ from the
    source. Only channels that start with a value (e.g. ``[]``) run their reducer on an
    import, which always writes into an empty thread; a channel that starts empty stores
    its first write raw, ``Overwrite`` wrapper included. Plain last-value channels store
    as given anyway.
    """
    from langgraph.channels.binop import BinaryOperatorAggregate
    from langgraph.types import Overwrite

    reducers = {
        k for k, ch in graph.channels.items()
        if isinstance(ch, BinaryOperatorAggregate) and ch.is_available()
    }
    return {k: Overwrite(v) if k in reducers else v for k, v in values.items()}


def _as_node_for_next(graph: Any, values: dict[str, Any], target_next: list[str]) -> str:
    """The node to write ``values`` as so the thread's ``next`` equals ``target_next``.

    ``update_state`` without ``as_node`` leaves the thread pending at the graph's first
    node. Which node yields a given ``next`` depends on the graph's edges and the state
    (e.g. tool calls on the last AI message), so each candidate is tried on an
    in-memory copy of the graph; nothing is written to the real checkpointer here.
    """
    from langgraph.checkpoint.memory import InMemorySaver

    probe = graph.copy(update={"checkpointer": InMemorySaver()})
    want = sorted(target_next)
    for node in probe.get_graph().nodes:
        if node.startswith("__"):
            continue
        cfg = {"configurable": {"thread_id": f"probe-{node}"}}
        try:
            probe.update_state(cfg, values, as_node=node)
        except Exception:  # noqa: BLE001 - not a valid writer for this state
            continue
        if sorted(probe.get_state(cfg).next) == want:
            return node
    raise ValueError(f"no node reproduces next={target_next}")


def import_thread_state(
    thread_id: str,
    values: dict[str, Any],
    *,
    assistant_id: str | None = None,
    as_node: str | None = None,
    next_nodes: list[str] | None = None,
    replace: bool = False,
) -> dict[str, Any]:
    """Write a migrated head state into the checkpointer.

    Backs ``POST /threads/import`` — transfers a LangGraph Cloud review thread's
    head ``values`` into this deployment's DynamoDB+S3 checkpointer so that
    ``GET /threads/{id}/state`` returns the prior run for display and the thread
    remains resumable. ``graph.update_state`` writes through the configured
    checkpointer, so payloads over the DynamoDB item limit auto-offload to S3
    (see ``api.checkpointer``).

    ``next_nodes`` is the source thread's ``next``; the write is made as the node that
    reproduces it. Reducer channels are written with ``Overwrite`` so values are stored
    exactly as the source had them. ``replace`` deletes the thread's existing checkpoints
    first, so the imported state has no history from earlier imports.
    """
    aid = _assistant_for_thread(thread_id, assistant_id)
    checkpointer = get_checkpointer()
    if checkpointer is None:
        raise RuntimeError(
            "Thread import requires a configured checkpointer (CHECKPOINT_TABLE_NAME)"
        )
    graph = build_graph(aid, with_checkpointer=True)
    values = _bypass_reducers(graph, _messages_from_wire(values))
    if as_node is None and next_nodes is not None:
        as_node = _as_node_for_next(graph, values, next_nodes)
    if replace:
        checkpointer.delete_thread(thread_id)
    config: dict[str, Any] = {"configurable": {"thread_id": thread_id}}
    graph.update_state(config, values, as_node=as_node)
    return _serialize_snapshot(graph.get_state(config))


def get_thread_history(
    thread_id: str,
    *,
    assistant_id: str | None = None,
    limit: int = 10,
    before: str | None = None,
) -> list[dict[str, Any]]:
    """Checkpoint history, newest first — backs ``GET /threads/{id}/history``.

    Mirrors LangGraph Platform's history endpoint so the dashboard's
    ``useStream`` can backfill prior state on mount without any client change.
    ``before`` is a checkpoint id (as returned in a prior entry's
    ``checkpoint.checkpoint_id``), used for pagination.
    """
    aid = _assistant_for_thread(thread_id, assistant_id)
    if get_checkpointer() is None:
        raise RuntimeError("Thread history requires a configured checkpointer (CHECKPOINT_TABLE_NAME)")
    graph = build_graph(aid, with_checkpointer=True)
    config: dict[str, Any] = {"configurable": {"thread_id": thread_id}}
    before_config = (
        {"configurable": {"thread_id": thread_id, "checkpoint_id": before}} if before else None
    )
    snapshots = graph.get_state_history(config, before=before_config, limit=limit)
    return [_serialize_snapshot(snapshot) for snapshot in snapshots]
