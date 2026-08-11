"""Tests for LoanLockMiddleware in output/proc_agent.py — the agent-level
wiring that holds one Encompass lock for the whole review run (main workflow
+ Mavent targeted rerun). See
processor-assistant-orchestrator/docs/encompass_resource_locking_plan.md.

Importing proc_agent builds the real `create_agent()` graph (module-level
`graph = create_agent()`), so this exercises the actual middleware wiring,
not a reimplementation of it.
"""
import os
import sys

import pytest

_OUTPUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "output")
if _OUTPUT_DIR not in sys.path:
    sys.path.insert(0, _OUTPUT_DIR)

import proc_agent  # noqa: E402
import encompass_client  # noqa: E402
import tools.data_gathering as data_gathering  # noqa: E402
from langgraph.types import Command  # noqa: E402

LOAN_ID = "12345678-aaaa-bbbb-cccc-1234567890ab"
LOCK_ID = "lock-guid-0001"


@pytest.fixture
def middleware():
    return proc_agent.LoanLockMiddleware()


# ═══════════════════════════════════════════════════════════════════════
# before_agent
# ═══════════════════════════════════════════════════════════════════════

def test_before_agent_acquires_lock_when_loan_id_known(monkeypatch, middleware):
    monkeypatch.setattr(encompass_client, "lock_resource", lambda loan_id, state=None: LOCK_ID)
    state = {"loan_id": LOAN_ID, "loan_number": "2604964148", "env": "Test"}

    update = middleware.before_agent(state, runtime=None)

    assert update == {"_loan_lock_id": LOCK_ID}


def test_before_agent_resolves_loan_id_via_find_loan_then_locks(monkeypatch, middleware):
    """No loan_id yet (brand-new run) — must resolve it via the SAME
    find_loan tool Step 0 uses, not a bespoke lookup."""
    monkeypatch.setattr(encompass_client, "lock_resource", lambda loan_id, state=None: LOCK_ID)

    def fake_find_loan_func(tool_call_id, state, loan_number=None, borrower_name=None):
        assert loan_number == "2604964148"
        return Command(update={"loan_id": LOAN_ID, "loan_number": loan_number})

    monkeypatch.setattr(data_gathering.find_loan, "func", fake_find_loan_func)

    state = {"loan_number": "2604964148", "env": "Test"}
    update = middleware.before_agent(state, runtime=None)

    assert update == {"loan_id": LOAN_ID, "_loan_lock_id": LOCK_ID}


def test_before_agent_noop_when_find_loan_cannot_resolve(monkeypatch, middleware):
    monkeypatch.setattr(
        data_gathering.find_loan, "func",
        lambda tool_call_id, state, loan_number=None, borrower_name=None: Command(
            update={"messages": []}
        ),
    )
    lock_calls = []
    monkeypatch.setattr(
        encompass_client, "lock_resource",
        lambda loan_id, state=None: lock_calls.append(loan_id),
    )

    state = {"loan_number": "not-a-real-loan", "env": "Test"}
    update = middleware.before_agent(state, runtime=None)

    assert update is None
    assert lock_calls == []  # never attempted a lock without a resolved GUID


def test_before_agent_noop_when_nothing_to_lock_against(middleware):
    """Fresh state with neither loan_id nor loan_number — must not blow up."""
    assert middleware.before_agent({}, runtime=None) is None


def test_before_agent_skips_reacquire_when_already_locked(monkeypatch, middleware):
    lock_calls = []
    monkeypatch.setattr(
        encompass_client, "lock_resource",
        lambda loan_id, state=None: lock_calls.append(loan_id),
    )
    state = {"loan_id": LOAN_ID, "_loan_lock_id": LOCK_ID}

    update = middleware.before_agent(state, runtime=None)

    assert update is None
    assert lock_calls == []


