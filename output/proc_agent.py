"""ProcessorAgent — Processor Submission Workflow Agent.

Orchestrates the full loan submission workflow for a mortgage processor,
from pre-checks through final UW submission and notifications.
"""
# ruff: noqa: E402  — sys.path must be configured before registry/step_loader imports

import dataclasses
import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Annotated, Any, NotRequired

from dotenv import load_dotenv
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

load_dotenv()

# ═══════════════════════════════════════════════════════════════════════
# Force minimum recursion limit
# ═══════════════════════════════════════════════════════════════════════
_MIN_RECURSION_LIMIT = int(os.getenv("LANGGRAPH_DEFAULT_RECURSION_LIMIT", "100000"))

import langgraph._internal._config as _lg_cfg

_lg_cfg.DEFAULT_RECURSION_LIMIT = _MIN_RECURSION_LIMIT

_original_merge_configs = _lg_cfg.merge_configs


def _patched_merge_configs(*configs):
    result = _original_merge_configs(*configs)
    if result.get("recursion_limit", 0) < _MIN_RECURSION_LIMIT:
        result["recursion_limit"] = _MIN_RECURSION_LIMIT
    return result


_lg_cfg.merge_configs = _patched_merge_configs

try:
    import langgraph.pregel.main as _pregel_main
    if hasattr(_pregel_main, "merge_configs"):
        _pregel_main.merge_configs = _patched_merge_configs
except Exception:
    pass

# Add project root to path for shared/ imports
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
PARENT_ROOT = os.path.dirname(PROJECT_ROOT)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
if PARENT_ROOT not in sys.path:
    sys.path.insert(0, PARENT_ROOT)

from copilotagent import create_deep_agent
from langchain.agents.middleware.types import AgentMiddleware, AgentState, OmitFromInput
from langgraph.graph.message import add_messages
from langgraph.types import Command as LgCommand

logger = logging.getLogger(__name__)

try:
    from copilotkit import CopilotKitMiddleware
    HAS_COPILOTKIT = True
except ImportError:
    HAS_COPILOTKIT = False
    CopilotKitMiddleware = None  # type: ignore[assignment,misc]
    logger.info("[INIT] copilotkit not found — running without CopilotKit middleware")

from registry import (
    STEP_ORDER,
    is_step_skipped,
    get_next_step,
    get_step_tools_excluding_skipped,
    generate_workflow_overview,
    SKIP_STEP_TOOLS,
)
from step_loader import load_plan_content

MAX_TOOL_RESULT_SIZE = 15000


# ═══════════════════════════════════════════════════════════════════════
# Reducers
# ═══════════════════════════════════════════════════════════════════════

def merge_dicts(existing: dict | None, new: dict | None) -> dict:
    """Deep merge two dicts."""
    if existing is None:
        return new or {}
    if new is None:
        return existing
    result = {**existing}
    for key, value in new.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = merge_dicts(result[key], value)
        else:
            result[key] = value
    return result


def last_value_reducer(existing: Any | None, new: Any | None) -> Any | None:
    return new if new is not None else existing


def _load_substep_names() -> dict[str, str]:
    if hasattr(_load_substep_names, "_cache"):
        return _load_substep_names._cache
    names: dict[str, str] = {}
    try:
        cfg_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "config", "workflow_config.json"
        )
        with open(cfg_path, "r") as f:
            cfg = json.load(f)
        for step_id, step_data in cfg.get("steps", {}).items():
            step_num = str(int(step_id.replace("STEP_", "")))
            for ss_key, ss_data in (step_data.get("substeps") or {}).items():
                name = ss_data.get("name", "")
                if name:
                    names[f"{step_num}.{ss_key}"] = name
    except Exception:
        pass
    _load_substep_names._cache = names
    return names


def dedupe_flags(existing: list | None, new: list | None) -> list:
    """Merge flags with deduplication by (substep, title)."""
    substep_names = _load_substep_names()
    all_flags = (existing or []) + (new or [])
    order: list[tuple[str, str]] = []
    by_key: dict[tuple[str, str], dict] = {}
    for flag in all_flags:
        if not isinstance(flag, dict):
            continue
        key = (flag.get("substep", ""), flag.get("title", ""))
        if "resolved" not in flag:
            flag["resolved"] = False
        if "timestamp" not in flag:
            flag["timestamp"] = datetime.now(timezone.utc).isoformat()
        flag.pop("hard_stop", None)
        ss = flag.get("substep", "")
        if ss and "substep_name" not in flag:
            if " — " in ss:
                parts = ss.split(" — ", 1)
                flag["substep"] = parts[0]
                flag["substep_name"] = parts[1]
            else:
                flag["substep_name"] = substep_names.get(ss, "")
        if key not in by_key:
            by_key[key] = flag
            order.append(key)
        else:
            prev = by_key[key]
            if not prev.get("resolved") and flag.get("resolved"):
                by_key[key] = flag
    return [by_key[k] for k in order]


