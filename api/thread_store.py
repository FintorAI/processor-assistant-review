"""Thread metadata + run record store (LangGraph Platform compatibility).

Stores lightweight records in the same DynamoDB table as the LangGraph
checkpointer, under distinct keys:

- ``PK=THREAD#<thread_id>, SK=METADATA``      thread record
- ``PK=THREAD#<thread_id>, SK=RUN#<run_id>``  run record (for background runs
  that clients poll via GET /threads/{id}/runs/{run_id})

Falls back to an in-memory store when CHECKPOINT_TABLE_NAME is unset (local dev).
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from typing import Any

_THREAD_FIELDS = ("thread_id", "created_at", "updated_at", "metadata", "status")
_RUN_FIELDS = (
    "run_id",
    "thread_id",
    "assistant_id",
    "status",
    "created_at",
    "updated_at",
    "error",
    "metadata",
    "cancel_requested",
)

# Terminal statuses — a cancel request against a run already in one of these
# is a no-op (nothing left to stop), matching LangGraph Platform semantics.
_TERMINAL_RUN_STATUSES = frozenset({"success", "error", "cancelled"})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _public(record: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    return {k: record.get(k) for k in fields if k in record}


class ThreadStore:
    # ── threads ──
    def create(self, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        raise NotImplementedError

    def put_thread(
        self,
        thread_id: str,
        *,
        metadata: dict[str, Any] | None = None,
        status: str = "idle",
    ) -> dict[str, Any]:
        """Create/overwrite a thread record with a caller-supplied ``thread_id``.

        Unlike ``create`` (which mints a fresh uuid), this preserves the id so a
        thread migrated from another deployment keeps the same identifier the
        dashboard already has stored. Used by ``POST /threads/import``.
        """
        raise NotImplementedError

    def get(self, thread_id: str) -> dict[str, Any] | None:
        raise NotImplementedError

    def set_status(self, thread_id: str, status: str) -> None:
        raise NotImplementedError

    def record_run(self, thread_id: str, *, assistant_id: str, metadata: dict[str, Any] | None = None) -> None:
        raise NotImplementedError

    def search(
        self,
        metadata: dict[str, Any] | None = None,
        *,
        status: str | None = None,
        limit: int = 10,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        raise NotImplementedError

    # ── runs ──
    def create_run(
        self,
        thread_id: str,
        *,
        assistant_id: str,
        metadata: dict[str, Any] | None = None,
        input_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        raise NotImplementedError

    def get_run(self, thread_id: str, run_id: str) -> dict[str, Any] | None:
        raise NotImplementedError

    def get_run_payload(self, thread_id: str, run_id: str) -> dict[str, Any] | None:
        """Return persisted worker payload ``{assistant_id, run_body}`` (or None)."""
        raise NotImplementedError

    def list_runs(self, thread_id: str) -> list[dict[str, Any]]:
        raise NotImplementedError

    def update_run(self, thread_id: str, run_id: str, *, status: str, error: str | None = None) -> None:
        raise NotImplementedError

    def set_run_task_arn(self, thread_id: str, run_id: str, task_arn: str) -> None:
        """Record the ECS task ARN backing a Fargate-dispatched run, so a
        cancel request can ``ecs.stop_task`` it instead of only setting a
        cooperative flag the worker checks on its own schedule. Internal —
        deliberately not in ``_RUN_FIELDS`` (not exposed to API clients)."""
        raise NotImplementedError

    def get_run_task_arn(self, thread_id: str, run_id: str) -> str | None:
        raise NotImplementedError

    def request_cancel(self, thread_id: str, run_id: str) -> dict[str, Any] | None:
        """Flag a run for cooperative cancellation. Returns the updated public
        run record, or ``None`` if the run doesn't exist."""
        raise NotImplementedError

    def is_cancel_requested(self, thread_id: str, run_id: str) -> bool:
        raise NotImplementedError


