# Future: `loan_runs` Postgres Schema

**Status: deferred — not implemented at v1.**

This document is the spec for the cross-loan dashboard backing store.
Add this when a dashboard query need appears (e.g. "show all loans with
unresolved critical flags", "show all loans where appraisal was ordered
this week"). Until then, LangSmith + LangGraph checkpointers cover
per-run observability, and Encompass is canonical for field state.

---

## Tables

### `loan_runs`

Top-level record for one agent run against one loan.

```sql
CREATE TABLE loan_runs (
    run_id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    loan_id         TEXT        NOT NULL,           -- Encompass loan GUID
    loan_number     TEXT,                           -- Human-readable loan number
    agent           TEXT        NOT NULL,           -- 'review' | 'integrations' | 'computer-use' | 'orchestrator'
    action          TEXT,                           -- NULL = full run; tool name = one-off
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at    TIMESTAMPTZ,
    status          TEXT        NOT NULL DEFAULT 'running',  -- 'running' | 'ok' | 'failed' | 'needs_hitl'
    processor_name  TEXT,
    env             TEXT        NOT NULL DEFAULT 'Prod',     -- 'Prod' | 'Test'
    langsmith_run_id TEXT,                          -- LangSmith run ID for deep-link
    summary         TEXT                            -- Human-readable run summary
);

CREATE INDEX idx_loan_runs_loan_id     ON loan_runs (loan_id);
CREATE INDEX idx_loan_runs_status      ON loan_runs (status);
CREATE INDEX idx_loan_runs_started_at  ON loan_runs (started_at DESC);
CREATE INDEX idx_loan_runs_agent       ON loan_runs (agent);
```

**Dashboard queries this enables:**
- All active runs: `WHERE status = 'running'`
- All loans waiting on HITL: `WHERE status = 'needs_hitl'`
- All runs for a loan: `WHERE loan_id = $1 ORDER BY started_at DESC`
- All failed runs today: `WHERE status = 'failed' AND started_at > now() - INTERVAL '1 day'`

---

### `loan_run_flags`

Every flag raised across all substeps in a run.

```sql
CREATE TABLE loan_run_flags (
    id              BIGSERIAL   PRIMARY KEY,
    run_id          UUID        NOT NULL REFERENCES loan_runs(run_id) ON DELETE CASCADE,
    substep_id      TEXT        NOT NULL,           -- e.g. '5.2'
    substep_name    TEXT,
    severity        TEXT        NOT NULL,           -- 'info' | 'warning' | 'critical'
    code            TEXT        NOT NULL,           -- e.g. 'EMD_AMOUNT_MISMATCH'
    message         TEXT        NOT NULL,
    payload         JSONB,                          -- structured context (expected vs actual, etc.)
    resolved        BOOLEAN     NOT NULL DEFAULT false,
    resolved_at     TIMESTAMPTZ,
    resolution_note TEXT
);

CREATE INDEX idx_flags_run_id    ON loan_run_flags (run_id);
CREATE INDEX idx_flags_severity  ON loan_run_flags (severity);
CREATE INDEX idx_flags_resolved  ON loan_run_flags (resolved);
CREATE INDEX idx_flags_code      ON loan_run_flags (code);
```

**Dashboard queries this enables:**
- All unresolved critical flags across all runs: `WHERE severity = 'critical' AND resolved = false`
- All flags for a loan (join with loan_runs): `JOIN loan_runs ON ... WHERE loan_id = $1`
- Flag frequency report: `GROUP BY code ORDER BY count DESC`

---

### `loan_run_writes`

Every LOS field write performed or staged during a run.

```sql
CREATE TABLE loan_run_writes (
    id              BIGSERIAL   PRIMARY KEY,
    run_id          UUID        NOT NULL REFERENCES loan_runs(run_id) ON DELETE CASCADE,
    substep_id      TEXT,
    field_id        TEXT        NOT NULL,           -- Encompass field ID
    field_name      TEXT,
    old_value       TEXT,
    new_value       TEXT        NOT NULL,
    committed       BOOLEAN     NOT NULL DEFAULT false,  -- true = actually written to Encompass
    committed_at    TIMESTAMPTZ
);

CREATE INDEX idx_writes_run_id   ON loan_run_writes (run_id);
CREATE INDEX idx_writes_field_id ON loan_run_writes (field_id);
CREATE INDEX idx_writes_committed ON loan_run_writes (committed);
```

**Dashboard queries this enables:**
- All staged (uncommitted) writes waiting for HITL approval: `WHERE committed = false`
- Audit trail for a field across all loans: `WHERE field_id = 'CX.PROCESSOR.NAME'`
- Write volume by substep: `GROUP BY substep_id`

---

### `loan_run_external`

Results from third-party API calls (Ocrolus, Fannie DU, Freddie LP, SMTP, etc.).

```sql
CREATE TABLE loan_run_external (
    id              BIGSERIAL   PRIMARY KEY,
    run_id          UUID        NOT NULL REFERENCES loan_runs(run_id) ON DELETE CASCADE,
    substep_id      TEXT,
    system          TEXT        NOT NULL,           -- 'ocrolus' | 'fannie_du' | 'freddie_lp' | 'smtp' | ...
    action          TEXT        NOT NULL,           -- e.g. 'run_income_calc' | 'order_appraisal'
    status          TEXT        NOT NULL,           -- 'success' | 'failed' | 'skipped'
    request         JSONB,                          -- sanitized request payload (no secrets)
    response        JSONB,                          -- parsed response
    error           TEXT,
    completed_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_external_run_id  ON loan_run_external (run_id);
CREATE INDEX idx_external_system  ON loan_run_external (system);
CREATE INDEX idx_external_status  ON loan_run_external (status);
```

**Dashboard queries this enables:**
- All failed Ocrolus runs this week: `WHERE system = 'ocrolus' AND status = 'failed'`
- AUS decisions by loan type (join with LOS fields): ad-hoc
- Email delivery failures: `WHERE system = 'smtp' AND status = 'failed'`

---

### `loan_run_efolder_actions`

eFolder UI automation actions (computer-use agent).

```sql
CREATE TABLE loan_run_efolder_actions (
    id              BIGSERIAL   PRIMARY KEY,
    run_id          UUID        NOT NULL REFERENCES loan_runs(run_id) ON DELETE CASCADE,
    substep_id      TEXT,
    action          TEXT        NOT NULL,           -- 'delete_bucket' | 'mark_ready_for_uw' | 'move_to_recycle' | ...
    target          TEXT        NOT NULL,           -- document type or bucket name
    status          TEXT        NOT NULL,           -- 'success' | 'failed' | 'skipped'
    error           TEXT,
    completed_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_efolder_run_id  ON loan_run_efolder_actions (run_id);
CREATE INDEX idx_efolder_status  ON loan_run_efolder_actions (status);
CREATE INDEX idx_efolder_action  ON loan_run_efolder_actions (action);
```

**Dashboard queries this enables:**
- All failed eFolder actions: `WHERE status = 'failed'`
- Docs not marked Ready-for-UW: `WHERE action = 'mark_ready_for_uw' AND status != 'success'`

---

## Migration Path

When standing up the Postgres store:

1. Create schema with migrations tool (Alembic or Flyway).
2. Add a `write_run_output(run_id, agent_output: AgentOutput)` helper in
   `shared/contracts.py` that maps `AgentOutput` fields to the tables above.
3. Update the orchestrator to call `write_run_output` after each sub-agent
   completes.
4. Add the LangSmith `run_id` to `loan_runs.langsmith_run_id` via the
   LangSmith SDK callback.

Do **not** move canonical LOS field state here — Encompass remains the source
of truth. This store is append-only audit + dashboard data.