def append_list(existing: list | None, new: list | None) -> list:
    return (existing or []) + (new or [])


def merge_manual_fields(existing: list | None, new: list | None) -> list:
    """Merge manual-entry field rows by (substep, field_id) — latest wins.

    manual_fields rows mark Encompass fields the agent deliberately did NOT
    write (judgment calls, unknown values, held writes). The dashboard's Field
    Writes tab renders them as empty editable rows next to the agent-written
    ledger entries.
    """
    all_rows = (existing or []) + (new or [])
    order: list[tuple[str, str]] = []
    by_key: dict[tuple[str, str], dict] = {}
    for row in all_rows:
        if not isinstance(row, dict):
            continue
        key = (row.get("substep", ""), row.get("field_id", ""))
        if key not in by_key:
            order.append(key)
        by_key[key] = row
    return [by_key[k] for k in order]


# Runtime/progress fields on a comms action item that must survive a re-run
# (i.e. NOT be clobbered by a freshly-derived item with the same id). Static
# fields (component, action_type, title, description, trigger, severity, …)
# always take the latest derived value.
_COMMS_ACTION_RUNTIME_KEYS = ("status", "result", "thread_id", "triggered_at")


def merge_comms_actions(existing: list | None, new: list | None) -> list:
    """Merge component-agnostic comms action items by ``id``.

    - De-dupes by ``id`` (no duplicate cards across partial re-runs).
    - Preserves runtime/progress fields (status, result, thread_id, …) set on a
      prior pass so re-deriving the same action doesn't reset a triggered one.
    - Static fields are refreshed from the newest derivation.
    """
    by_id: dict[str, dict] = {}
    order: list[str] = []

    def _ingest(items: list | None, is_new: bool) -> None:
        for item in items or []:
            if not isinstance(item, dict):
                continue
            aid = item.get("id")
            if not aid:
                continue
            if aid not in by_id:
                by_id[aid] = dict(item)
                order.append(aid)
            elif is_new:
                preserved = {
                    k: by_id[aid][k]
                    for k in _COMMS_ACTION_RUNTIME_KEYS
                    if by_id[aid].get(k) is not None
                }
                by_id[aid] = {**by_id[aid], **item, **preserved}

    _ingest(existing, is_new=False)
    _ingest(new, is_new=True)
    return [by_id[a] for a in order]


def truncate_messages(existing: list | None, new: list | None) -> list:
    merged = add_messages(existing, new)
    truncated = []
    for msg in merged:
        if isinstance(msg, ToolMessage):
            content = msg.content
            if isinstance(content, str) and len(content) > MAX_TOOL_RESULT_SIZE:
                truncated_content = content[:MAX_TOOL_RESULT_SIZE] + "\n... [TRUNCATED]"
                truncated.append(ToolMessage(
                    content=truncated_content,
                    tool_call_id=msg.tool_call_id,
                    name=msg.name if hasattr(msg, "name") else None,
                ))
            else:
                truncated.append(msg)
        else:
            truncated.append(msg)
    return truncated


# ═══════════════════════════════════════════════════════════════════════
# State Schema
# ═══════════════════════════════════════════════════════════════════════