def _matches_metadata(record: dict[str, Any], wanted: dict[str, Any] | None) -> bool:
    if not wanted:
        return True
    have = record.get("metadata") or {}
    return all(have.get(k) == v for k, v in wanted.items())


class InMemoryThreadStore(ThreadStore):
    def __init__(self) -> None:
        self._threads: dict[str, dict[str, Any]] = {}
        self._runs: dict[tuple[str, str], dict[str, Any]] = {}

    def create(self, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        thread_id = str(uuid.uuid4())
        record = {
            "thread_id": thread_id,
            "created_at": _now(),
            "updated_at": _now(),
            "metadata": metadata or {},
            "status": "idle",
        }
        self._threads[thread_id] = record
        return _public(record, _THREAD_FIELDS)

    def put_thread(self, thread_id, *, metadata=None, status="idle"):
        existing = self._threads.get(thread_id)
        record = {
            "thread_id": thread_id,
            "created_at": (existing or {}).get("created_at") or _now(),
            "updated_at": _now(),
            "metadata": metadata or {},
            "status": status,
        }
        self._threads[thread_id] = record
        return _public(record, _THREAD_FIELDS)

    def get(self, thread_id: str) -> dict[str, Any] | None:
        record = self._threads.get(thread_id)
        return _public(record, _THREAD_FIELDS) if record else None

    def set_status(self, thread_id: str, status: str) -> None:
        record = self._threads.get(thread_id)
        if record:
            record["status"] = status
            record["updated_at"] = _now()

    def record_run(self, thread_id: str, *, assistant_id: str, metadata: dict[str, Any] | None = None) -> None:
        record = self._threads.get(thread_id)
        if not record:
            return
        merged = dict(record.get("metadata") or {})
        merged["assistant_id"] = assistant_id
        if metadata:
            merged.update(metadata)
        record["metadata"] = merged
        record["status"] = "busy"
        record["updated_at"] = _now()

    def search(self, metadata=None, *, status=None, limit=10, offset=0):
        records = [r for r in self._threads.values() if _matches_metadata(r, metadata)]
        if status:
            records = [r for r in records if r.get("status") == status]
        records.sort(key=lambda r: r.get("created_at", ""), reverse=True)
        return [_public(r, _THREAD_FIELDS) for r in records[offset : offset + limit]]

    def create_run(self, thread_id, *, assistant_id, metadata=None, input_payload=None):
        run_id = str(uuid.uuid4())
        record = {
            "run_id": run_id,
            "thread_id": thread_id,
            "assistant_id": assistant_id,
            "status": "pending",
            "created_at": _now(),
            "updated_at": _now(),
            "error": None,
            "metadata": metadata or {},
            "cancel_requested": False,
            # Stored for the Fargate worker; stripped from public get_run views.
            "input_payload": input_payload or {},
        }
        self._runs[(thread_id, run_id)] = record
        return _public(record, _RUN_FIELDS)

    def get_run(self, thread_id, run_id):
        record = self._runs.get((thread_id, run_id))
        return _public(record, _RUN_FIELDS) if record else None

    def get_run_payload(self, thread_id, run_id):
        record = self._runs.get((thread_id, run_id))
        if not record:
            return None
        payload = record.get("input_payload") or {}
        return {
            "assistant_id": payload.get("assistant_id") or record.get("assistant_id"),
            "run_body": payload.get("run_body") or {},
        }

    def list_runs(self, thread_id):
        records = [r for (tid, _), r in self._runs.items() if tid == thread_id]
        records.sort(key=lambda r: r.get("created_at", ""), reverse=True)
        return [_public(r, _RUN_FIELDS) for r in records]

    def update_run(self, thread_id, run_id, *, status, error=None):
        record = self._runs.get((thread_id, run_id))
        if record:
            record["status"] = status
            record["error"] = error
            record["updated_at"] = _now()

    def set_run_task_arn(self, thread_id, run_id, task_arn):
        record = self._runs.get((thread_id, run_id))
        if record:
            record["task_arn"] = task_arn
            record["updated_at"] = _now()

    def get_run_task_arn(self, thread_id, run_id):
        record = self._runs.get((thread_id, run_id))
        return record.get("task_arn") if record else None

    def request_cancel(self, thread_id, run_id):
        record = self._runs.get((thread_id, run_id))
        if not record:
            return None
        if record.get("status") not in _TERMINAL_RUN_STATUSES:
            record["cancel_requested"] = True
            record["updated_at"] = _now()
        return _public(record, _RUN_FIELDS)

    def is_cancel_requested(self, thread_id, run_id):
        record = self._runs.get((thread_id, run_id))
        return bool(record and record.get("cancel_requested"))


class DynamoDBThreadStore(ThreadStore):
    def __init__(self, table_name: str, *, region_name: str | None = None) -> None:
        import boto3

        self._table = boto3.resource("dynamodb", region_name=region_name).Table(table_name)

    @staticmethod
    def _thread_key(thread_id: str) -> dict[str, str]:
        return {"PK": f"THREAD#{thread_id}", "SK": "METADATA"}

    @staticmethod
    def _run_key(thread_id: str, run_id: str) -> dict[str, str]:
        return {"PK": f"THREAD#{thread_id}", "SK": f"RUN#{run_id}"}

    def create(self, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        thread_id = str(uuid.uuid4())
        record = {
            **self._thread_key(thread_id),
            "thread_id": thread_id,
            "created_at": _now(),
            "updated_at": _now(),
            "metadata": metadata or {},
            "status": "idle",
        }
        self._table.put_item(Item=record)
        return _public(record, _THREAD_FIELDS)

    def put_thread(self, thread_id, *, metadata=None, status="idle"):
        existing = self.get(thread_id)
        record = {
            **self._thread_key(thread_id),
            "thread_id": thread_id,
            "created_at": (existing or {}).get("created_at") or _now(),
            "updated_at": _now(),
            "metadata": metadata or {},
            "status": status,
        }
        self._table.put_item(Item=record)
        return _public(record, _THREAD_FIELDS)

    def get(self, thread_id: str) -> dict[str, Any] | None:
        resp = self._table.get_item(Key=self._thread_key(thread_id))
        item = resp.get("Item")
        return _public(item, _THREAD_FIELDS) if item else None

    def set_status(self, thread_id: str, status: str) -> None:
        self._table.update_item(
            Key=self._thread_key(thread_id),
            UpdateExpression="SET #status = :status, updated_at = :now",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={":status": status, ":now": _now()},
        )

    def record_run(self, thread_id: str, *, assistant_id: str, metadata: dict[str, Any] | None = None) -> None:
        record = self.get(thread_id)
        if not record:
            return
        merged = dict(record.get("metadata") or {})
        merged["assistant_id"] = assistant_id
        if metadata:
            merged.update(metadata)
        self._table.update_item(
            Key=self._thread_key(thread_id),
            UpdateExpression="SET metadata = :metadata, #status = :status, updated_at = :now",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={":metadata": merged, ":status": "busy", ":now": _now()},
        )

    def search(self, metadata=None, *, status=None, limit=10, offset=0):
        # Table volume is one METADATA item per workflow run, so a filtered
        # scan is acceptable here (checkpoints have different SK values and
        # are excluded by the filter before they reach us).
        from boto3.dynamodb.conditions import Attr

        records: list[dict[str, Any]] = []
        scan_kwargs: dict[str, Any] = {"FilterExpression": Attr("SK").eq("METADATA")}
        while True:
            resp = self._table.scan(**scan_kwargs)
            records.extend(resp.get("Items", []))
            if "LastEvaluatedKey" not in resp:
                break
            scan_kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]

        records = [r for r in records if _matches_metadata(r, metadata)]
        if status:
            records = [r for r in records if r.get("status") == status]
        records.sort(key=lambda r: r.get("created_at", ""), reverse=True)
        return [_public(r, _THREAD_FIELDS) for r in records[offset : offset + limit]]

    def create_run(self, thread_id, *, assistant_id, metadata=None, input_payload=None):
        run_id = str(uuid.uuid4())
        record = {
            **self._run_key(thread_id, run_id),
            "run_id": run_id,
            "thread_id": thread_id,
            "assistant_id": assistant_id,
            "status": "pending",
            "created_at": _now(),
            "updated_at": _now(),
            "error": None,
            "metadata": metadata or {},
            "cancel_requested": False,
            # Stored for the Fargate worker; stripped from public get_run views.
            "input_payload": input_payload or {},
        }
        self._table.put_item(Item=record)
        return _public(record, _RUN_FIELDS)

    def get_run(self, thread_id, run_id):
        resp = self._table.get_item(Key=self._run_key(thread_id, run_id))
        item = resp.get("Item")
        return _public(item, _RUN_FIELDS) if item else None

    def get_run_payload(self, thread_id, run_id):
        resp = self._table.get_item(Key=self._run_key(thread_id, run_id))
        item = resp.get("Item")
        if not item:
            return None
        payload = item.get("input_payload") or {}
        return {
            "assistant_id": payload.get("assistant_id") or item.get("assistant_id"),
            "run_body": payload.get("run_body") or {},
        }

    def list_runs(self, thread_id):
        from boto3.dynamodb.conditions import Key

        resp = self._table.query(
            KeyConditionExpression=Key("PK").eq(f"THREAD#{thread_id}") & Key("SK").begins_with("RUN#"),
        )
        records = resp.get("Items", [])
        records.sort(key=lambda r: r.get("created_at", ""), reverse=True)
        return [_public(r, _RUN_FIELDS) for r in records]

    def update_run(self, thread_id, run_id, *, status, error=None):
        self._table.update_item(
            Key=self._run_key(thread_id, run_id),
            UpdateExpression="SET #status = :status, #error = :error, updated_at = :now",
            ExpressionAttributeNames={"#status": "status", "#error": "error"},
            ExpressionAttributeValues={":status": status, ":error": error, ":now": _now()},
        )

    def set_run_task_arn(self, thread_id, run_id, task_arn):
        self._table.update_item(
            Key=self._run_key(thread_id, run_id),
            UpdateExpression="SET task_arn = :arn, updated_at = :now",
            ExpressionAttributeValues={":arn": task_arn, ":now": _now()},
        )

    def get_run_task_arn(self, thread_id, run_id):
        resp = self._table.get_item(
            Key=self._run_key(thread_id, run_id),
            ProjectionExpression="task_arn",
        )
        item = resp.get("Item")
        return item.get("task_arn") if item else None

    def request_cancel(self, thread_id, run_id):
        record = self.get_run(thread_id, run_id)
        if not record:
            return None
        if record.get("status") not in _TERMINAL_RUN_STATUSES:
            self._table.update_item(
                Key=self._run_key(thread_id, run_id),
                UpdateExpression="SET cancel_requested = :true, updated_at = :now",
                ExpressionAttributeValues={":true": True, ":now": _now()},
            )
            record = self.get_run(thread_id, run_id)
        return record

    def is_cancel_requested(self, thread_id, run_id):
        resp = self._table.get_item(
            Key=self._run_key(thread_id, run_id),
            ProjectionExpression="cancel_requested",
        )
        item = resp.get("Item")
        return bool(item and item.get("cancel_requested"))


_thread_store: ThreadStore | None = None


def get_thread_store() -> ThreadStore:
    global _thread_store
    if _thread_store is not None:
        return _thread_store

    table_name = os.environ.get("CHECKPOINT_TABLE_NAME", "").strip()
    if table_name:
        region = os.environ.get("AWS_REGION", os.environ.get("AWS_DEFAULT_REGION", "us-east-2"))
        _thread_store = DynamoDBThreadStore(table_name, region_name=region)
    else:
        _thread_store = InMemoryThreadStore()
    return _thread_store
