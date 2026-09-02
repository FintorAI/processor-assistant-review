"""Pydantic models for LangGraph Platform-compatible requests.

``extra="allow"`` keeps us liberal in what we accept: real Platform clients
send fields we don't act on (webhook, multitask_strategy, ...), and rejecting
them would break run_cloud.py / the frontend for no benefit.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class RunCreate(BaseModel):
    model_config = ConfigDict(extra="allow")

    assistant_id: str
    input: dict[str, Any] | None = Field(default_factory=dict)
    config: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None
    stream_mode: list[str] | str | None = None


class ThreadCreate(BaseModel):
    model_config = ConfigDict(extra="allow")

    metadata: dict[str, Any] | None = None


class ThreadImport(BaseModel):
    """Body for ``POST /threads/import`` (cross-deployment thread migration)."""

    model_config = ConfigDict(extra="allow")

    thread_id: str
    assistant_id: str | None = None
    metadata: dict[str, Any] | None = None
    status: str = "idle"
    values: dict[str, Any] | None = None
    # Re-import over an already-present thread (default: skip to stay idempotent).
    overwrite: bool = False
    # Normally inferred; set only if update_state reports an ambiguous entry node.
    as_node: str | None = None


class ThreadSearch(BaseModel):
    model_config = ConfigDict(extra="allow")

    metadata: dict[str, Any] | None = None
    status: str | None = None
    limit: int = 10
    offset: int = 0


class AssistantSearch(BaseModel):
    model_config = ConfigDict(extra="allow")

    metadata: dict[str, Any] | None = None
    limit: int = 10
    offset: int = 0


class ThreadHistoryRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    limit: int = 10
    before: str | dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None
    # `checkpoint` selects a subgraph namespace on real Platform deployments;
    # DiscOrch's graph is flat, so this is accepted but unused.
    checkpoint: dict[str, Any] | None = None