class ProcessorAgentState(AgentState):
    """State schema for ProcessorAgent."""

    # ── Input fields ──
    loan_number: str
    env: str
    almas_notes: NotRequired[str]
    almas_notes_images: NotRequired[list]
    processor_name: NotRequired[str]
    additional_info: NotRequired[dict]
    force_extract: NotRequired[bool]

    # ── Core data (set in Step 0) ──
    loan_id: Annotated[NotRequired[str], OmitFromInput, last_value_reducer]

    # ── Encompass resource lock (held for the whole run by LoanLockMiddleware) ──
    # NOTE: cleared to "" (not None) on release — last_value_reducer treats a
    # None update as "keep existing" (see last_value_reducer above), so None
    # can never clear a channel once set. "" is falsy for every truthy check
    # in this file (`if state.get("_loan_lock_id")`) while still round-tripping
    # through the same reducer as every other Annotated field here.
    _loan_lock_id: Annotated[NotRequired[str | None], OmitFromInput, last_value_reducer]
    los_fields: Annotated[NotRequired[dict], OmitFromInput, merge_dicts]
    doc_fields: Annotated[NotRequired[dict], OmitFromInput, merge_dicts]
    efolder_documents: Annotated[NotRequired[dict], OmitFromInput, merge_dicts]
    loan_summary: Annotated[NotRequired[dict], OmitFromInput, last_value_reducer]
    loan_profile: Annotated[NotRequired[dict], OmitFromInput, last_value_reducer]
    address_validation: Annotated[NotRequired[dict], OmitFromInput, last_value_reducer]
    property_verification: Annotated[NotRequired[dict], OmitFromInput, last_value_reducer]
    vod_data: Annotated[NotRequired[list], OmitFromInput, last_value_reducer]

    # ── Collections read channels (dashboard Field Writes tab tables) ──
    # Raw Encompass collection rows emitted by the review tools that fetch
    # them, so the dashboard can render editable VOD/VOL/VOE/file-contact tables
    # (writes go through the stateless write_los_collections graph).
    vods: Annotated[NotRequired[list], OmitFromInput, last_value_reducer]
    vols: Annotated[NotRequired[list], OmitFromInput, last_value_reducer]
    voes: Annotated[NotRequired[list], OmitFromInput, last_value_reducer]
    file_contacts: Annotated[NotRequired[list], OmitFromInput, last_value_reducer]

    # ── Issues and tracking ──
    flags: Annotated[NotRequired[list[dict]], OmitFromInput, dedupe_flags]
    pending_field_updates: Annotated[NotRequired[list[dict]], OmitFromInput]

    # ── Communications action items (component-agnostic; see build_action_items) ──
    comms_actions: Annotated[NotRequired[list[dict]], OmitFromInput, merge_comms_actions]

    # ── Mavent ECS results (STEP_13.2; full messages for dashboard panel) ──
    mavent_verification: Annotated[NotRequired[dict], OmitFromInput, last_value_reducer]
    mavent_results: Annotated[NotRequired[dict], OmitFromInput, last_value_reducer]

    # ── Workflow progress ──
    current_step: Annotated[NotRequired[str | None], OmitFromInput, last_value_reducer]
    current_substep: Annotated[NotRequired[str | None], OmitFromInput, last_value_reducer]
    workflow_plan: Annotated[NotRequired[dict | None], OmitFromInput, last_value_reducer]
    dynamic_skipping: Annotated[NotRequired[list[str]], OmitFromInput, last_value_reducer]
    step_reports: Annotated[NotRequired[dict[str, dict]], OmitFromInput, merge_dicts]
    step_fullReports: Annotated[NotRequired[dict[str, dict]], OmitFromInput, merge_dicts]

    # ── Files and UI ──
    loan_files: Annotated[NotRequired[dict[str, dict]], OmitFromInput, merge_dicts]

    # ── Field-writes ledger ──
    field_writes_ledger: Annotated[NotRequired[list[dict]], OmitFromInput, append_list]

    # ── Manual-entry fields (agent deliberately did not write; user fills in UI) ──
    manual_fields: Annotated[NotRequired[list[dict]], OmitFromInput, merge_manual_fields]

    # ── Substep timeout tracking ──
    substep_started_at: Annotated[NotRequired[str | None], OmitFromInput, last_value_reducer]

    # ── Audit ──
    step_start_message_index: Annotated[NotRequired[int], OmitFromInput, last_value_reducer]
    conversation_summary: Annotated[NotRequired[str], OmitFromInput, last_value_reducer]
    _summarized_up_to_index: Annotated[NotRequired[int], OmitFromInput, last_value_reducer]
    notes: Annotated[NotRequired[list[dict]], OmitFromInput]

    # ── Messages ──
    messages: Annotated[list, truncate_messages]


# ═══════════════════════════════════════════════════════════════════════
# Step Detection
# ═══════════════════════════════════════════════════════════════════════

