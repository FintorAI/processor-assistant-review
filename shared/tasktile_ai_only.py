"""Processor-side TaskTile ``rns_ai_only`` client.

Why this exists
---------------
The processor's ``doc_fields`` are populated from the dashboard-side eFolder
extraction. To use ``rns_ai_only`` output *without* changing the dashboard's shared
``rack_and_stack_v1`` default (which would affect every client), the processor calls
TaskTile **directly** for the loan it is working: download the relevant eFolder
attachments → upload to TaskTile → create an ``rns_ai_only`` job → poll → return the
manifest. The bucket engine (``tasktile_fallback`` / ``tasktile_docfields``) then
consumes that manifest to fill / validate gaps.

Design
------
* **Gated:** ``run_ai_only`` returns ``None`` immediately unless
  ``TASKTILE_AI_ONLY_ENABLED`` is on (override with ``force=True`` for the harness/tests).
* **Best-effort:** never raises — any failure logs and returns ``None`` so callers
  degrade gracefully to the existing LandingAI bypass / missing-field flags.
* **No dashboard coupling:** ad-hoc API jobs are not tracked by the dashboard, so this
  does not trigger gap analysis. Job metadata carries a ``source`` tag and (optionally)
  the loan number for traceability.
* **Latency:** ~50s for 2 docs, minutes for more — callers should run this off the
  critical path (shadow) and only for the doc types they actually need.

Env
---
    TASKTILE_AI_ONLY_ENABLED     gate (see tasktile_fallback.ai_only_enabled)
    TASKTILE_API_BASE_URL        default https://tasktile.staging.cybersoftbpo.ai/api
    TASKTILE_PROD_CLIENT_KEY / _SECRET   preferred creds
    TASKTILE_CLIENT_ID / _SECRET         fallback creds
    TASKTILE_PIPELINE            default "rns_ai_only"
    TASKTILE_CATEGORIES_URL      optional; forwarded as categories_url
"""
from __future__ import annotations

import logging
import os
import time
from typing import Optional

import requests

from shared.tasktile_fallback import ai_only_enabled

logger = logging.getLogger(__name__)

_DEFAULT_BASE = "https://tasktile.staging.cybersoftbpo.ai/api"
_DEFAULT_PIPELINE = "rns_ai_only"


# ── config / auth ───────────────────────────────────────────────────────────
def _base() -> str:
    return os.getenv("TASKTILE_API_BASE_URL", _DEFAULT_BASE).rstrip("/")


def _pipeline() -> str:
    return os.getenv("TASKTILE_PIPELINE", _DEFAULT_PIPELINE)


def _creds() -> Optional[tuple[str, str]]:
    """Prefer PROD keys, fall back to the generic client id/secret. None if unset."""
    cid = os.getenv("TASKTILE_PROD_CLIENT_KEY") or os.getenv("TASKTILE_CLIENT_ID")
    sec = os.getenv("TASKTILE_PROD_CLIENT_SECRET") or os.getenv("TASKTILE_CLIENT_SECRET")
    if not cid or not sec:
        return None
    return cid, sec


def _token() -> Optional[str]:
    creds = _creds()
    if not creds:
        logger.warning("[TT_AI_ONLY] No TaskTile creds (TASKTILE_PROD_CLIENT_KEY/SECRET) — skipping.")
        return None
    cid, sec = creds
    logger.info(f"[TT_AI_ONLY] Auth as client_id={cid}")
    r = requests.post(f"{_base()}/auth/token",
                      json={"client_id": cid, "client_secret": sec}, timeout=60)
    r.raise_for_status()
    return r.json()["access_token"]


# ── upload / job / poll ─────────────────────────────────────────────────────
def _upload(token: str, filename: str, pdf: bytes, attachment_id: str) -> str:
    """Initiate → PUT → complete (retries while the virus scan runs)."""
    h = {"Authorization": f"Bearer {token}"}
    init = requests.post(f"{_base()}/uploads/initiate", headers=h, json={
        "filename": filename, "content_type": "application/pdf",
        "size": len(pdf), "metadata": {"attachment_id": attachment_id},
    }, timeout=60)
    init.raise_for_status()
    info = init.json()
    put = requests.put(info["put_url"], data=pdf,
                       headers={"Content-Type": "application/pdf"}, timeout=180)
    put.raise_for_status()
    last = None
    for _ in range(20):  # ~3 min: wait out the virus scan
        c = requests.post(f"{_base()}/uploads/complete", headers=h,
                          json={"upload_id": info["upload_id"]}, timeout=60)
        if c.status_code == 200:
            return info["upload_id"]
        last = c
        time.sleep(10)
    raise RuntimeError(f"upload complete failed: {last.status_code if last else '?'}")


