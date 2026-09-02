"""LangGraph checkpointer selection for the AWS serverless API.

- ``CHECKPOINT_TABLE_NAME`` set  -> DynamoDB (Lambda / AWS)
  - optional ``CHECKPOINT_S3_BUCKET`` -> offload payloads >350KB to S3
    (required for full disclosure runs — state grows past DynamoDB's 400KB
    item limit around mid-workflow)
  - compression enabled whenever DynamoDB is used
- ``CHECKPOINT_IN_MEMORY=true``  -> InMemorySaver (local API tests)
- otherwise                      -> None (stateless; LangGraph Cloud and
  ``langgraph dev`` provide their own persistence, so this module is a no-op
  in those environments)
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from langgraph.checkpoint.base import BaseCheckpointSaver


@lru_cache(maxsize=1)
def get_checkpointer() -> "BaseCheckpointSaver | None":
    table_name = os.environ.get("CHECKPOINT_TABLE_NAME", "").strip()
    if table_name:
        from langgraph_checkpoint_aws import DynamoDBSaver

        region = os.environ.get("AWS_REGION", os.environ.get("AWS_DEFAULT_REGION", "us-east-2"))
        s3_bucket = os.environ.get("CHECKPOINT_S3_BUCKET", "").strip()
        s3_offload = None
        if s3_bucket:
            s3_offload = {
                "bucket_name": s3_bucket,
                "key_prefix": os.environ.get("CHECKPOINT_S3_PREFIX", "checkpoints").strip()
                or "checkpoints",
            }
        return DynamoDBSaver(
            table_name=table_name,
            region_name=region,
            enable_checkpoint_compression=True,
            s3_offload_config=s3_offload,
        )

    if os.environ.get("CHECKPOINT_IN_MEMORY", "").strip().lower() in ("1", "true", "yes"):
        from langgraph.checkpoint.memory import InMemorySaver

        return InMemorySaver()

    return None
