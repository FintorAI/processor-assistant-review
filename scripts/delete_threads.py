"""Delete threads from an AWS Lambda deployment via `DELETE /threads/{id}`.

For threads imported into the wrong environment. Dry run unless --send. With --keep, refuses to
run if any id to delete is also in the keep file (e.g. this environment's own ids).

Env (never printed):
    LAMBDA_URL, LAMBDA_API_KEY    the Lambda Function URL and its API_KEY

Usage:
    python scripts/delete_threads.py --ids remove.txt --keep thread-ids-dev.txt [--send]
"""

from __future__ import annotations

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from compare_threads import Api  # noqa: E402


def read_ids(path: Path) -> list[str]:
    return [line.strip() for line in path.read_text().splitlines() if line.strip()]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", type=Path, required=True, help="file of thread ids to delete, one per line")
    ap.add_argument("--keep", type=Path, help="file of thread ids that must not be deleted")
    ap.add_argument("--send", action="store_true", help="actually delete (default: dry run)")
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()

    ids = read_ids(args.ids)
    if args.keep:
        clash = set(ids) & set(read_ids(args.keep))
        if clash:
            sys.exit(f"{len(clash)} ids are in both --ids and --keep; nothing deleted")

    lam = Api("lambda", "LAMBDA_URL", "LAMBDA_API_KEY")
    present = set(lam.all_thread_ids())
    targets = [t for t in ids if t in present]
    print(f"{len(ids)} ids, {len(targets)} on the lambda")
    if not args.send:
        print("dry run; pass --send to delete")
        return 0

    with ThreadPoolExecutor(args.workers) as pool:
        statuses = list(pool.map(lambda t: lam.call("DELETE", f"/threads/{t}")[0], targets))
    failed = [(t, s) for t, s in zip(targets, statuses) if s != 204]
    print(f"deleted {len(targets) - len(failed)}, failed {len(failed)}")
    for t, s in failed[:20]:
        print(f"  {t}: HTTP {s}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