def get_current_step_from_state(state: dict) -> str:
    current_step = state.get("current_step")

    if current_step == "COMPLETED":
        return "COMPLETED"

    if current_step:
        step_reports = state.get("step_reports", {})
        step_status = step_reports.get(f"{current_step}_status", {}).get("status")

        if step_status in ("completed", "skipped"):
            candidate = get_next_step(current_step)
            while candidate:
                cand_status = step_reports.get(f"{candidate}_status", {}).get("status")
                if cand_status not in ("completed", "skipped"):
                    return candidate
                candidate = get_next_step(candidate)
            return "COMPLETED"
        return current_step

    step_reports = state.get("step_reports", {})

    for key, value in step_reports.items():
        if key.endswith("_status") and isinstance(value, dict):
            parts = key.replace("_status", "").split("_")
            if len(parts) == 2 and parts[0] == "STEP":
                if value.get("status") == "in_progress":
                    return value.get("step_id", "STEP_00")

    for key, value in step_reports.items():
        if key.endswith("_status") and isinstance(value, dict):
            if value.get("status") == "in_progress":
                step_id = value.get("step_id")
                if step_id:
                    return step_id

    completed_nums = set()
    for key, value in step_reports.items():
        if key.endswith("_status") and isinstance(value, dict):
            parts = key.replace("_status", "").split("_")
            if len(parts) == 2 and parts[0] == "STEP" and value.get("status") == "completed":
                step_id = value.get("step_id")
                if step_id:
                    try:
                        completed_nums.add(int(step_id.replace("STEP_", "")))
                    except ValueError:
                        pass

    if completed_nums:
        last_num = max(completed_nums)
        next_num = last_num + 1
        max_step = max(int(s.replace("STEP_", "")) for s in STEP_ORDER)
        if next_num <= max_step:
            return f"STEP_{next_num:02d}"
        return "COMPLETED"

    return "STEP_00"


# ═══════════════════════════════════════════════════════════════════════
# Resolvers
# ═══════════════════════════════════════════════════════════════════════

_ALL_TOOLS_REF = None

# Dashboard-triggerable one-off reruns (additional_info.action → tool name).
# Honored regardless of workflow position — even COMPLETED threads — so the
# dashboard's "Rerun Mavent Compliance Check" action item works after the
# review finishes. The targeted tool clears the action from additional_info
# when it runs (see _consume_targeted_action in the tool) so subsequent runs
# resume the normal workflow.
TARGETED_ACTION_TOOLS: dict[str, str] = {
    "run_mavent_compliance": "run_mavent_compliance",
}


def get_targeted_action(state: dict) -> str | None:
    """Tool name for a pending dashboard-targeted rerun, if any."""
    action = ((state.get("additional_info") or {}).get("action") or "").strip()
    return TARGETED_ACTION_TOOLS.get(action)


def get_completed_targeted_action(state: dict) -> str | None:
    """Tool name for a targeted rerun whose tool has already run this turn.

    The targeted tool swaps additional_info.action for action_completed when
    it returns (see _consume_targeted_action in the tool). The marker keeps
    the follow-up summary turn in one-shot mode — no workflow plan/tools, no
    WorkflowGuard nudge — and WorkflowGuardMiddleware.after_agent clears it
    once the run finalizes.
    """
    action = ((state.get("additional_info") or {}).get("action_completed") or "").strip()
    return TARGETED_ACTION_TOOLS.get(action)


def resolve_tools_for_step(state: dict) -> list:
    global _ALL_TOOLS_REF

    if _ALL_TOOLS_REF is None:
        logger.warning("[TOOL_RESOLVER] _ALL_TOOLS_REF not initialized")
        return []

    targeted = get_targeted_action(state) or get_completed_targeted_action(state)
    if targeted:
        # Completed marker keeps the summary turn scoped too: returning [] would
        # trip DynamicToolMiddleware's fallback to ALL tools, so keep exposing
        # only the targeted tool (the plan forbids calling it again).
        targeted_tools = [t for t in _ALL_TOOLS_REF
                          if getattr(t, "name", getattr(t, "__name__", "")) == targeted]
        if targeted_tools:
            logger.info(f"[TOOL_RESOLVER] Targeted rerun — exposing only '{targeted}'")
            return targeted_tools
        logger.warning(f"[TOOL_RESOLVER] Targeted action tool '{targeted}' not found")

    current_step = get_current_step_from_state(state)

    if current_step == "COMPLETED":
        completed_tools = [t for t in _ALL_TOOLS_REF
                           if getattr(t, "name", getattr(t, "__name__", "")) in SKIP_STEP_TOOLS]
        return completed_tools

    if is_step_skipped(current_step):
        skip_tools = [t for t in _ALL_TOOLS_REF
                      if getattr(t, "name", getattr(t, "__name__", "")) in SKIP_STEP_TOOLS]
        return skip_tools

    dynamic_skipping = set(state.get("dynamic_skipping", []) or [])
    allowed_names = get_step_tools_excluding_skipped(
        current_step, extra_skip_substeps=dynamic_skipping
    )
    filtered = [t for t in _ALL_TOOLS_REF
                if getattr(t, "name", getattr(t, "__name__", "")) in allowed_names]
    return filtered


