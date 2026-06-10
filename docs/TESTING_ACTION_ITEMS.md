# Testing — Communications Action Items (`build_action_items`)

How to test the `comms_actions` feature added to the review agent: the
`build_action_items` rule registry (final substep `STEP_11.3`) and the
`merge_comms_actions` reducer.

See also: [`../README.md` → Communications action items](../README.md) and the
downstream contract in
[`processor-assistant-communications/docs/AGENT_INPUT_CONTRACT.md`](../../processor-assistant-communications/docs/AGENT_INPUT_CONTRACT.md).

---

## What's under test

| Component | File |
|---|---|
| Rule registry (emits action items) | `output/tools/build_action_items.py` |
| State field + dedupe reducer | `output/proc_agent.py` (`comms_actions`, `merge_comms_actions`) |
| Registration | `output/tools/__init__.py`, `output/config/workflow_config.json` (STEP_11 + substep `3`) |

**Rules and their triggers**

| `action_type` | Fires when | Graph |
|---|---|---|
| `order_title_report` | No **Title Report** in the eFolder | `processor_title_order` |
| `lock_desk_address_change` | Loan **locked** AND USPS-normalized address ≠ LOS address | `processor_lock_desk` |
| `emd_request` | **Purchase** loan with an unresolved EMD flag (`review_urla_emd`) | `processor_emd_request` |
| `hoa_loe_signature` | **Not a condo** AND no **HOA Statement** on file | `processor_blend_loe` |

---

## Test A — isolated rule logic (no network, fastest)

Exercises the rules against a synthetic state. No Encompass/LangGraph calls.

```bash
cd processor-assistant-review/output
./../venv/bin/python -c "
import sys; sys.path.insert(0,'.')
from tools.build_action_items import RULES
def los(d): return {k:{'value':v} for k,v in d.items()}
# Condo, locked, title+HOA missing, EMD mismatch (mirrors loan 2604964148)
state={'loan_number':'2604964148','loan_id':'g','env':'Prod','processor_name':'Ash Desai',
 'los_fields':los({'borrower_first_name':'Jane','borrower_last_name':'Buyer',
   'property_address':'2814 Carlisle Dr Unit 18','property_city':'New Windsor',
   'property_state':'MD','property_zip':'21776','property_type':'Condominium',
   'loan_purpose':'Purchase','rate_is_locked':'Y','emd_amount':'10000'}),
 'address_validation':{'valid':True,
   'normalized':'2814 Carlisle Dr Unit 18 New Windsor MD 21776',
   'los_address':'2814 Carlisle Dr Apt 18, New Windsor, MD 21776'},
 'efolder_documents':{},
 'flags':[{'title':'EMD Amount Mismatch','details':'9000 vs 10000','resolved':False}]}
for it in (r(state) for r in RULES):
    print(it['action_type'],'->',it['trigger']['graph_id']) if it else print('(none)')
"
```

**Expected**
```
order_title_report -> processor_title_order
lock_desk_address_change -> processor_lock_desk
emd_request -> processor_emd_request
(none)                       # HOA suppressed — property is a condo
```

### Variations to confirm each rule

- **Title present:** add `efolder_documents={'Title Report': {'efolder_listing_count': 1}}` → `order_title_report` disappears.
- **Not locked:** set `rate_is_locked:'N'` → `lock_desk_address_change` disappears.
- **Address already normalized:** make `normalized` equal `los_address` (canonically) → `lock_desk_address_change` disappears.
- **HOA letter case:** set `property_type:'Single Family'` and no HOA Statement → `hoa_loe_signature` appears.
- **No EMD flag:** drop the EMD flag → `emd_request` disappears.

### Registration sanity

```bash
cd processor-assistant-review/output
./../venv/bin/python -c "
import sys; sys.path.insert(0,'.')
from tools import get_all_tools
names=[getattr(t,'name',getattr(t,'__name__','?')) for t in get_all_tools()]
assert 'build_action_items' in names
print('registered OK; total tools:', len(names))
"
```

---

## Test B — full agent run (integration)

Runs the whole workflow; `comms_actions` is populated only after `STEP_11.3`.

```bash
cd processor-assistant-review
source venv/bin/activate
langgraph dev          # serves the `review` graph at http://localhost:2024
```

```python
from langgraph_sdk import get_client
client = get_client(url="http://localhost:2024")
thread = await client.threads.create()
await client.runs.wait(
    thread["thread_id"], "review",
    input={"loan_number": "2604964148", "env": "Prod", "processor_name": "Ash Desai"},
)
state = await client.threads.get_state(thread["thread_id"])
print(state["values"].get("comms_actions"))   # list of action items
```

**Pass criteria**
- Run reaches `current_step == "COMPLETED"`.
- `comms_actions` is a list; each item has `id`, `component`, `action_type`,
  `trigger.graph_id`, and `trigger.payload` (with top-level `loan_number/env/processor_name` + nested `inputs`).
- The items present match the loan's data (see trigger table).

> A single-substep re-run will **not** populate `comms_actions` unless the run
> reaches STEP_11. Use a full run (or a one-off `action: "build_action_items"`
> on a thread whose state is already hydrated from a prior full run).

---

## Test C — dedupe / idempotency (reducer)

Re-run the same thread (full run or `action: "build_action_items"`):

```python
await client.runs.wait(thread["thread_id"], "review",
    input={"loan_number":"2604964148","env":"Prod","processor_name":"Ash Desai"})
state = await client.threads.get_state(thread["thread_id"])
print(len(state["values"]["comms_actions"]))   # same count — no duplicates
```

**Pass criteria**
- Count does not grow on re-run (deduped by `id`).
- If an item carried runtime fields (`status`, `result`, `thread_id`) from a
  prior pass, those survive the re-derivation (static fields refresh).

---

## Test D — live state probe (optional)

To validate rules against **real** Encompass data rather than synthetic state,
run `build_action_items` against a loan whose state was hydrated by a real Step 0.
The fastest path is Test B against a real loan and inspecting `comms_actions`.
(If a dedicated `scripts/probe_action_items.py` is added, document its usage here.)
