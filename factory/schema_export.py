"""Export Encompass field definitions (format + dropdown options) for the dashboard.

Reads every LOS field registered in ``output/config/fields_config.json``, fetches
its live definition from the Encompass schema APIs, and writes
``output/config/field_writes_config.json`` — the contract the dashboard's Field
Writes tab uses to pick input controls:

    format X            -> checkbox (value "X" / blank)
    format YN           -> toggle   (fieldWriter accepts "True"/"False", reads back Y/N)
    format DROPDOWNLIST -> select   (options list)
    format DATE         -> date picker (ISO yyyy-MM-dd for custom fields)
    format STRING + big maxLength -> textarea

Endpoints (both read-only):
    GET /encompass/v3/schemas/loan/standardFields?ids=...   (standard fields)
    GET /encompass/v3/settings/loan/customFields            (CX.* / CUST* fields)

Usage:
    python3.11 -m factory export-field-schema --env Prod

Requires Encompass credentials in .env (same as the runtime agent). This is a
separate command from factory-reset because it needs network access; re-run it
whenever fields are added to the YAML definitions.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import urllib.parse
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Field IDs the agent writes but does not read (not in fields_config los_fields).
# Keep in sync with tools' _write_fields targets that lack a los_fields_read entry.
EXTRA_WRITE_TARGETS = [
    "CX.KM.SUBMISSION.NOTES",
]


def _is_custom(field_id: str) -> bool:
    up = field_id.upper()
    return up.startswith("CX.") or up.startswith("CUST")


def _get_token(env: str) -> tuple[str, str]:
    """Authenticate against Encompass directly and return (base_url, token).

    Self-contained (no copilotagent dependency — that package is only available
    in the runtime venv, not to the factory CLI). Same credential scheme as
    scripts/test_field_rw.py: PROD_/TEST_-prefixed env vars win, unprefixed are
    the fallback; password+impersonation flow when username/password are set,
    client-credentials otherwise.
    """
    import requests

    prefix = f"{env.upper()}_"

    def _var(name: str) -> str | None:
        return os.getenv(f"{prefix}{name}") or os.getenv(name)

    base = (_var("ENCOMPASS_API_BASE_URL") or "https://api.elliemae.com").rstrip("/")
    client_id = _var("ENCOMPASS_CLIENT_ID")
    client_secret = _var("ENCOMPASS_CLIENT_SECRET")
    instance_id = _var("ENCOMPASS_INSTANCE_ID")
    username = _var("ENCOMPASS_USERNAME")
    password = _var("ENCOMPASS_PASSWORD")
    subject_user_id = _var("ENCOMPASS_SUBJECT_USER_ID")
    if not all([client_id, client_secret, instance_id]):
        raise RuntimeError(f"Missing Encompass credentials for env={env} (check .env)")

    token_url = f"{base}/oauth2/v1/token"
    if username and password:
        r = requests.post(token_url, data={
            "grant_type": "password",
            "username": f"{username}@encompass:{instance_id}",
            "password": password,
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": "lp",
        }, timeout=30)
        r.raise_for_status()
        actor_token = r.json()["access_token"]
        r = requests.post(token_url, data={
            "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
            "actor_token_type": "urn:ietf:params:oauth:token-type:access_token",
            "subject_user_id": subject_user_id,
            "actor_token": actor_token,
            "scope": "lp",
            "client_id": client_id,
            "client_secret": client_secret,
        }, timeout=30)
        r.raise_for_status()
        return base, r.json()["access_token"]

    r = requests.post(token_url, data={
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
        "instance_id": instance_id,
        "scope": "lp",
    }, timeout=30)
    r.raise_for_status()
    return base, r.json()["access_token"]


def _normalize_options(options) -> list[dict]:
    """standardFields options are {value,text} dicts; customFields are strings."""
    normalized = []
    for o in options or []:
        if isinstance(o, dict):
            normalized.append({"value": o.get("value"), "text": o.get("text") or o.get("value")})
        else:
            normalized.append({"value": o, "text": o})
    return normalized


def export_field_schema(env: str = "Prod", output_dir: str | None = None) -> dict:
    """Fetch field definitions and write field_writes_config.json.

    Returns a results dict: {"success": bool, "path": str, "found": int, "missing": [ids]}.
    """
    import requests

    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(PROJECT_ROOT, ".env"))
    except Exception:
        # dotenv may be absent for the CLI python — fall back to a tiny parser.
        env_path = os.path.join(PROJECT_ROOT, ".env")
        if os.path.isfile(env_path):
            with open(env_path) as fh:
                for line in fh:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, _, v = line.partition("=")
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

    output_dir = output_dir or os.path.join(PROJECT_ROOT, "output")
    fields_config_path = os.path.join(output_dir, "config", "fields_config.json")
    with open(fields_config_path) as f:
        fields_config = json.load(f)

    registered = {f["field_id"]: f for f in fields_config.get("los_fields", [])}
    all_ids = list(registered.keys()) + [
        fid for fid in EXTRA_WRITE_TARGETS if fid not in registered
    ]
    standard_ids = [fid for fid in all_ids if not _is_custom(fid)]

    base_url, token = _get_token(env)
    headers = {
        "accept": "application/json",
        "Authorization": f"Bearer {token}",
    }

    # ── Standard fields (batched) ──
    # Some registered ids are virtual/calculated fields the schema API rejects
    # with 400 "Invalid fields in parameter ids: X, Y" — parse those out of the
    # error, drop them from the batch, and retry (they end up in missing_field_ids).
    std_defs: dict[str, dict] = {}
    invalid_ids: set[str] = set()
    batch_size = 100
    for i in range(0, len(standard_ids), batch_size):
        batch = list(standard_ids[i:i + batch_size])
        for _attempt in range(5):
            qs = urllib.parse.urlencode({"ids": ",".join(batch), "limit": 500, "start": 0})
            url = f"{base_url}/encompass/v3/schemas/loan/standardFields?{qs}"
            resp = requests.get(url, headers=headers, timeout=30)
            if resp.status_code == 400 and "Invalid fields" in resp.text:
                try:
                    detail = resp.json().get("details", "")
                except ValueError:
                    detail = resp.text
                rejected = [
                    fid.strip()
                    for fid in detail.split(":", 1)[-1].split(",")
                    if fid.strip() in batch
                ]
                if not rejected:
                    resp.raise_for_status()
                invalid_ids.update(rejected)
                batch = [fid for fid in batch if fid not in rejected]
                if not batch:
                    break
                continue
            resp.raise_for_status()
            for d in resp.json():
                if isinstance(d, dict) and d.get("id"):
                    std_defs[d["id"]] = d
            break

    # ── Custom fields (single listing, filter) ──
    url = f"{base_url}/encompass/v3/settings/loan/customFields"
    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()
    custom_defs = {str(d.get("id", "")).upper(): d for d in resp.json() if isinstance(d, dict)}

    fields: dict[str, dict] = {}
    missing: list[str] = []
    for fid in all_ids:
        d = std_defs.get(fid) if not _is_custom(fid) else custom_defs.get(fid.upper())
        reg = registered.get(fid, {})
        if d is None:
            missing.append(fid)
            fields[fid] = {
                "key": reg.get("key"),
                "label": reg.get("field_name") or fid,
                "category": reg.get("category"),
                "found_in_schema": False,
            }
            continue
        fields[fid] = {
            "key": reg.get("key"),
            "label": reg.get("field_name") or d.get("description") or fid,
            "encompass_description": d.get("description"),
            "category": reg.get("category"),
            "format": d.get("format"),
            "data_type": d.get("dataType"),
            "read_only": bool(d.get("readOnly")),
            "max_length": d.get("maxLength"),
            "multi_instance": bool(d.get("multiInstance")),
            "options": _normalize_options(d.get("options")),
            "is_custom": _is_custom(fid),
            "found_in_schema": True,
        }

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "env": env,
        "source": "GET /v3/schemas/loan/standardFields + /v3/settings/loan/customFields",
        "stats": {
            "total": len(fields),
            "found": len(fields) - len(missing),
            "missing_from_schema": len(missing),
            "with_options": sum(1 for v in fields.values() if v.get("options")),
        },
        "missing_field_ids": missing,
        "fields": fields,
    }

    out_path = os.path.join(output_dir, "config", "field_writes_config.json")
    with open(out_path, "w") as f:
        json.dump(out, f, indent=2)

    logger.info(
        f"[SCHEMA-EXPORT] Wrote {out_path}: {out['stats']['found']}/{out['stats']['total']} "
        f"fields resolved, {out['stats']['with_options']} with options"
    )
    return {"success": True, "path": out_path, "found": out["stats"]["found"], "missing": missing}
