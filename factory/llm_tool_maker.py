"""LLM Tool Maker — Uses Claude to generate tool code from step definitions.

Given a step YAML definition and an English description of the business logic,
calls Claude to produce production-quality Python tool code that follows
the exact patterns of the factory's tool template.

Key features:
- Injects the full shared tools catalog so Claude knows what utilities exist
- Enforces proper @tool descriptions so the agent model can select the right tool
- Supports LLM-within-tool patterns (shared.llm_call)

Usage:
    from factory.llm_tool_maker import generate_tool_code
    code = await generate_tool_code(step_def, substep_id="1.1", description="...")
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Optional

import anthropic
import yaml

from .schema import StepDef, SubstepDef
from .shared_tools_catalog import SHARED_TOOLS_CATALOG

if TYPE_CHECKING:
    from .field_registry import FieldRegistry

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────

MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 8192

TOOL_TEMPLATE = '''"""{{tool_name}} — Tool for substep {{substep_id}}: {{substep_name}}

Step {{step_number}} ({{step_id}}): {{step_name}}
Phase: {{phase}}
"""

import json
import logging
from datetime import datetime, timezone
from typing import Annotated, Optional

from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.prebuilt import InjectedState
from langgraph.types import Command

from ._helpers import _los, _doc

logger = logging.getLogger(__name__)
'''


SYSTEM_PROMPT = """You are an expert Python developer generating tool functions for a LangGraph workflow agent that processes mortgage loans.

## Critical: Tool Description Quality

The @tool decorated function's docstring is THE MOST IMPORTANT PART of the tool. The agent model reads this docstring to decide which tool to call. A bad description = the model picks the wrong tool or skips it.

### Rules for Tool Descriptions:

