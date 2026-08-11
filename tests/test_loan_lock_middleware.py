"""Tests for LoanLockMiddleware in output/proc_agent.py — the agent-level
wiring that holds one Encompass lock for the whole review run (main workflow
+ Mavent targeted rerun). See
processor-assistant-orchestrator/docs/encompass_resource_locking_plan.md.

Importing proc_agent builds the real `create_agent()` graph (module-level
`graph = create_agent()`), so this exercises the actual middleware wiring,
not a reimplementation of it.
"""
import pytest

import proc_agent
import encompass_client
import tools.data_gathering as data_gathering
from langgraph.types import Command

LOAN_ID = "12345678-aaaa-bbbb-cccc-1234567890ab"
LOCK_ID = "lock-guid-0001"


@pytest.fixture
def middleware():
    return proc_agent.LoanLockMiddleware()


def test_loan_lock_middleware_is_first_in_created_agent_stack(monkeypatch):
    """Regression guard for the ordering invariant documented on
    LoanLockMiddleware: before_agent hooks run in list order and after_agent
    hooks run in reverse, so it must be first (among the middleware this
    repo controls) to gate before any of our own before_agent hooks and
    release last, after every other middleware's after_agent has run.

    Intercepts the `middleware=` kwarg create_agent() passes to
    create_deep_agent — introspecting the compiled graph directly is
    unreliable since copilotagent.create_deep_agent prepends its own
    built-in middleware ahead of whatever we pass (see the class
    docstring), so "first" only holds for the list we construct here.
    """
    captured = {}

    def fake_create_deep_agent(*args, middleware=(), **kwargs):
        captured["middleware"] = middleware
        return object()

    monkeypatch.setattr(proc_agent, "create_deep_agent", fake_create_deep_agent)
    proc_agent.create_agent()

    stack = captured["middleware"]
    assert isinstance(stack[0], proc_agent.LoanLockMiddleware)


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


# ═══════════════════════════════════════════════════════════════════════
# wrap_model_call / wrap_tool_call — escape-hatch release when after_agent
# is bypassed (uncaught exception, or the graceful substep-timeout
# interrupt in WorkflowGuardMiddleware)
# ═══════════════════════════════════════════════════════════════════════

class _FakeRequest:
    def __init__(self, state):
        self.state = state


def test_wrap_model_call_releases_lock_on_exception_and_reraises(monkeypatch, middleware):
    calls = []
    monkeypatch.setattr(
        encompass_client, "unlock_resource",
        lambda lock_id, loan_id, state=None: calls.append((lock_id, loan_id)),
    )
    request = _FakeRequest({"loan_id": LOAN_ID, "_loan_lock_id": LOCK_ID})

    def handler(req):
        raise RuntimeError("model call blew up")

    with pytest.raises(RuntimeError):
        middleware.wrap_model_call(request, handler)

    assert calls == [(LOCK_ID, LOAN_ID)]


def test_wrap_model_call_releases_lock_on_graph_interrupt(monkeypatch, middleware):
    """The substep-timeout halt in WorkflowGuardMiddleware raises
    GraphInterrupt (via langgraph.types.interrupt) from inside the handler
    chain this middleware wraps — must release the same as any other
    escaping exception."""
    from langgraph.errors import GraphInterrupt

    calls = []
    monkeypatch.setattr(
        encompass_client, "unlock_resource",
        lambda lock_id, loan_id, state=None: calls.append((lock_id, loan_id)),
    )
    request = _FakeRequest({"loan_id": LOAN_ID, "_loan_lock_id": LOCK_ID})

    def handler(req):
        raise GraphInterrupt("TIMEOUT: substep halted")

    with pytest.raises(GraphInterrupt):
        middleware.wrap_model_call(request, handler)

    assert calls == [(LOCK_ID, LOAN_ID)]


def test_wrap_model_call_noop_on_success(monkeypatch, middleware):
    calls = []
    monkeypatch.setattr(
        encompass_client, "unlock_resource",
        lambda lock_id, loan_id, state=None: calls.append((lock_id, loan_id)),
    )
    request = _FakeRequest({"loan_id": LOAN_ID, "_loan_lock_id": LOCK_ID})

    result = middleware.wrap_model_call(request, lambda req: "ok")

    assert result == "ok"
    assert calls == []  # after_agent (not this hook) releases on the happy path


