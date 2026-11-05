"""Compare threads on a LangGraph Cloud deployment with the same threads on its AWS Lambda.

Read-only on both sides. Validates a `POST /threads/import` migration: every Cloud thread
should exist on the Lambda under the same id with the same status, graph and head state.

Env (never printed):
    CLOUD_URL, CLOUD_API_KEY      the LangGraph Cloud deployment and its LangSmith key
    LAMBDA_URL, LAMBDA_API_KEY    the Lambda Function URL and its API_KEY

Usage:
    python scripts/compare_threads.py [--sample N] [--workers 8] [--out report.json]

The report holds thread ids, key names and hashes only, never state values (they are loan data).
Exit status 0 means every Cloud thread matched.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

PAGE = 100
# Identity and bookkeeping fields that legitimately differ between the two stores.
IGNORED_STATE_FIELDS = {"checkpoint", "parent_checkpoint", "checkpoint_id", "created_at", "metadata", "tasks"}


class Api:
    def __init__(self, name: str, url_env: str, key_env: str):
        self.name = name
        self.url = os.environ.get(url_env, "").rstrip("/")
        self.key = os.environ.get(key_env, "")
        if not self.url or not self.key:
            sys.exit(f"set {url_env} and {key_env}")

    def call(self, method: str, path: str, body: object | None = None) -> tuple[int, object]:
        data = None if body is None else json.dumps(body).encode()
        for attempt in range(4):
            req = urllib.request.Request(
                self.url + path, data=data, method=method,
                headers={"Content-Type": "application/json", "x-api-key": self.key},
            )
            try:
                with urllib.request.urlopen(req, timeout=120) as res:
                    return res.status, json.loads(res.read() or b"null")
            except urllib.error.HTTPError as e:
                if e.code < 500 or attempt == 3:
                    return e.code, None
            except Exception as e:  # noqa: BLE001 - truncated reads and resets are retried
                if attempt == 3:
                    return 0, str(e)
        return 0, "unreachable"

    def all_thread_ids(self) -> list[str]:
        ids, offset = [], 0
        while True:
            # Without `select`, Cloud returns every thread's full values and a page times out.
            status, page = self.call(
                "POST", "/threads/search", {"limit": PAGE, "offset": offset, "select": ["thread_id"]}
            )
            if status != 200 or not isinstance(page, list):
                sys.exit(f"{self.name} /threads/search failed at offset {offset}: HTTP {status}")
            ids += [t["thread_id"] for t in page if t.get("thread_id")]
            if len(page) < PAGE:
                return ids
            offset += PAGE


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()[:12]


def compare(thread_id: str, cloud: Api, lam: Api) -> dict:
    out: dict = {"thread_id": thread_id, "problems": []}
    (cs, ct), (ls, lt) = cloud.call("GET", f"/threads/{thread_id}"), lam.call("GET", f"/threads/{thread_id}")
    if ls == 404:
        out["problems"].append("missing on lambda")
        return out
    if cs != 200 or ls != 200:
        out["problems"].append(f"thread fetch: cloud HTTP {cs}, lambda HTTP {ls}")
        return out
    c_graph = (ct.get("metadata") or {}).get("graph_id")
    out["graph_id"], out["status"] = c_graph, ct.get("status")
    if ct.get("status") != lt.get("status"):
        out["problems"].append(f"status: cloud {ct.get('status')}, lambda {lt.get('status')}")

    (cs, cst), (ls, lst) = cloud.call("GET", f"/threads/{thread_id}/state"), lam.call("GET", f"/threads/{thread_id}/state")
    if cs != 200 or ls != 200:
        out["problems"].append(f"state fetch: cloud HTTP {cs}, lambda HTTP {ls}")
        return out
    if (cst.get("next") or []) != (lst.get("next") or []):
        out["problems"].append(f"next: cloud {cst.get('next')}, lambda {lst.get('next')}")
    cv, lv = cst.get("values") or {}, lst.get("values") or {}
    if not isinstance(cv, dict) or not isinstance(lv, dict):
        if digest(cv) != digest(lv):
            out["problems"].append("values differ (non-dict)")
        return out
    out["valueKeys"] = len(cv)
    missing = sorted(set(cv) - set(lv))
    extra = sorted(set(lv) - set(cv))
    changed = sorted(k for k in set(cv) & set(lv) if digest(cv[k]) != digest(lv[k]))
    if missing:
        out["problems"].append(f"keys missing on lambda: {missing}")
    if changed:
        out["problems"].append(f"keys with different values: {changed}")
    if extra:
        # Graph defaults the Lambda fills in; reported, not counted as a failure.
        out["extraKeysOnLambda"] = extra
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, help="compare only the first N Cloud threads")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--out", type=Path, default=Path("/tmp/thread-compare-report.json"))
    ap.add_argument("--skip-graph", action="append", default=[],
                    help="graph_id whose threads aren't migrated (stateless on the Lambda); repeatable")
    ap.add_argument("--ids", type=Path,
                    help="file of thread ids, one per line: the Cloud threads this Lambda should hold")
    args = ap.parse_args()

    cloud = Api("cloud", "CLOUD_URL", "CLOUD_API_KEY")
    lam = Api("lambda", "LAMBDA_URL", "LAMBDA_API_KEY")
    cloud_ids, lambda_ids = cloud.all_thread_ids(), lam.all_thread_ids()
    if args.ids:
        wanted = {line.strip() for line in args.ids.read_text().splitlines() if line.strip()}
        cloud_ids = [t for t in cloud_ids if t in wanted]
    only_lambda = sorted(set(lambda_ids) - set(cloud_ids))
    to_check = cloud_ids[: args.sample] if args.sample else cloud_ids
    print(f"cloud threads: {len(cloud_ids)}, lambda threads: {len(lambda_ids)}, "
          f"lambda-only (started after import, or outside --ids): {len(only_lambda)}; "
          f"comparing {len(to_check)}")

    with ThreadPoolExecutor(args.workers) as pool:
        results = list(pool.map(lambda t: compare(t, cloud, lam), to_check))
    skipped = [r for r in results if r.get("graph_id") in args.skip_graph]
    results = [r for r in results if r not in skipped]
    if skipped:
        print(f"skipped {len(skipped)} threads of graphs {sorted(set(args.skip_graph))}")

    bad = [r for r in results if r["problems"]]
    missing = [r for r in bad if r["problems"] == ["missing on lambda"]]
    with_extra = [r for r in results if r.get("extraKeysOnLambda")]
    report = {
        "cloudThreads": len(cloud_ids), "lambdaThreads": len(lambda_ids), "compared": len(results),
        "matched": len(results) - len(bad), "missingOnLambda": [r["thread_id"] for r in missing],
        "mismatched": [r for r in bad if r not in missing], "lambdaOnly": only_lambda,
        "extraKeysOnLambda": sorted({k for r in with_extra for k in r["extraKeysOnLambda"]}),
    }
    args.out.write_text(json.dumps(report, indent=2))
    print(f"matched {report['matched']}/{len(results)}; missing on lambda {len(missing)}; "
          f"mismatched {len(report['mismatched'])}")
    for r in report["mismatched"][:20]:
        print(f"  {r['thread_id']} ({r.get('graph_id')}, {r.get('status')}): {'; '.join(r['problems'])}")
    if report["extraKeysOnLambda"]:
        print(f"keys only on lambda (not failures): {report['extraKeysOnLambda']}")
    print(f"report: {args.out}")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
