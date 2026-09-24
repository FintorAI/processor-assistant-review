# AWS Deployment — processor-assistant-review

Lambda Function URL (API) + ECS Fargate (long background runs) + DynamoDB/S3 checkpoints.
Playbook: `monte-carlo-leadership-intelligence/monte-carlo-intelligence/AWS_DEPLOYMENT_PLAYBOOK.md` (Phase 4 Fargate).
Pattern reference: `LG-discOrch`.

## Architecture

```
Dashboard ──► Lambda Function URL (Platform shim)
                 │
                 ├─ /runs/wait (write_los_* / analyze_uw_conditions) — in-Lambda
                 └─ POST /threads/{id}/runs (full review) ──► Fargate worker
                                                                 │
                                                                 └─ webhook POST → reviewRunWebhook
```

## Local deploy

```bash
./infra/deploy.sh dev
./infra/smoke_test_aws.sh dev
```

## Worker VPC

By default each stage creates its own VPC (2 public subnets, no NAT). us-west-1
allows 5 VPCs per region and the account is at that limit, so a stage can reuse
an existing VPC instead:

```bash
WORKER_VPC_ID=vpc-xxxxxxxx ./infra/deploy.sh prod
```

The VPC must have public subnets — `run_task` uses `assignPublicIp=ENABLED`.
The security group and ECS cluster are still per-stage, so two stages can share
one VPC. Raising the "VPCs per Region" quota is the alternative if prod needs
its own network boundary.

## Dashboard cutover (config only)

```bash
VITE_REVIEW_DEPLOYMENT_URL=https://xxxx.lambda-url.us-west-1.on.aws/
# Auth: either set stack API_KEY == VITE_LANGSMITH_API_KEY, or introduce a dedicated key
REVIEW_DEPLOYMENT_URL=...   # Amplify invokeProcessorReviewAgent / cancelReviewRun
```

Hardcoded `*.langgraph.app` fallbacks in `copilotkit-config.ts` must not win — set the Vite env at build time.

## HITL / webhook

- Background create stores `webhook` on the run body (Dashboard `startReviewRun`).
- Fargate `execute_background_run` POSTs a Platform-shaped Run payload on terminal status.
- Client polls `GET /threads/{id}/state` (already 5s poll in Dashboard).

## Rollback

Point `VITE_REVIEW_DEPLOYMENT_URL` / Amplify `REVIEW_DEPLOYMENT_URL` back to LangGraph Cloud + LangSmith key.