def resolve_plan_for_step(state: dict) -> str | None:
    targeted = get_targeted_action(state)
    if targeted:
        force_hint = (
            " with `force_refresh=True` so a fresh report is generated"
            if targeted == "run_mavent_compliance" else ""
        )
        return (
            "## Targeted Rerun (dashboard request)\n\n"
            f"The dashboard requested a one-off rerun of `{targeted}`.\n"
            f"1. Call `{targeted}` now{force_hint}.\n"
            "2. After the tool returns, summarize the outcome in one short "
            "sentence and STOP. Do NOT continue the workflow or call any "
            "other tool."
        )

    completed = get_completed_targeted_action(state)
    if completed:
        return (
            "## Targeted Rerun — Completed\n\n"
            f"The dashboard-requested rerun of `{completed}` has finished.\n"
            "Summarize the tool outcome in one short sentence and STOP. "
            "Do NOT continue the workflow or call any other tool."
        )

    current_step = get_current_step_from_state(state)

    if current_step == "COMPLETED":
        if not state.get("messages"):
            # A legitimately-finished run always has prior turns by the time
            # it reaches COMPLETED, so returning None here is normally fine
            # (DynamicPlanMiddleware only prepends to an already-nonempty
            # messages list). But LoanLockMiddleware.before_agent can also
            # force current_step="COMPLETED" on a brand-new thread's very
            # first turn (loan locked at start — see
            # processor-assistant-orchestrator/docs/encompass_resource_locking_plan.md).
            # In that case messages is genuinely empty, None here would leave
            # it empty, and the model call 400s ("messages: at least one
            # message is required"). Return a minimal instruction instead so
            # there's always at least one message.
            flags = state.get("flags") or []
            detail = flags[-1].get("details") if flags else None
            return (
                "## Run halted before starting\n\n"
                f"{detail or 'The run could not start.'}\n\n"
                "Do not call any tool. Summarize this in one short sentence "
                "and STOP."
            )
        return None
    if is_step_skipped(current_step):
        return None

    try:
        return load_plan_content(current_step)
    except Exception as e:
        logger.warning(f"[PLAN_RESOLVER] Could not load plan for {current_step}: {e}")
        return None


# ═══════════════════════════════════════════════════════════════════════
# Middleware
# ═══════════════════════════════════════════════════════════════════════

class ProcessorAgentMiddleware(AgentMiddleware):
    state_schema = ProcessorAgentState


class SystemMessageNormalizerMiddleware(AgentMiddleware):
    """Moves all system messages to the front before each model call."""

    async def awrap_model_call(self, request, handler):
        messages = list(request.messages)
        system_msgs = [m for m in messages if getattr(m, "type", None) in ("system", "developer")]
        non_system_msgs = [m for m in messages if getattr(m, "type", None) not in ("system", "developer")]
        if len(system_msgs) > 1 or (system_msgs and non_system_msgs and messages.index(system_msgs[0]) > 0):
            request = request.override(messages=system_msgs + non_system_msgs)
        return await handler(request)

    def wrap_model_call(self, request, handler):
        messages = list(request.messages)
        system_msgs = [m for m in messages if getattr(m, "type", None) in ("system", "developer")]
        non_system_msgs = [m for m in messages if getattr(m, "type", None) not in ("system", "developer")]
        if len(system_msgs) > 1 or (system_msgs and non_system_msgs and messages.index(system_msgs[0]) > 0):
            request = request.override(messages=system_msgs + non_system_msgs)
        return handler(request)


class FieldWritesLedgerMiddleware(AgentMiddleware):
    """Drains the Encompass field-writes ledger into thread state.

    write_fields()/_write_fields() append receipts to a module-level list in
    shared.encompass_io. Historically only the stateless write graphs drained
    it (flush_field_writes_ledger), so writes made by review tools never
    reached the thread's field_writes_ledger channel and the dashboard's
    Field Writes tab looked incomplete. This middleware drains the ledger
    after EVERY tool call and merges the rows into the tool's Command update
    (the channel reducer is append_list, so rows accumulate run-wide).
    """

    @staticmethod
    def _merge_ledger(result, loan_id):
        from shared.encompass_io import flush_field_writes_ledger
        # Scoped flush: only this run's loan (plus unattributable entries) is
        # drained, so concurrent runs in the same process don't steal each
        # other's receipts.
        ledger = flush_field_writes_ledger(loan_id)
        if not ledger:
            return result
        if isinstance(result, LgCommand):
            update = dict(result.update or {})
            update["field_writes_ledger"] = (
                list(update.get("field_writes_ledger") or []) + ledger
            )
            return dataclasses.replace(result, update=update)
        # Plain ToolMessage — wrap it so the ledger rows still reach state.
        return LgCommand(update={"messages": [result], "field_writes_ledger": ledger})

    @staticmethod
    def _loan_id_from(request) -> str:
        # Empty string (not None) when the run has no loan yet: the scoped
        # flush then drains only unattributable entries instead of everything.
        state = getattr(request, "state", None) or {}
        return state.get("loan_id") or ""

    def wrap_tool_call(self, request, handler):
        return self._merge_ledger(handler(request), self._loan_id_from(request))

    async def awrap_tool_call(self, request, handler):
        return self._merge_ledger(await handler(request), self._loan_id_from(request))