def test_wrap_model_call_noop_when_no_lock_held(middleware):
    """No lock in state (e.g. before_agent never ran, or already released)
    — must not attempt to unlock, just propagate the exception."""
    request = _FakeRequest({"loan_id": LOAN_ID})

    def handler(req):
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        middleware.wrap_model_call(request, handler)


def test_wrap_tool_call_releases_lock_on_exception_and_reraises(monkeypatch, middleware):
    calls = []
    monkeypatch.setattr(
        encompass_client, "unlock_resource",
        lambda lock_id, loan_id, state=None: calls.append((lock_id, loan_id)),
    )
    request = _FakeRequest({"loan_id": LOAN_ID, "_loan_lock_id": LOCK_ID})

    def handler(req):
        raise RuntimeError("tool call blew up")

    with pytest.raises(RuntimeError):
        middleware.wrap_tool_call(request, handler)

    assert calls == [(LOCK_ID, LOAN_ID)]


def test_awrap_model_call_releases_lock_on_exception_and_reraises(monkeypatch, middleware):
    # No pytest-asyncio in this repo's test tooling — drive the coroutine
    # directly with asyncio.run() rather than adding a new dependency.
    import asyncio

    calls = []
    monkeypatch.setattr(
        encompass_client, "unlock_resource",
        lambda lock_id, loan_id, state=None: calls.append((lock_id, loan_id)),
    )
    request = _FakeRequest({"loan_id": LOAN_ID, "_loan_lock_id": LOCK_ID})

    async def handler(req):
        raise RuntimeError("async model call blew up")

    with pytest.raises(RuntimeError):
        asyncio.run(middleware.awrap_model_call(request, handler))

    assert calls == [(LOCK_ID, LOAN_ID)]


def test_awrap_tool_call_releases_lock_on_exception_and_reraises(monkeypatch, middleware):
    import asyncio

    calls = []
    monkeypatch.setattr(
        encompass_client, "unlock_resource",
        lambda lock_id, loan_id, state=None: calls.append((lock_id, loan_id)),
    )
    request = _FakeRequest({"loan_id": LOAN_ID, "_loan_lock_id": LOCK_ID})

    async def handler(req):
        raise RuntimeError("async tool call blew up")

    with pytest.raises(RuntimeError):
        asyncio.run(middleware.awrap_tool_call(request, handler))

    assert calls == [(LOCK_ID, LOAN_ID)]


# ═══════════════════════════════════════════════════════════════════════
# resolve_plan_for_step — the loan-lock halt-before-first-turn regression.
#
# LoanLockMiddleware.before_agent can force current_step="COMPLETED" on a
# brand-new thread's very first turn (loan already locked). Before this
# fix, resolve_plan_for_step returned None for any COMPLETED state,
# assuming (correctly for a *naturally* finished run, wrongly here) that
# messages already has prior turns. DynamicPlanMiddleware only prepends
# when plan_resolver returns non-None, so None + already-empty messages
# meant the model got called with messages=[] -> Anthropic 400
# "messages: at least one message is required".
# ═══════════════════════════════════════════════════════════════════════

def test_resolve_plan_for_step_returns_none_when_naturally_completed_with_history():
    """A run that legitimately finished after real turns must not get a
    synthetic halt message injected — None is correct there."""
    state = {
        "current_step": "COMPLETED",
        "messages": [{"type": "human", "content": "start review"}],
    }
    assert proc_agent.resolve_plan_for_step(state) is None


def test_resolve_plan_for_step_injects_message_when_halted_before_first_turn():
    """The bug this regression guards: COMPLETED forced by a lock-halt on a
    fresh thread with zero prior messages must still get *something* so
    the model call never sees an empty messages list."""
    state = {
        "current_step": "COMPLETED",
        "messages": [],
        "flags": [{
            "substep": "0",
            "title": "Loan Locked in Encompass",
            "severity": "error",
            "details": "Loan 12345678 is already locked by Test Admin.",
            "resolved": False,
        }],
    }
    plan = proc_agent.resolve_plan_for_step(state)

    assert plan is not None
    assert "Test Admin" in plan
    assert "STOP" in plan


def test_resolve_plan_for_step_injects_generic_message_when_no_flags():
    """Belt-and-suspenders: even without a flags entry, a fresh/empty
    COMPLETED state must not fall back to None."""
    state = {"current_step": "COMPLETED", "messages": []}
    assert proc_agent.resolve_plan_for_step(state) is not None