1. **First sentence**: Clear, specific statement of what the tool DOES (not what the step is about). This is the "headline" the model reads.
   - GOOD: "Check if the loan has Clear-to-Close status and verify CD sent date is within required timeframes."
   - BAD: "Preflight check tool." (too vague, model won't know when to use it)
   - BAD: "Runs preflight checks." (circular, doesn't say WHAT checks)

2. **Second sentence**: When/why the model should call this tool (the trigger condition).
   - GOOD: "Call this after loan data is loaded and before starting field verification."
   - BAD: "Substep 1.1 of STEP_01." (meaningless to the model)

3. **Args section**: List any fields the tool reads/writes, so the model knows the tool's data scope.
   - "Reads: ctc_status, cd_sent_date, lock_expiration, mortgage_type"
   - "Writes: field_corrections for lock-related fields"
   - "Flags: CTC Not Clear (critical), Lock Expired (blocking)"

4. **Never include**: Internal implementation details, Jinja template references, or raw field IDs in the top-level description. Keep it about WHAT and WHY, not HOW.

### Example of a PERFECT tool description:

```python
@tool
def verify_borrower_info(
    tool_call_id: Annotated[str, InjectedToolCallId],
    state: Annotated[dict, InjectedState],
) -> Command:
    \"\"\"Compare borrower name, DOB, and SSN between LOS fields and extracted documents.
    Flags mismatches and auto-corrects LOS when the document value is clearly more accurate.
    Call this after document extraction is complete for Driver's License and Credit Report.

    Reads LOS: borrower_first_name, borrower_last_name, borrower_dob, borrower_ssn
    Reads Docs: Driver's License (name, DOB), Credit Report (SSN)
    Flags: Borrower Name Mismatch (warning), SSN Mismatch (critical), DOB Mismatch (warning)
    Writes: field_corrections for empty/mismatched fields
    \"\"\"
```

## Important Rules

1. Every tool MUST use the exact signature pattern:
```python
@tool
def tool_name(
    tool_call_id: Annotated[str, InjectedToolCallId],
    state: Annotated[dict, InjectedState],
) -> Command:
```

2. Read LOS fields using `_los(state, "key_name")` and doc fields using `_doc(state, "key_name")`.

3. Every tool MUST return a `Command(update={...})` with at least a `messages` key containing a `ToolMessage`.

4. Flags are appended to the state's `flags` list. Each flag is a dict:
```python
{"title": "...", "severity": "critical|blocking|warning|info", "step_id": "STEP_XX", "substep_id": "X.X", "details": "...", "suggestion": "..."}
```

5. Field updates are written via state update:
```python
Command(update={
    "messages": [...],
    "flags": flags,
    "field_corrections": [{"field_id": "...", "value": "...", "reason": "..."}],
})
```

6. Use proper logging: `logger.info(f"[TOOL_NAME] ...")`

8. Handle errors gracefully — never let a tool crash. Wrap in try/except.

9. Implement REAL business logic based on the rules. No TODOs, no placeholders, no "pass" statements.

10. For date comparisons, parse with datetime. For string comparisons, normalize (strip, upper/lower).

11. When a subagent is needed, note it in a comment but don't call it — the agent orchestrator handles subagent calls.

12. **USE SHARED UTILITIES** from the catalog below. Don't reimplement what already exists. Especially use `shared.llm_call` for any fuzzy matching, text analysis, or judgment calls.

## Output Format

Return ONLY the Python code for a SINGLE tool function (one substep = one file).
No markdown fences, no explanations. Start with the imports, then the single @tool function.
Do NOT include the `_los` and `_doc` helpers — they are imported from `._helpers`.
Always include `from ._helpers import _los, _doc` in your imports.

""" + SHARED_TOOLS_CATALOG


# ── Core Generation ───────────────────────────────────────────────────


def _build_field_inventory(registry: FieldRegistry, step_id: str) -> str:
    """Build a comprehensive field inventory for Claude.

    This tells Claude exactly which fields are loaded into state at runtime
    (gathered by Step 0), so it can use _los() and _doc() with correct keys.
    """
    parts: list[str] = []
    parts.append("## Fields Available in State at Runtime")
    parts.append("")
    parts.append("Step 0 (Data Gathering) loads ALL of these into state BEFORE your tool runs.")
    parts.append("Use `_los(state, \"key\")` for LOS fields and `_doc(state, \"key\")` for doc fields.")
    parts.append("")

    # ── LOS Fields ──
    step_los = registry.get_los_fields_for_step(step_id)
    step_keys = {f.key for f in step_los}
    all_los = sorted(registry.los_fields_by_key.items(), key=lambda x: x[0])

    if step_los:
        parts.append("### LOS Fields Used by THIS Step (primary — your tool reads these)")
        parts.append("")
        parts.append("| Key | Field ID | Purpose |")
        parts.append("|-----|----------|---------|")
        for fref in step_los:
            purpose = fref.purpose or ""
            parts.append(f"| `{fref.key}` | {fref.field_id} | {purpose} |")
        parts.append("")

    # Show other fields available (from other steps) as secondary reference
    other_los = [(k, info) for k, info in all_los if k not in step_keys]
    if other_los:
        parts.append("### Other LOS Fields Available in State (from other steps)")
        parts.append("")
        parts.append("These are loaded by Step 0 for other steps. You CAN read them if needed.")
        parts.append("")
        parts.append("| Key | Field ID | Used By |")
        parts.append("|-----|----------|---------|")
        for key, info in other_los:
            used = ", ".join(info.used_by_steps[:3])
            parts.append(f"| `{key}` | {info.field_id} | {used} |")
        parts.append("")

    # ── Document Types & Doc Fields ──
    step_doc = registry.get_doc_fields_for_step(step_id)
    step_doc_keys = {f.key for f in step_doc}

    # Get doc types used by this step
    step_def = next((s for s in registry.steps if s.id == step_id), None)
    step_doc_types: set[str] = set()
    if step_def:
        for ss in step_def.substeps:
            for dt in ss.doc_types:
                step_doc_types.add(dt.document_type)

    if registry.doc_type_info:
        parts.append("### Document Types & Extracted Fields")
        parts.append("")
        for dt_name, dti in sorted(registry.doc_type_info.items()):
            marker = " **(used by this step)**" if dt_name in step_doc_types else ""
            copies = " (ALL COPIES)" if dti.all_copies else ""
            parts.append(f"**{dt_name}**{copies}{marker}")
            parts.append(f"  Fields: {', '.join(f'`{f}`' for f in dti.fields)}")
            parts.append("")

    # ── Quick Reference: all doc field keys ──
    if registry.doc_fields:
        other_doc = [(k, info) for k, info in sorted(registry.doc_fields.items()) if k not in step_doc_keys]
        if step_doc:
            parts.append("### Doc Fields Used by THIS Step")
            parts.append("")
            for fref in step_doc:
                info = registry.doc_fields.get(fref.key)
                src = ", ".join(info.source_documents) if info else "?"
                parts.append(f"- `{fref.key}` — from {src}")
            parts.append("")

        if other_doc:
            parts.append("### Other Doc Fields Available (from other steps)")
            parts.append("")
            for key, info in other_doc:
                src = ", ".join(info.source_documents[:2])
                parts.append(f"- `{key}` — from {src}")
            parts.append("")

    return "\n".join(parts)


def _build_substep_context(ss: SubstepDef) -> str:
    """Build a detailed context string for a substep."""
    parts = [f"### Substep {ss.id}: {ss.name}"]
    parts.append(f"Tool name: `{ss.tool}`")
    parts.append(f"Description: {ss.description}")

    if ss.los_fields_read:
        parts.append("\nLOS Fields:")
        for f in ss.los_fields_read:
            parts.append(f"  - `{f.key}` (field_id={f.field_id})")

    if ss.doc_types:
        parts.append("\nDoc Types:")
        for dt in ss.doc_types:
            copies = " (ALL COPIES)" if dt.all_copies else ""
            parts.append(f"  Document: {dt.document_type}{copies}")
            for f in dt.fields:
                parts.append(f"    - `{f.key}`: {f.purpose}")

    if ss.rules:
        parts.append("\nBusiness Rules:")
        for r in ss.rules:
            parts.append(f"  - [{r.type}] {r.name}: {r.logic}")
            if r.check:
                parts.append(f"    Check: `{r.check}`")
            if r.table:
                parts.append(f"    Lookup table: {yaml.dump(r.table, default_flow_style=True).strip()}")

    if ss.flags:
        parts.append("\nFlags to raise:")
        for fl in ss.flags:
            parts.append(f"  - [{fl.severity}] \"{fl.title}\": when {fl.condition} -> {fl.suggestion}")

    if ss.field_updates:
        parts.append("\nField updates to write:")
        for u in ss.field_updates:
            parts.append(f"  - field_id={u.field_id}, value=\"{u.value}\", condition={u.condition}")

    if ss.subagent:
        parts.append(f"\nSubagent call: type={ss.subagent.type}")
        parts.append(f"  Inputs: {ss.subagent.inputs}")
        parts.append(f"  Expected: {ss.subagent.expected}")

    return "\n".join(parts)


def _build_prompt(
    step: StepDef,
    substep_id: Optional[str],
    description: str,
    registry: Optional[FieldRegistry] = None,
) -> str:
    """Build the user prompt for Claude.

    Args:
        step: The step definition to generate code for.
        substep_id: If set, generate for a single substep. Otherwise all.
        description: English description of the business logic.
        registry: If provided, includes the full field inventory so Claude
                  knows every field available at runtime.
    """
    parts = [f"# Generate tool code for {step.id}: {step.name}"]
    parts.append(f"Phase: {step.phase}")
    parts.append(f"Step description: {step.description}")
    parts.append("")

    # ── Field inventory (if registry available) ──
    if registry:
        parts.append(_build_field_inventory(registry, step.id))
        parts.append("")

    if description:
        parts.append("## English Description of Business Logic")
        parts.append(description)
        parts.append("")

    if substep_id:
        # Generate for a specific substep
        ss = next((s for s in step.substeps if s.id == substep_id), None)
        if ss:
            parts.append(_build_substep_context(ss))
        else:
            parts.append(f"ERROR: Substep {substep_id} not found in step {step.id}")
    else:
        # Generate for all substeps in this step
        parts.append("## All Substeps")
        for ss in step.substeps:
            parts.append("")
            parts.append(_build_substep_context(ss))

    # Resolve substep info for the template
    target_ss = None
    if substep_id:
        target_ss = next((s for s in step.substeps if s.id == substep_id), None)

    parts.append("")
    parts.append("## Code Template (use these helpers)")
    template = (TOOL_TEMPLATE
                .replace("{{step_name}}", step.name)
                .replace("{{step_id}}", step.id)
                .replace("{{step_number}}", str(step.step_number))
                .replace("{{phase}}", step.phase)
                .replace("{{tool_name}}", target_ss.tool if target_ss else "tool_name")
                .replace("{{substep_id}}", target_ss.id if target_ss else "X.X")
                .replace("{{substep_name}}", target_ss.name if target_ss else "Substep Name"))
    parts.append(template)

    parts.append("")
    parts.append("## REMINDER: Write excellent tool descriptions!")
    parts.append("The @tool function docstring MUST clearly describe:")
    parts.append("1. What the tool does (specific actions, not vague)")
    parts.append("2. When the model should call it")
    parts.append("3. What fields it reads (LOS keys + doc types)")
    parts.append("4. What flags it can raise (name + severity)")
    parts.append("5. What it writes (field_corrections)")
    parts.append("The agent model will use this description to decide whether to call this tool.")

    return "\n".join(parts)


async def generate_tool_code(
    step: StepDef,
    substep_id: Optional[str] = None,
    description: str = "",
    registry: Optional[FieldRegistry] = None,
) -> dict:
    """Generate tool code using Claude.

    Args:
        step: The step definition
        substep_id: If set, generate for just this substep. Otherwise, full step.
        description: English description of the business logic
        registry: Field registry — if provided, Claude gets a complete inventory
                  of every LOS field and doc field available at runtime.

    Returns:
        dict with keys: success, code, model, usage, error
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return {"success": False, "code": "", "error": "ANTHROPIC_API_KEY not set"}

    prompt = _build_prompt(step, substep_id, description, registry=registry)

    try:
        client = anthropic.AsyncAnthropic(api_key=api_key)
        response = await client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )

        code = response.content[0].text

        # Strip markdown fences if Claude included them anyway
        if code.startswith("```python"):
            code = code[len("```python"):].strip()
        if code.startswith("```"):
            code = code[3:].strip()
        if code.endswith("```"):
            code = code[:-3].strip()

        usage = {
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
        }

        logger.info(
            f"[LLM_TOOL_MAKER] Generated {len(code)} chars for {step.id}"
            f" (substep={substep_id or 'all'}, tokens={usage})"
        )

        return {
            "success": True,
            "code": code,
            "model": MODEL,
            "usage": usage,
            "error": None,
        }

    except Exception as e:
        logger.error(f"[LLM_TOOL_MAKER] Error: {e}")
        return {"success": False, "code": "", "error": str(e)}


def generate_tool_code_sync(
    step: StepDef,
    substep_id: Optional[str] = None,
    description: str = "",
    registry: Optional[FieldRegistry] = None,
) -> dict:
    """Synchronous wrapper for generate_tool_code."""
    import asyncio
    return asyncio.run(generate_tool_code(step, substep_id, description, registry=registry))