class LoanLockMiddleware(AgentMiddleware):
    """Holds one Encompass Exclusive resource lock for the whole agent run.

    Acquired in before_agent — before any tool call is possible, including
    Step 0's own loan resolution — and released in after_agent. Placement in
    the middleware list matters: before_agent hooks run in list order and
    after_agent hooks run in REVERSE list order (LangChain's factory chains
    the entry node from the first middleware with a before_agent hook, and
    the exit node from the last one backwards), so this middleware must be
    FIRST in the list *we* pass to create_deep_agent (see create_agent()
    below) to (a) gate before any of our own middleware's before_agent and
    (b) release the lock LAST among ours, after every other middleware's
    after_agent (e.g. WorkflowGuardMiddleware's) has already run.
    (`copilotagent.create_deep_agent` itself prepends its own built-in
    middleware — filesystem/tool-patching/etc. — ahead of whatever we pass;
    none of those touch Encompass, so that's fine.)

    Fail-fast, never steals a foreign lock (a human in Encompass desktop, or
    another of our own runs holding it): a 409 halts the run gracefully via
    a `flags` entry + `current_step="COMPLETED"` (the same mechanism the
    dashboard already uses to know a run finished, see
    get_current_step_from_state / resolve_tools_for_step above) rather than
    raising and erroring the whole LangGraph run. See
    processor-assistant-orchestrator/docs/encompass_resource_locking_plan.md.
    """

    def before_agent(self, state: dict, runtime) -> dict | None:
        from encompass_client import LoanLockedError, lock_resource

        update: dict = {}
        loan_id = state.get("loan_id")

        if not loan_id:
            loan_number = state.get("loan_number")
            if not loan_number:
                # No loan_number yet either — nothing to lock against. Let
                # the normal workflow run (it will fail its own way at
                # Step 0); we're not in a position to gate anything here.
                return None
            # Resolve via the SAME tool Step 0 uses, so this never drifts
            # from the real resolution logic (GUID passthrough, borrower-name
            # fallback, hallucination guards, etc. — see find_loan).
            from tools.data_gathering import find_loan
            resolution = find_loan.func(
                tool_call_id="_loan_lock_middleware",
                state=state,
                loan_number=loan_number,
            )
            resolved = (
                (resolution.update or {}).get("loan_id")
                if isinstance(resolution, LgCommand) else None
            )
            if not resolved:
                # Resolution failed (not found, Encompass error, etc.) — let
                # Step 0's own find_loan call surface that the normal way
                # instead of duplicating its error handling here.
                return None
            loan_id = resolved
            update["loan_id"] = loan_id

        if state.get("_loan_lock_id"):
            # Already held — a checkpoint replay/resume of before_agent
            # within a run that already acquired the lock earlier. Do NOT
            # re-acquire (we'd just 409 on our own lock).
            return update or None

        try:
            lock_id = lock_resource(loan_id, state=state)
        except LoanLockedError as e:
            logger.warning(f"[LOAN_LOCK] Loan {loan_id[:8]} locked — halting run: {e}")
            update["flags"] = [{
                "substep": "0",
                "title": "Loan Locked in Encompass",
                "severity": "error",
                "details": str(e),
                "suggestion": (
                    "Close the loan in Encompass desktop (or wait for the "
                    "other run to finish) and retry."
                ),
                "resolved": False,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }]
            # Mirrors how STEP completion marks the run done (see
            # update_processor_workflow / general.py's step-completion
            # handler) so the dashboard doesn't show "in progress" forever
            # and resolve_tools_for_step/WorkflowGuardMiddleware stop
            # exposing write tools for the rest of this run.
            update["current_step"] = "COMPLETED"
            # A targeted rerun (e.g. Mavent) takes priority over current_step
            # in resolve_tools_for_step (see get_targeted_action there) — so
            # current_step="COMPLETED" alone would NOT stop the model from
            # still being handed the targeted write tool. Clear the pending
            # action too (same "drop a completed/dead key" pattern as
            # WorkflowGuardMiddleware.after_agent above) so this halt is
            # airtight for both the normal workflow AND a targeted rerun.
            info = state.get("additional_info") or {}
            if info.get("action"):
                update["additional_info"] = {k: v for k, v in info.items() if k != "action"}
            return update

        update["_loan_lock_id"] = lock_id
        logger.info(f"[LOAN_LOCK] Acquired lock {lock_id[:8]} for loan {loan_id[:8]}")
        return update

    def after_agent(self, state: dict, runtime) -> dict | None:
        from encompass_client import unlock_resource

        lock_id = state.get("_loan_lock_id")
        loan_id = state.get("loan_id")
        if not lock_id or not loan_id:
            return None
        unlock_resource(lock_id, loan_id, state=state)
        logger.info(f"[LOAN_LOCK] Released lock {lock_id[:8]} for loan {loan_id[:8]}")
        # "" not None — see the field comment on ProcessorAgentState;
        # last_value_reducer never lets a None update clear a channel.
        return {"_loan_lock_id": ""}

    @staticmethod
    def _release_on_escape(state: dict) -> None:
        """Best-effort release for paths that skip after_agent entirely: an
        uncaught exception from a model/tool call, or the graceful substep
        timeout in WorkflowGuardMiddleware._check_substep_timeout (which
        halts via `langgraph.types.interrupt`, a GraphInterrupt — a normal
        Exception subclass, so it's caught by the `except Exception` below
        same as any other escape). Relies on this middleware being first
        among ours (see the class docstring), so its wrap_model_call /
        wrap_tool_call are the outermost layer and their `handler(request)`
        call encloses WorkflowGuardMiddleware's timeout check.

        This only guarantees the *real* Encompass lock is dropped — it
        can't also clear the checkpointed `_loan_lock_id` (that requires a
        normal state-update return, unavailable here since we're mid-
        exception and must re-raise). A resumed run on this thread will
        still see `_loan_lock_id` set and skip re-acquiring in before_agent;
        that's the same "crashed span leaks, recovered via dashboard
        force-unlock" trade-off already accepted for `loan_lock` elsewhere
        (see encompass_client.py's docstring there).
        """
        lock_id = state.get("_loan_lock_id")
        loan_id = state.get("loan_id")
        if not lock_id or not loan_id:
            return
        from encompass_client import unlock_resource
        unlock_resource(lock_id, loan_id, state=state)
        logger.info(
            f"[LOAN_LOCK] Released lock {lock_id[:8]} for loan {loan_id[:8]} "
            "(exception/interrupt escape, before_agent/after_agent bypassed)"
        )

    def wrap_model_call(self, request, handler):
        try:
            return handler(request)
        except Exception:
            self._release_on_escape(request.state)
            raise

    async def awrap_model_call(self, request, handler):
        try:
            return await handler(request)
        except Exception:
            self._release_on_escape(request.state)
            raise

    def wrap_tool_call(self, request, handler):
        try:
            return handler(request)
        except Exception:
            self._release_on_escape(request.state)
            raise

    async def awrap_tool_call(self, request, handler):
        try:
            return await handler(request)
        except Exception:
            self._release_on_escape(request.state)
            raise


