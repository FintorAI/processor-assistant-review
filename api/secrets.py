"""Load secrets from AWS Secrets Manager into os.environ on Lambda cold start.

Real values are pushed out-of-band via `aws secretsmanager put-secret-value`
(see infra/deploy.sh) — never through CloudFormation, so they are never
visible in a synthesized template. This must run BEFORE disc_orch_agent (and
its tools) are imported: several tool modules read env vars like
LANGCHAIN_API_KEY at import time.
"""

from __future__ import annotations

import json
import logging
import os

logger = logging.getLogger(__name__)
_loaded = False


def load_secrets() -> None:
    global _loaded
    if _loaded:
        return

    secret_arn = os.environ.get("AGENT_SECRETS_ARN", "").strip()
    if not secret_arn:
        _loaded = True
        return

    try:
        import boto3

        client = boto3.client("secretsmanager")
        resp = client.get_secret_value(SecretId=secret_arn)
        payload = json.loads(resp["SecretString"])
        loaded_keys = []
        for key, value in payload.items():
            if key.startswith("_"):  # e.g. _cfn_placeholder seeded by CDK
                continue
            if value and not os.environ.get(key):
                os.environ[key] = str(value)
                loaded_keys.append(key)
        logger.info("Loaded %d secrets from Secrets Manager: %s", len(loaded_keys), sorted(loaded_keys))
    except Exception:
        logger.exception("Failed to load secrets from %s", secret_arn)
    finally:
        _loaded = True
