# processor-assistant-review

**Review Agent** — verifies a mortgage loan file against Encompass and supporting
documents, flags any issues, and writes confirmed field values back to the LOS.

Part of the [processor-assistant multi-agent system](ARCHITECTURE.md).

---

## Scope

This agent owns Steps 0–9, 15, and 17.3 of the mortgage submission workflow:

| Step | Name | Phase |
|---|---|---|
| 00 | Data Gathering (Find Loan, Fetch Fields, Build Summary) | VERIFICATION |
| 01 | Pre-Checks (Document Presence) | INTAKE |
| 02 | Borrower Summary — Origination | DATA_REVIEW |
| 03 | 1003 URLA Page 1 | DATA_REVIEW |
| 04 | 1003 URLA Page 2 (Employment VOE, Other Income) | DATA_REVIEW |
| 05 | 1003 URLA Part 3 (Assets, EMD, Liabilities, REO) | DATA_REVIEW |
| 06 | 1003 URLA Part 4 (Downpayment, Declarations, Ethnicity) | DATA_REVIEW |
| 07 | Cover Letter (LLM draft → `CX.KM.SUBMISSION.NOTES`) | FORM_UPDATES |
| 08 | Borrower Info — Vesting | FORM_UPDATES |
| 09 | Transmittal Summary (Condo Project Advisor + LOS writes) | FORM_UPDATES |
| 15 | Processor Workflow & Closing (LOS writes) | PROCESSOR_UPDATE |
| 17.3 | Milestone Change and Processor Name | SUBMISSION |

**Not owned by this agent:**

- Orders, AUS, eFolder UI actions → `processor-assistant-integrations` / `processor-assistant-computer-use`
- HITL flag review + full workflow dispatch → `processor-assistant-orchestrator`
- Email notifications → `processor-assistant-integrations`

---

## Multi-agent system

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full system architecture, deployment
map, and inter-agent input/output contract.

The contract types (Pydantic models) live in [`shared/contracts.py`](shared/contracts.py):
`AgentInput`, `AgentOutput`, `Flag`, `FieldWrite`.

A forward-looking cross-loan Postgres schema (for the future dashboard) is
documented in [`docs/future_loan_runs_schema.md`](docs/future_loan_runs_schema.md).

---

## Architecture at a glance

```
definitions/*.yaml          ──►   factory   ──►   output/
  (single source of truth)        (codegen)        (generated runtime)
       │                              │                  │
       │                              │                  ├── tools/        ── per-substep tools
       │                              │                  ├── plans/        ── per-step plans (LLM context)
       │                              │                  ├── config/       ── workflow_config.json, fields_config.json
       │                              │                  ├── registry.py   ── STEP_ORDER, tool maps
       │                              │                  └── proc_agent.py ── LangGraph entrypoint (graph: review)
       │
       └── _agent.yaml
       └── step_NN_*.yaml
```

The `output/` directory is **never edited in sync manually** — the factory is the
single source of truth. See `.cursor/rules/yaml-to-output-sync.mdc`.

FACTORY-LOCK: `output/tools/run_pre_checks.py` and `output/tools/review_borrower_summary.py`
contain hand-written logic and are protected from factory overwrites.

---

## Repo layout

```
processor-assistant-review/
├── definitions/              # YAML — source of truth (Steps 0–9, 15, 17.3 only)
│   ├── _agent.yaml
│   └── step_NN_*.yaml
├── factory/                  # Codegen: YAML → output/
├── output/                   # GENERATED — do not hand-edit unless FACTORY-LOCK: true
│   ├── proc_agent.py         # LangGraph entrypoint (graph = review)
│   ├── registry.py
│   ├── step_loader.py
│   ├── config/
│   ├── plans/
│   └── tools/
├── shared/                   # Hand-written helpers
│   ├── contracts.py          # AgentInput / AgentOutput Pydantic models
│   ├── encompass_io.py
│   ├── docrepo.py
│   └── ...
├── docs/
│   └── future_loan_runs_schema.md   # Forward-looking Postgres schema spec
├── ARCHITECTURE.md           # Multi-agent system architecture + routing table
├── langgraph.json            # LangGraph Cloud config (graph: review)
├── requirements.txt
└── .env.example
```

---

## Setup

Requires Python **3.11**.

```bash
python3.11 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# fill in tokens — see .env.example for required keys
```

---

## Running locally

```bash
langgraph dev
```

The compiled graph (`output/proc_agent.py:graph`) accepts state with at minimum:

- `loan_number` — Encompass loan number (required)
- `env` — `"Test"` or `"Prod"` (required)
- `almas_notes` — originator notes (required for Step 7 Cover Letter)
- `processor_name` — required for Step 17.3 milestone change

Step 0 hydrates `loan_id`, `los_fields`, `doc_fields`, `efolder_documents`,
`loan_summary`, and `loan_profile` into state for downstream steps.

Or call directly as a one-off action (no orchestrator needed):

```python
from langgraph_sdk import get_client

client = get_client(url="http://localhost:2024")
thread = await client.threads.create()
run = await client.runs.create(
    thread_id=thread["thread_id"],
    assistant_id="review",
    input={
        "loan_id": "<encompass-loan-guid>",
        "action": "run_pre_checks",   # or None for full workflow
        "env": "Test",
    },
)
```

---

## The factory (YAML → code)

```bash
python3.11 -m factory factory-reset   # full regenerate (keeps FACTORY-LOCK files)
python3.11 -m factory validate        # must print Validation: PASSED
python3.11 -m factory status          # overview of steps / fields
```

After any YAML edit: run `factory-reset`, confirm `Validation: PASSED`.

### FACTORY-LOCK

Files with `# FACTORY-LOCK: true` on line 9 are never overwritten by the factory.
Flip from `false` to `true` the moment a tool file gains real business logic.
