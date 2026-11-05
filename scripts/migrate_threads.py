"""Copy threads from a LangGraph Cloud deployment to its AWS Lambda via `POST /threads/import`.

Reads each Cloud thread's record and head state and imports it under the same id, passing the
source `next` so the Lambda reproduces where the run stopped. Dry run unless --send.

Env (never printed):
    CLOUD_URL, CLOUD_API_KEY      the LangGraph Cloud deployment and its LangSmith key
    LAMBDA_URL, LAMBDA_API_KEY    the Lambda Function URL and its API_KEY

Usage:
    python scripts/migrate_threads.py [--send] [--overwrite] [--only-missing]
        [--skip-graph analyze_uw_conditions] [--ids ids.txt] [--workers 4] [--out manifest.json]

Then validate with scripts/compare_threads.py. The manifest holds ids and result flags only.
"""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from compare_threads import Api  # noqa: E402


def migrate(thread_id: str, cloud: Api, lam: Api, args) -> dict:
    out: dict = {"thread_id": thread_id}
    status, thread = cloud.call("GET", f"/threads/{thread_id}")
    if status != 200:
        return {**out, "error": f"cloud thread HTTP {status}"}
    graph = (thread.get("metadata") or {}).get("graph_id")
    out["graph_id"] = graph
    if graph in args.skip_graph:
        return {**out, "skipped": "graph"}
    status, state = cloud.call("GET", f"/threads/{thread_id}/state")
    if status != 200:
        return {**out, "error": f"cloud state HTTP {status}"}
    body = {
        "thread_id": thread_id,
        "metadata": thread.get("metadata") or {},
        "status": thread.get("status") or "idle",
        "values": state.get("values") or {},
        "next": state.get("next") or [],
        "overwrite": args.overwrite,
    }
    out["next"] = body["next"]
    if not args.send:
        return {**out, "dryRun": True, "bytes": len(json.dumps(body))}
    status, res = lam.call("POST", "/threads/import", body)
    if status != 200 or not isinstance(res, dict):
        return {**out, "error": f"import HTTP {status}"}
    if res.get("skipped"):
        return {**out, "skipped": res.get("reason", "exists")}
    if res.get("state_error"):
        return {**out, "error": f"state_error: {str(res['state_error'])[:200]}"}
    return {**out, "imported": True, "stateWritten": bool(res.get("state_written"))}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--send", action="store_true", help="actually import (default: dry run)")
    ap.add_argument("--overwrite", action="store_true", help="replace threads already on the Lambda")
    ap.add_argument("--only-missing", action="store_true", help="only threads not yet on the Lambda")
    ap.add_argument("--skip-graph", action="append", default=[], help="graph_id not to migrate; repeatable")
    ap.add_argument("--ids", type=Path, help="file of thread ids, one per line (default: all Cloud threads)")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--out", type=Path, default=Path("/tmp/thread-migrate-manifest.json"))
    args = ap.parse_args()

    cloud = Api("cloud", "CLOUD_URL", "CLOUD_API_KEY")
    lam = Api("lambda", "LAMBDA_URL", "LAMBDA_API_KEY")
    ids = args.ids.read_text().split() if args.ids else cloud.all_thread_ids()
    if args.only_missing:
        present = set(lam.all_thread_ids())
        ids = [t for t in ids if t not in present]
    print(f"{'importing' if args.send else 'dry run for'} {len(ids)} threads"
          f"{' (overwrite)' if args.overwrite else ''}")

    with ThreadPoolExecutor(args.workers) as pool:
        results = list(pool.map(lambda t: migrate(t, cloud, lam, args), ids))
    args.out.write_text(json.dumps(results, indent=2))

    def count(key):
        return sum(1 for r in results if r.get(key))

    print(f"imported {count('imported')}, skipped {count('skipped')}, errors {count('error')}, dry-run {count('dryRun')}")
    for r in [r for r in results if r.get("error")][:20]:
        print(f"  {r['thread_id']} ({r.get('graph_id')}): {r['error']}")
    print(f"manifest: {args.out}")
    return 1 if count("error") else 0


if __name__ == "__main__":
    sys.exit(main())
