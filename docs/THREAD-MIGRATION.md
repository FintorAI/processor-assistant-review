# Thread migration: LangGraph Cloud → AWS Lambda

How to move an agent's threads off its LangGraph Cloud deployment so the deployment can be deleted (bucket 3 in `LANGSMITH-COST-PLAN.md`). processor-assistant-review went first; this is what it took and what to repeat for the others.

## Why threads need copying

The dashboard stores the Cloud `thread_id` and later reopens it with `GET /threads/{id}/state` (and sometimes `/history`). Pointing the URL at the Lambda doesn't bring those threads along, and deleting the Cloud deployment deletes them. Each Lambda needs a `POST /threads/import` route, and every thread is copied under the same id.

## What processor-assistant-review taught us

The first import looked fine on screen but wasn't faithful. Comparing all 138 threads found:

| Problem | Effect | Fix (in [PR #61](https://github.com/FintorAI/processor-assistant-review/pull/61)) |
|---|---|---|
| Import called `update_state` without `as_node` | Every thread looked pending from the graph's first node, so a resume could replay it | Pass the Cloud `next`; the import picks the node that reproduces it |
| Messages coerced from plain dicts | `usage_metadata` moved into `additional_kwargs` on every AI message | Rebuild with `messages_from_dict` |
| Overwrite wrote on top of the existing checkpoint | Append-only fields (`field_writes_ledger`) doubled on each re-import | Delete the thread's checkpoints before writing |
| Cloud `/threads/search` without `select` | Returns full state for every thread; a page times out | Search with `select: ["thread_id"]` |
| Reducers run on the imported value | `dedupe_flags` added `substep_name` to flags that had none (only on langgraph 1.2+, where list channels start as `[]`) | Write reducer channels with `Overwrite` ([PR #63](https://github.com/FintorAI/processor-assistant-review/pull/63)) |
| Mixed environments on one Cloud deployment | First import put 44 prod-loan threads in the dev Lambda | Split by dashboard table; `DELETE /threads/{id}` ([PR #62](https://github.com/FintorAI/processor-assistant-review/pull/62)) to remove misplaced ones |
| Stateless graphs (`analyze_uw_conditions`) | No checkpointer on the Lambda, so import can't write them | Skip them; check the dashboard recomputes them |
| Dashboard sends `VITE_LANGSMITH_API_KEY` to every agent | The Lambda checks its own `API_KEY`, so a URL swap alone returns 401 | Per-agent key env var in the dashboard (see Cutover) |

The dashboard screen rendered the broken threads normally. **Clicking through a loan is not validation; the comparison script is.** Run any local checks on the same langgraph version the Lambda installs: the reducer drift above didn't show up on 1.0.x.

## Tools

From processor-assistant-review `scripts/` (copy them into the agent's repo). Stdlib Python; they read these env vars and never print them:

```bash
export CLOUD_URL=https://<deployment>.us.langgraph.app
export LAMBDA_URL=https://<id>.lambda-url.<region>.on.aws
read -s CLOUD_API_KEY && export CLOUD_API_KEY       # a LangSmith key with access to that deployment
read -s LAMBDA_API_KEY && export LAMBDA_API_KEY     # the Lambda's API_KEY (its Secrets Manager secret)
```

- `migrate_threads.py`: copies Cloud threads to the Lambda, passing `next`. Dry run unless `--send`. Flags: `--overwrite`, `--only-missing`, `--skip-graph <graph_id>`, `--ids file`.
- `split_threads_by_env.py`: read-only. Writes one id file per dashboard environment (see Inventory).
- `delete_threads.py`: removes threads imported into the wrong environment. Dry run unless `--send`; `--keep` refuses if any id is also in the keep file.
- `compare_threads.py`: read-only. `--ids` limits it to one environment's threads. For every Cloud thread, checks status, `graph_id`, `next` and every `values` key against the Lambda. Exit 0 only if all match. The report has ids and key names only.

These threads are real loan data. Don't write state to disk, paste it into chat, or share reports beyond ids and key names.

## Per agent

### 1. Inventory (read-only, ~30 min)

- Cloud URL, Lambda URL (dev and prod), the key for each.
- Which graphs have threads on Cloud: `compare_threads.py` prints counts per graph; also check `metadata.graph_id` on a few threads.
- Which of those graphs are checkpointed on the Lambda. Stateless ones can't be imported; confirm the dashboard doesn't reopen them (it calls `/runs/wait` and stores the result, or recomputes on mount).
- What the dashboard reads for this agent: `/state` only, or `/history` too. History is not copied; only the head state is.
- **Which environment each thread belongs to.** Dev and prod dashboards share one Cloud deployment per agent, so its threads are mixed. Don't split on state fields: `env` in agent state is the Encompass tenant the user picked, and dev users run against Prod. A thread belongs to the environment whose dashboard table stores its id. Find the model and attribute that store this agent's thread id (`Station.threadId` for review), then:

  ```bash
  aws appsync list-graphql-apis --region us-west-1   # apiId per Amplify branch (tags amplify:branch-name)
  python scripts/split_threads_by_env.py \
      --table dev=<Model>-<dev apiId>-NONE --table main=<Model>-<main apiId>-NONE [--attr threadId]
  ```

  It writes `/tmp/thread-ids-{dev,main,unreferenced}.txt` and prints counts. Import each file only into its own environment's Lambda; never put prod loan threads in the dev Lambda. Unreferenced threads (station deleted, or replaced by a rerun) can't be opened from any screen; skip them.

### 2. Add the import route to the agent

Copy from processor-assistant-review after PRs #61, #62 and #63:

- `api/platform/models.py`: `ThreadImport` (with `next`, `overwrite`, `as_node`)
- `api/platform/threads.py`: `POST /threads/import`, including `_resolve_assistant` (prefers `metadata.graph_id`; the Cloud `assistant_id` is a Cloud UUID)
- `api/services/state.py`: `import_thread_state`, `_messages_from_wire`, `_bypass_reducers`, `_as_node_for_next`
- `DELETE /threads/{thread_id}` and `ThreadStore.delete` (both store backends)
- `tests/test_thread_import.py`: adapt the synthetic state to the agent's graph

Check the Lambda has `CHECKPOINT_TABLE_NAME` and, for large state, `CHECKPOINT_S3_BUCKET` (DynamoDB rejects items over 400 KB). Payloads must stay under the Function URL's 6 MB request limit; the dry run prints sizes. Open a PR, merge to dev, let it deploy.

### 3. Dry run, import, compare (dev)

```bash
IDS=/tmp/thread-ids-dev.txt    # main for the prod Lambda
python scripts/migrate_threads.py --ids $IDS --skip-graph <stateless graph>          # dry run: counts, sizes, no writes
python scripts/migrate_threads.py --ids $IDS --send --skip-graph <stateless graph>
python scripts/compare_threads.py --ids $IDS --skip-graph <stateless graph>
```

Done when the compare shows **0 mismatched** and **0 missing**. "Lambda-only" should list only threads started on the Lambda itself; anything else there came from the wrong environment. A thread that was imported by an older route version needs `--send --overwrite` (with `--ids` to limit it).

Mismatches to expect and what they mean:

- `next` differs: the import didn't receive `next`, or the route is the old version.
- A key differs in every thread: a reducer or serialization problem in the route; diagnose before re-importing.
- A key differs in a few threads: the thread kept running on Cloud after the import; re-import those ids with `--overwrite`.
- Missing on lambda: created on Cloud after the import; `--only-missing`.

### 4. Look at it once in the dashboard (local)

Optional, after the compare passes. In the dashboard repo:

1. `npx ampx generate outputs --app-id <app> --branch dev --out-dir /tmp/outputs-dev`, then copy it over `amplify_outputs.json` (the committed one points at an old user pool).
2. `.env.local` with the agent's URL override pointing at the Lambda, and the key var set to the Lambda's key.
3. `pnpm dev --port 5174`, sign in with your dev-site account, open one finished and one in-progress loan. Don't press Run.
4. `git checkout amplify_outputs.json && rm .env.local`.

### 5. Cutover (per environment)

1. Dashboard change: give the agent its own key env var (e.g. `VITE_REVIEW_API_KEY`) read by both the browser service and any backend Lambda that calls it, falling back to `VITE_LANGSMITH_API_KEY`. Without this the swap returns 401.
2. Right before switching: rerun `split_threads_by_env.py` (new loans keep landing on Cloud), then `migrate_threads.py --ids <env file> --send --only-missing` and `compare_threads.py --ids <env file>`. Re-import any mismatches with `--overwrite`. Cloud keeps receiving threads until the URL flips.
3. Set the URL and key env vars in the Amplify environment; redeploy.
4. Start one new run and let it finish (exercises the run-completion webhook, which imported threads never do).
5. Final split + `migrate --only-missing` + `compare` to catch threads created during the deploy.
6. Keep Cloud up a few days as rollback (revert the env vars). Then delete the deployment.

## Agents

From `LANGSMITH-COST-PLAN.md` bucket 3. "Route" means `POST /threads/import` and `DELETE /threads/{id}` with the fixes from PRs #61–#63.

| Deployment | AWS side today | Route | Notes |
|---|---|---|---|
| processor-assistant-review | Lambda dev + prod | Done (dev); prod via PR #64 | 138 Cloud threads: 45 dev (migrated, 45/45), 69 prod, 24 unreferenced. Prod still creating threads on Cloud (latest Nov 4) |
| LG-BlendExtractionCUA | Confirm Lambda + checkpointer | None | CUA state may be large; check sizes in the dry run |
| processor-assistant-document-management | Lambda exists | None | 602 October runs: check which graphs are stateless |
| processor-assistant-communications | Lambda exists | None | |
| communications-agent | Confirm Lambda + checkpointer | None | |
| LG-LOAEncompass | Lambda exists | None | |
| gap-analysis-agent | `ad-gap-analysis-agent` Lambda, state read only | None | Confirm whether old threads are reopened at all |
| gap-analysis-agent-development | Dev Cloud the gap UI still hits | None | Repoint the gap UI first |

## processor-assistant-review: remaining steps

The split (Station tables of Dashboard-Officer `dev` and `main`): 45 dev, 69 prod, 24 unreferenced (21 review, 3 `analyze_uw_conditions`). No thread is referenced by both.

Done on dev (Nov 5):
- PRs #61, #62 and #63 merged and deployed.
- The 64 non-dev threads (44 prod, 20 unreferenced) that the first import put in the dev Lambda are deleted.
- The 45 dev threads are re-imported; `compare_threads.py --ids thread-ids-dev.txt` reports 45/45 exact, none missing.

1. Merge [PR #64](https://github.com/FintorAI/processor-assistant-review/pull/64) (`dev` → `main`) to deploy the routes to prod. Then rerun the split (prod still creates threads on Cloud), import `thread-ids-main.txt` into the prod Lambda with its key, and compare.
2. Dashboard-Officer: `VITE_REVIEW_API_KEY` change (browser `processor-review.service.ts`, `invokeProcessorReviewAgent`, `cancelReviewRun`).
3. Cutover per environment as above; delete the Cloud deployment after the rollback window.
