"""Maps LangGraph Platform assistant_id values to compiled review graphs.

Assistants match langgraph.json / Dashboard:
- review                 — full workflow (Fargate background + webhook)
- write_los_fields       — threadless /runs/wait
- write_los_collections  — threadless /runs/wait
- analyze_uw_conditions  — threadless /runs/wait
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

ASSISTANTS: dict[str, dict[str, str]] = {
    "review": {
        "assistant_id": "review",
        "graph_id": "review",
        "name": "Processor Review",
        "description": "Full processor submission review workflow",
    },
    "write_los_fields": {
        "assistant_id": "write_los_fields",
        "graph_id": "write_los_fields",
        "name": "Write LOS Fields",
        "description": "Stateless LOS field writes",
    },
    "write_los_collections": {
        "assistant_id": "write_los_collections",
        "graph_id": "write_los_collections",
        "name": "Write LOS Collections",
        "description": "Stateless VOD/VOL/contacts writes",
    },
    "analyze_uw_conditions": {
        "assistant_id": "analyze_uw_conditions",
        "graph_id": "analyze_uw_conditions",
        "name": "Analyze UW Conditions",
        "description": "Post-UW conditions triage",
    },
}

DEFAULT_ASSISTANT_ID = "review"

_MIN_RECURSION_LIMIT = 100_000


def list_assistants() -> list[dict[str, str]]:
    return list(ASSISTANTS.values())


def validate_assistant_id(assistant_id: str) -> str:
    if assistant_id not in ASSISTANTS:
        raise KeyError(f"Unknown assistant_id: {assistant_id}")
    return assistant_id


def _apply_recursion_limit(graph: Any) -> Any:
    try:
        cfg = getattr(graph, "config", None) or {}
        if isinstance(cfg, dict):
            cfg = dict(cfg)
            cfg["recursion_limit"] = max(
                int(cfg.get("recursion_limit") or 0), _MIN_RECURSION_LIMIT
            )
            graph.config = cfg
    except Exception:
        pass
    return graph


@lru_cache(maxsize=4)
def _stateless_agent(assistant_id: str) -> Any:
    if assistant_id == "review":
        from output.proc_agent import create_agent

        return _apply_recursion_limit(create_agent())
    if assistant_id == "write_los_fields":
        from output.write_graphs import field_write_graph

        return field_write_graph
    if assistant_id == "write_los_collections":
        from output.write_graphs import collection_write_graph

        return collection_write_graph
    if assistant_id == "analyze_uw_conditions":
        from output.analyze_uw_conditions import graph as analyze_graph

        return analyze_graph
    raise KeyError(f"Unknown assistant_id: {assistant_id}")


@lru_cache(maxsize=2)
def _checkpointed_review() -> Any:
    from api.checkpointer import get_checkpointer
    from output.proc_agent import create_agent

    checkpointer = get_checkpointer()
    if checkpointer is None:
        return _stateless_agent("review")
    return _apply_recursion_limit(create_agent(checkpointer=checkpointer))


def build_graph(assistant_id: str, *, with_checkpointer: bool = False) -> Any:
    validate_assistant_id(assistant_id)
    if assistant_id == "review" and with_checkpointer:
        return _checkpointed_review()
    # Write/analyze helpers are short threadless runs — no DynamoDB checkpointer.
    return _stateless_agent(assistant_id)