def _create_job(token: str, upload_ids: list, entity: dict,
                scan_retries: int = 120) -> str:
    h = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    payload = {"pipeline_name": _pipeline(), "upload_ids": upload_ids, "entity": entity}
    cats = os.getenv("TASKTILE_CATEGORIES_URL")
    if cats:
        payload["categories_url"] = cats
    for attempt in range(scan_retries):  # ~20 min: wait out "still pending" scans
        r = requests.post(f"{_base()}/jobs", headers=h, json=payload, timeout=120)
        if r.status_code in (200, 201):
            body = r.json()
            return body.get("job_id") or body.get("id")
        if r.status_code == 400 and "still pending" in r.text:
            if attempt == 0:
                logger.info("[TT_AI_ONLY] uploads still scanning; waiting...")
            time.sleep(10)
            continue
        raise RuntimeError(f"job create failed: {r.status_code} {r.text[:300]}")
    raise RuntimeError("job create failed: uploads never cleared scanning")


def _poll(token: str, job_id: str, minutes: int = 30) -> dict:
    h = {"Authorization": f"Bearer {token}"}
    deadline = time.time() + minutes * 60
    last = None
    while time.time() < deadline:
        r = requests.get(f"{_base()}/jobs/{job_id}", headers=h, timeout=60)
        r.raise_for_status()
        st = r.json()
        status = st.get("status") or st.get("state")
        if status != last:
            logger.info(f"[TT_AI_ONLY] job {job_id} status={status}")
            last = status
        if str(status).lower() in ("completed", "success", "succeeded", "failed", "error"):
            return st
        time.sleep(20)
    logger.warning(f"[TT_AI_ONLY] job {job_id} poll timed out after {minutes}m")
    return {}


def _manifest(token: str, job_id: str) -> dict:
    h = {"Authorization": f"Bearer {token}"}
    r = requests.get(f"{_base()}/jobs/{job_id}/manifest", headers=h, timeout=60)
    if r.status_code != 200:
        return {}
    body = r.json()
    if isinstance(body, dict) and "documents" not in body:
        dl = body.get("download_url") or body.get("manifest_url")
        if dl:
            return requests.get(dl, timeout=60).json()
    return body


# ── orchestration ───────────────────────────────────────────────────────────
def run_ai_only(
    loan_id: str,
    attachments: list[dict],
    *,
    state: Optional[dict] = None,
    loan_number: Optional[str] = None,
    wait_minutes: int = 30,
    force: bool = False,
) -> Optional[dict]:
    """Run ``rns_ai_only`` for a loan's attachments and return the manifest.

    Args:
        loan_id: Encompass loan GUID.
        attachments: [{"attachment_id": str, "filename": str}, ...] to extract.
        state: optional agent state (for the Encompass client / env).
        loan_number: optional, tagged into job metadata for traceability. Omit to
            keep metadata neutral (ad-hoc jobs don't trigger gap analysis either way).
        wait_minutes: poll timeout.
        force: bypass the TASKTILE_AI_ONLY_ENABLED gate (harness/tests only).

    Returns the manifest dict, or ``None`` on any failure / when gated off.
    """
    if not force and not ai_only_enabled():
        logger.info("[TT_AI_ONLY] disabled (TASKTILE_AI_ONLY_ENABLED off) — skipping.")
        return None
    if not loan_id:
        logger.warning("[TT_AI_ONLY] no loan_id — skipping.")
        return None
    specs = [a for a in (attachments or []) if a.get("attachment_id")]
    if not specs:
        logger.info("[TT_AI_ONLY] no attachments to process — skipping.")
        return None

    try:
        from shared.ess_contact_bypass import _download_attachment
        from encompass_client import get_encompass_client

        client = get_encompass_client(state=state)
        token = _token()
        if not token:
            return None

        upload_ids = []
        for spec in specs:
            aid = spec["attachment_id"]
            name = spec.get("filename") or "document"
            pdf = _download_attachment(client, loan_id, aid)
            if not pdf:
                logger.warning(f"[TT_AI_ONLY] could not download attachment {aid} — skipping it.")
                continue
            fname = f"{aid}_{name}".replace("/", "_")[:80] + ".pdf"
            upload_ids.append(_upload(token, fname, pdf, aid))

        if not upload_ids:
            logger.warning("[TT_AI_ONLY] no uploads succeeded — aborting.")
            return None

        metadata = {"source": "processor_rns_ai_only"}
        if loan_number:
            metadata["loan_number"] = loan_number
        job_id = _create_job(token, upload_ids, {"metadata": metadata})
        logger.info(f"[TT_AI_ONLY] job {job_id} created with {len(upload_ids)} upload(s)")

        _poll(token, job_id, minutes=wait_minutes)
        man = _manifest(token, job_id)
        if man:
            man.setdefault("_processor", {})["job_id"] = job_id
        return man or None
    except Exception as exc:  # best-effort — never break the caller
        logger.error(f"[TT_AI_ONLY] run failed: {exc}")
        return None