class WorkflowGuardMiddleware(AgentMiddleware):
    """Prevents premature agent termination from text-only responses."""

    MAX_RETRIES = 2
    SUBSTEP_TIMEOUT_SECONDS = 600

    def _is_workflow_done(self, state: dict) -> bool:
        # Targeted rerun: a text-only summary after the tool call is the
        # desired terminal response — don't nudge the model to keep working.
        # The tool swaps `action` for `action_completed` when it runs, so both
        # markers must count; otherwise the summary turn (action already
        # cleared) would be nudged back into the normal workflow.
        if get_targeted_action(state) or get_completed_targeted_action(state):
            return True
        return get_current_step_from_state(state) == "COMPLETED"

    def after_agent(self, state: dict, runtime) -> dict | None:
        """Clear the one-shot targeted-rerun marker once the run finalizes.

        additional_info is a last-value channel: without this, the completed
        marker would persist in thread state and every subsequent run would
        skip the workflow guard and the normal plan/tool resolution.
        """
        info = state.get("additional_info") or {}
        if not info.get("action_completed"):
            return None
        return {
            "additional_info": {
                k: v for k, v in info.items() if k != "action_completed"
            }
        }

    def _check_substep_timeout(self, state: dict):
        started_at = state.get("substep_started_at")
        if not started_at:
            return
        try:
            start_dt = datetime.fromisoformat(started_at)
            elapsed = (datetime.now(timezone.utc) - start_dt).total_seconds()
        except (ValueError, TypeError):
            return
        if elapsed > self.SUBSTEP_TIMEOUT_SECONDS:
            step = state.get("current_step", "unknown")
            substep = state.get("current_substep", "unknown")
            elapsed_min = elapsed / 60
            from langgraph.types import interrupt
            interrupt(
                f"TIMEOUT: Substep {substep} in {step} has been running for "
                f"{elapsed_min:.1f} minutes (limit: {self.SUBSTEP_TIMEOUT_SECONDS // 60} min). "
                f"The run has been halted."
            )

    @staticmethod
    def _get_ai_message(result):
        if isinstance(result, AIMessage):
            return result
        if hasattr(result, "result") and result.result:
            return result.result[0]
        return None

    def _has_tool_calls(self, result) -> bool:
        ai_msg = self._get_ai_message(result)
        return bool(ai_msg and getattr(ai_msg, "tool_calls", None))

    def _nudge_message(self, state: dict) -> HumanMessage:
        step = state.get("current_step", "unknown")
        substep = state.get("current_substep", "unknown")
        return HumanMessage(
            content=(
                f"You are at {step} substep {substep}. The workflow is NOT complete. "
                "You MUST call the designated tool for this substep. "
                "Do NOT say 'tool unavailable' — all tools in your tool set ARE available. "
                "Call the tool now."
            ),
            additional_kwargs={"lc_source": "workflow_guard"},
        )

    def wrap_model_call(self, request, handler):
        self._check_substep_timeout(request.state)
        result = handler(request)
        if self._has_tool_calls(result) or self._is_workflow_done(request.state):
            return result
        for attempt in range(1, self.MAX_RETRIES + 1):
            self._check_substep_timeout(request.state)
            ai_msg = self._get_ai_message(result)
            nudge = self._nudge_message(request.state)
            retry_messages = list(request.messages)
            if ai_msg is not None:
                retry_messages.append(ai_msg)
            retry_messages.append(nudge)
            request = request.override(messages=retry_messages)
            result = handler(request)
            if self._has_tool_calls(result) or self._is_workflow_done(request.state):
                return result
        return result

    async def awrap_model_call(self, request, handler):
        self._check_substep_timeout(request.state)
        result = await handler(request)
        if self._has_tool_calls(result) or self._is_workflow_done(request.state):
            return result
        for attempt in range(1, self.MAX_RETRIES + 1):
            self._check_substep_timeout(request.state)
            ai_msg = self._get_ai_message(result)
            nudge = self._nudge_message(request.state)
            retry_messages = list(request.messages)
            if ai_msg is not None:
                retry_messages.append(ai_msg)
            retry_messages.append(nudge)
            request = request.override(messages=retry_messages)
            result = await handler(request)
            if self._has_tool_calls(result) or self._is_workflow_done(request.state):
                return result
        return result