def test_before_agent_halts_gracefully_on_loan_locked_error(monkeypatch, middleware):
    def fake_lock_resource(loan_id, state=None):
        raise encompass_client.LoanLockedError(
            "Loan 12345678 is already locked by Test Admin.",
            holder_user_id="testadmin",
            holder_full_name="Test Admin",
            lock_id="their-lock-id",
        )

    monkeypatch.setattr(encompass_client, "lock_resource", fake_lock_resource)
    state = {"loan_id": LOAN_ID}

    update = middleware.before_agent(state, runtime=None)

    assert update["current_step"] == "COMPLETED"
    assert len(update["flags"]) == 1
    flag = update["flags"][0]
    assert flag["severity"] == "error"
    assert "Test Admin" in flag["details"]
    assert flag["resolved"] is False
    assert "_loan_lock_id" not in update  # never fabricate a lock id we don't hold


def test_before_agent_clears_pending_targeted_action_on_lock_failure(monkeypatch, middleware):
    """Regression guard: resolve_tools_for_step gives a pending targeted
    rerun (e.g. Mavent) priority over current_step, so current_step=
    'COMPLETED' alone would NOT stop the model from still calling the
    targeted write tool. The pending action must be cleared too."""
    monkeypatch.setattr(
        encompass_client, "lock_resource",
        lambda loan_id, state=None: (_ for _ in ()).throw(
            encompass_client.LoanLockedError("locked", holder_full_name="Test Admin")
        ),
    )
    state = {
        "loan_id": LOAN_ID,
        "additional_info": {"action": "run_mavent_compliance", "other_key": "keep-me"},
    }

    update = middleware.before_agent(state, runtime=None)

    assert update["additional_info"] == {"other_key": "keep-me"}
    # And resolve_tools_for_step must actually treat the run as done now
    # that the targeted action is gone.
    merged_state = {**state, **update}
    assert proc_agent.get_targeted_action(merged_state) is None
    tools = proc_agent.resolve_tools_for_step(merged_state)
    tool_names = {getattr(t, "name", None) for t in tools}
    assert "run_mavent_compliance" not in tool_names


# ═══════════════════════════════════════════════════════════════════════
# after_agent
# ═══════════════════════════════════════════════════════════════════════

def test_after_agent_releases_lock_and_clears_channel(monkeypatch, middleware):
    calls = []
    monkeypatch.setattr(
        encompass_client, "unlock_resource",
        lambda lock_id, loan_id, state=None: calls.append((lock_id, loan_id)),
    )
    state = {"loan_id": LOAN_ID, "_loan_lock_id": LOCK_ID}

    update = middleware.after_agent(state, runtime=None)

    assert calls == [(LOCK_ID, LOAN_ID)]
    assert update == {"_loan_lock_id": ""}


def test_after_agent_noop_when_no_lock_held(monkeypatch, middleware):
    calls = []
    monkeypatch.setattr(
        encompass_client, "unlock_resource",
        lambda lock_id, loan_id, state=None: calls.append((lock_id, loan_id)),
    )
    assert middleware.after_agent({"loan_id": LOAN_ID}, runtime=None) is None
    assert middleware.after_agent({"loan_id": LOAN_ID, "_loan_lock_id": ""}, runtime=None) is None
    assert calls == []


def test_cleared_lock_channel_never_blocks_the_next_run(monkeypatch, middleware):
    """The "" clear sentinel (not None — see the field comment on
    ProcessorAgentState) must actually make the next run's before_agent
    re-acquire instead of treating a stale id as still held."""
    lock_calls = []
    monkeypatch.setattr(
        encompass_client, "lock_resource",
        lambda loan_id, state=None: lock_calls.append(loan_id) or LOCK_ID,
    )
    state_after_release = {"loan_id": LOAN_ID, "_loan_lock_id": ""}

    update = middleware.before_agent(state_after_release, runtime=None)

    assert lock_calls == [LOAN_ID]
    assert update == {"_loan_lock_id": LOCK_ID}
