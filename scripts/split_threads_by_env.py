"""Which dashboard environment owns each Cloud thread.

Dev and prod dashboards share one Cloud deployment, so its threads are mixed.
A thread belongs to the environment whose dashboard table stores its id. Writes
one id file per environment (for `migrate_threads.py --ids` and
`compare_threads.py --ids`) and prints counts only.

    export CLOUD_URL=... CLOUD_API_KEY=...
    python scripts/split_threads_by_env.py \
        --table dev=Station-nk3ul6tldzbrdll54xho26672u-NONE \
        --table main=Station-6r6pf3fplbcxreywvccqma737m-NONE

Needs AWS credentials that can scan the tables. Only the id attribute is read.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from compare_threads import Api


def table_ids(table: str, attr: str, region: str) -> set[str]:
    ids, start = set(), None
    while True:
        cmd = ["aws", "dynamodb", "scan", "--region", region, "--table-name", table,
               "--projection-expression", "#a", "--expression-attribute-names", json.dumps({"#a": attr}),
               "--output", "json"]
        if start:
            cmd += ["--exclusive-start-key", json.dumps(start)]
        page = json.loads(subprocess.run(cmd, capture_output=True, text=True, check=True).stdout)
        ids |= {item[attr]["S"] for item in page.get("Items", []) if "S" in item.get(attr, {})}
        start = page.get("LastEvaluatedKey")
        if not start:
            return ids


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--table", action="append", required=True, metavar="ENV=TABLE",
                    help="dashboard table holding thread ids for ENV; repeatable")
    ap.add_argument("--attr", default="threadId", help="attribute that stores the thread id")
    ap.add_argument("--region", default="us-west-1")
    ap.add_argument("--out-dir", type=Path, default=Path("/tmp"))
    args = ap.parse_args()

    cloud = Api("cloud", "CLOUD_URL", "CLOUD_API_KEY")
    cloud_ids = set(cloud.all_thread_ids())
    owners: dict[str, list[str]] = {t: [] for t in cloud_ids}
    for spec in args.table:
        env, table = spec.split("=", 1)
        for t in table_ids(table, args.attr, args.region) & cloud_ids:
            owners[t].append(env)

    groups: dict[str, list[str]] = {}
    for t, envs in owners.items():
        groups.setdefault("+".join(envs) or "unreferenced", []).append(t)
    print(f"cloud threads: {len(cloud_ids)}")
    for name, ids in sorted(groups.items()):
        path = args.out_dir / f"thread-ids-{name}.txt"
        path.write_text("\n".join(sorted(ids)) + "\n")
        print(f"  {name}: {len(ids)} -> {path}")
    if any("+" in g for g in groups):
        print("some threads are referenced by more than one environment; import them into each")
    return 0


if __name__ == "__main__":
    sys.exit(main())