# ═══════════════════════════════════════════════════════════════════════
# Agent Creation
# ═══════════════════════════════════════════════════════════════════════

def create_agent():
    global _ALL_TOOLS_REF

    from tools import get_all_tools
    all_tools = get_all_tools()
    _ALL_TOOLS_REF = all_tools

    plans_dir = os.path.join(PROJECT_ROOT, "plans")
    with open(os.path.join(plans_dir, "system_prompt.template.md"), "r") as f:
        system_prompt = f.read()

    overview = generate_workflow_overview()
    system_prompt = system_prompt.replace("{{WORKFLOW_OVERVIEW}}", overview)

    # LoanLockMiddleware must be FIRST — see its class docstring for why
    # ordering matters (before_agent runs first / after_agent runs last).
    # ProcessorAgentMiddleware only registers state_schema (no before_agent/
    # after_agent hooks), so swapping it after LoanLockMiddleware is safe.
    middleware_stack = [LoanLockMiddleware(), ProcessorAgentMiddleware()]
    if HAS_COPILOTKIT:
        middleware_stack.append(CopilotKitMiddleware())
    middleware_stack.append(SystemMessageNormalizerMiddleware())
    middleware_stack.append(FieldWritesLedgerMiddleware())
    middleware_stack.append(WorkflowGuardMiddleware())

    agent = create_deep_agent(
        model="claude-sonnet-4-6",
        tools=all_tools,
        system_prompt=system_prompt,
        middleware=tuple(middleware_stack),
        tool_resolver=resolve_tools_for_step,
        plan_resolver=resolve_plan_for_step,
        name="processor_agent",
    )

    return agent


graph = create_agent()
graph.config["recursion_limit"] = _MIN_RECURSION_LIMIT
