"""LLM Plan Maker — Uses Claude to generate step plans (agent prompts).

A "plan" is a Markdown file that gets injected into the agent's context at
runtime when the agent reaches that step. It is literally a PROMPT that tells
the LLM which tools to call, in what order, what to look for in the results,
and when to raise flags. The agent model reads this plan and follows it.

Usage:
    from factory.llm_plan_maker import generate_plan
    plan = await generate_plan(step_def, description="...")
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Optional

import anthropic

from .schema import StepDef

if TYPE_CHECKING:
    from .field_registry import FieldRegistry

logger = logging.getLogger(__name__)

MODEL = "claude-sonnet-4-20250514"
MAX_TOKENS = 8192

SYSTEM_PROMPT = """You are writing a PLAN — a Markdown prompt that will be injected into an AI agent's context at runtime.

## What Is a Plan?

A plan is NOT documentation. It is an INSTRUCTION SET for an AI agent (Claude) that is
executing a mortgage loan review workflow. When the agent reaches this step, the plan
is loaded into its context as a system-level instruction. The agent reads it and follows
it to know exactly:

1. **Which tools to call** — each substep has a specific `@tool` Python function
2. **In what order** — substeps must be executed sequentially
3. **What to look for** in each tool's results — specific return fields, values, conditions
4. **When to raise flags** via the tool's flagging mechanism — with exact severity and remedy
5. **How to track progress** using `write_todo` after each substep
6. **What state looks like** after each tool runs — concrete Python dict examples
7. **Exception/skip conditions** — when to skip substeps based on loan type, state, or field values

## MANDATORY Document Structure

Every plan MUST follow this exact section structure (in this order):

```
## Purpose
1-3 sentence description of what this step does and WHY.
**NOTE**: Each substep has its own dedicated tool. State (`los_fields`, `doc_fields`) is automatically injected.
Clearly state if this step is READ-ONLY or may WRITE to Encompass.

## Available Tools
| Tool | Purpose |
|------|---------|
| `tool_name` | Concise purpose |

## Overview
| Substep | Description | Tool |
|---------|-------------|------|
| X.1 | What it does | `tool_name` |
| X.2 | What it does | `other_tool` |

## Tool Calls
```python
# Substep X.1 - Name
# ⚠️ Notes about side effects, writes, or skip conditions
tool_name(loan_guid=loan_id)

# Substep X.2 - Name
other_tool(loan_guid=loan_id)
# Returns: field1, field2, field3
```

---

## Substeps

### Substep X.1 - Name
**Tool**: `tool_name`

[DEEP content — see requirements below]

---

### Substep X.2 - Name
**Tool**: `other_tool`

[DEEP content]

---

## Response Fields / State Updates After Step X

[Show what state looks like after this step completes]

---

## Step Completion

When ALL substeps above are completed:
1. Call `save_step_report(step_name="STEP_XX", status="completed", ...)`
2. Call `write_todo(step_id="STEP_XX", status="completed")`
3. Call `write_todo(step_id="STEP_YY", status="in_progress")` to start the next step

⚠️ You MUST call write_todo to advance — do NOT produce a text-only response.
```

## What Makes a GOOD Substep (Critical — Read Carefully)

Each substep MUST be DEEP and ACTIONABLE. A thin substep that just lists field names is USELESS.
The agent needs to know HOW to verify, WHAT to compare, WHAT values mean, and WHEN to flag.

For EACH substep, include ALL of these that are relevant:

### 1. Overview paragraph
What this substep does, why it matters, and any domain context.

### 2. Key Checks (the most important part)
Bullet list or bold headers for each verification the tool performs.
Be SPECIFIC about conditions, thresholds, and expected values:
- BAD: "Check CTC status"
- GOOD: "**CTC Must Be Clear**: CTC status field must indicate 'Clear to Close'. If not → CRITICAL flag, remedy: Escalate — cannot proceed without CTC"

### 3. Field ID Tables
When 3+ fields are involved, use markdown tables with ALL columns:
| Encompass Field | Field ID | Source | Notes |
|-----------------|----------|--------|-------|
| Current APR | `799` | Loan entity | Calculated APR |
| Disclosed APR | `3121` | Last CD | APR from Closing Disclosure |

### 4. Detailed Business Logic
Include the ACTUAL rules — formulas, thresholds, lookup tables, date math:
- "APR variance > 0.125% → 3-day wait REQUIRED, new CD needed"
- "1st Payment Date = Closing Date + 1 month (landed on the 1st)"
- "MIP refund uses FUNDING DATE (not Note Date) to determine month"

### 5. Exception / Skip Conditions
When should this substep be skipped or behave differently?
- "Skipped if: PIW, FHA Streamline, VA IRRRL, or waiver marked"
- "VA loans only — skip for Conventional/FHA/USDA"
- "If co-borrower does NOT exist: log 'Co-borrower does not exist' and skip coborrower checks"

### 6. State Structure Examples
Show concrete Python dicts of what state looks like AFTER the tool runs:
```python
state["usps_validated_address"] = {
    "street": "123 MAIN ST",
    "city": "LAS VEGAS",
    "state": "NV",
    "zip": "89101",
    "dpv_confirmed": True
}
```

### 7. Tool Return Values
Document what the tool returns so the agent knows what to check:
- `passed`: Boolean if all critical checks passed
- `checks`: List of check results [{name, passed, value, reason, remedy}]
- `critical_issues`: List of critical issues requiring resolution

### 8. Flag Examples
Show the exact flag structure with all fields:
```json
{
  "substep": "1.2",
  "title": "CTC Not Clear",
  "details": "CTC status is 'Pending' — expected 'Clear to Close'",
  "suggestion": "Escalate — cannot proceed without CTC",
  "severity": "critical"
}
```

### 9. Field Updates (writes to Encompass)
If the tool writes to Encompass, clearly mark with ⚠️:
**⚠️ WRITES TO ENCOMPASS:**
- Field `1039` (Section of Act): Auto-corrects to "203B" for FHA loans
- Field `748` (Note Date): If `additional_info.note_date` is provided

### 10. Auto-enforcement / Auto-correction Rules
If the tool auto-corrects values, explain the logic:
- "If company name does NOT start with 'R3': auto-overwrite Paid to Type = 'Other'"
- "If negative value: auto-correct to positive and flag"

### 11. Cross-step Context
Reference data from previous/next steps when relevant:
- "Uses `state['usps_validated_address']` from Step 1.2"
- "Stores `existing_ptf_conditions` in state for use by Steps 5 and 15"

### 12. write_todo Call
Always end the substep with:
```
After completing this substep, call:
write_todo(step_id="STEP_XX", substep_id="X.Y", status="completed")
```

## Response Format Section

After all substeps, include a "Response Fields" or "State Updates" section showing
what the step's output looks like as a whole:
```json
{
  "step": 1,
  "title": "Verification",
  "status": "success",
  "substeps": {
    "1.1_preflight_checks": {"passed": true, "checks_total": 14},
    "1.2_usps_validation": {"is_valid": true, "dpv_confirmed": true}
  },
  "ready_for_step2": true
}
```

## Formatting Rules

1. **Substeps use `###` (h3)** — `##` is for top-level sections (Purpose, Substeps, etc.)
2. **Each substep starts with `**Tool**: tool_name`** (singular) on the line after the heading
3. **Be directive** — "Call `validate_usps_address`" not "The address tool..."
4. **Include `---` horizontal rules** between substeps and between major sections
5. **Use markdown tables liberally** for field references, check summaries, lookup rules
6. **Include Python code blocks** for state structure examples
7. **Include JSON code blocks** for flag examples and response format
8. **Use ⚠️ emoji** for write warnings and critical notes
9. **Bold key terms** — field names, rule names, severity levels

## Domain Context

This is a mortgage loan processing agent for All Western Mortgage Inc. The workflow:
- Step 0: Data Gathering (read-only) — fetch loan, extract fields, get documents
- Steps 1-N: Verification, review, compliance checks, fee processing, etc.
- Each step may read LOS fields (from Encompass), doc fields (from eFolder documents), and loan_summary (URLA snapshot)
- State access patterns:
  - `state["los_fields"]["field_key"]["value"]` — LOS field values
  - `state["doc_fields"]["field_key"]["value"]` — Document field values
  - `state["loan_summary"]` — Categorized loan snapshot (borrower, property, loan_terms, dates, vesting, derived)
  - `state["flags"]` — List of flag dicts
  - `state["loan_id"]` — Encompass loan GUID
- Issue remedies: Escalate, Override, Condition (PTF), Ignore, Note
- Flag severities: critical (stop), blocking (must resolve), warning (review), info (FYI)

## Tone

Write as if you're briefing a competent colleague who has access to all the tools but needs
to know the exact business rules, field IDs, thresholds, and edge cases.
Be thorough, precise, and actionable. No fluff, no vague instructions.
Include mortgage domain specifics — Encompass field IDs, form numbers, regulatory rules,
calculation formulas, and lookup tables.

## Output

Return ONLY the Markdown content. No code fences around the entire output.
Aim for 200-500 lines per plan depending on step complexity. More substeps = longer plan.
Never be thin or skeletal — the agent relies entirely on this plan for instructions."""


def _build_field_summary(registry: FieldRegistry, step_id: str) -> str:
    """Build a concise field summary for the plan maker.

    The plan needs to reference field keys by name so the agent knows
    which fields to check after calling each tool.
    """
    parts: list[str] = []
    parts.append("## Fields Available at Runtime")
    parts.append("")
    parts.append("Step 0 loads these fields into state. Reference them by key when instructing the agent.")
    parts.append("")

    # LOS fields for this step
    step_los = registry.get_los_fields_for_step(step_id)
    if step_los:
        parts.append("### LOS Fields (this step)")
        parts.append("")
        for fref in step_los:
            purpose = f" — {fref.purpose}" if fref.purpose else ""
            parts.append(f"- `{fref.key}` (field {fref.field_id}){purpose}")
        parts.append("")

    # Doc types and fields for this step
    step_def = next((s for s in registry.steps if s.id == step_id), None)
    step_doc_types: dict[str, list[str]] = {}
    if step_def:
        for ss in step_def.substeps:
            for dt in ss.doc_types:
                if dt.document_type not in step_doc_types:
                    step_doc_types[dt.document_type] = []
                for f in dt.fields:
                    if f.key not in step_doc_types[dt.document_type]:
                        step_doc_types[dt.document_type].append(f.key)

    if step_doc_types:
        parts.append("### Document Types & Fields (this step)")
        parts.append("")
        for dt_name, fields in sorted(step_doc_types.items()):
            dti = registry.doc_type_info.get(dt_name)
            copies = " (ALL COPIES)" if dti and dti.all_copies else ""
            parts.append(f"- **{dt_name}**{copies}: {', '.join(f'`{f}`' for f in fields)}")
        parts.append("")

    return "\n".join(parts)


def _build_prompt(
    step: StepDef,
    description: str = "",
    registry: Optional[FieldRegistry] = None,
) -> str:
    """Build the user prompt for Claude."""
    parts = [
        f"# Generate a DETAILED plan for: {step.id} — {step.name}",
        f"Phase: {step.phase}",
        f"Step Number: {step.step_number}",
        f"Description: {step.description}",
        "",
        "The agent will read this plan as system-level instructions. It must be",
        "thorough enough that the agent can execute the step without any other context.",
        "",
        "## QUALITY BAR",
        "",
        "The plan must be DEEP, not skeletal. Each substep should include:",
        "- Concrete business logic with specific values, thresholds, formulas",
        "- Field ID tables when 3+ fields are involved",
        "- State structure examples (Python dicts) showing what state looks like after tool runs",
        "- Tool return value documentation",
        "- Exception/skip conditions (e.g., 'VA loans only', 'skip if PIW')",
        "- Flag examples with exact JSON structure",
        "- Write warnings (⚠️) if the tool modifies Encompass",
        "- Cross-step references when using data from prior steps",
        "",
        "A single substep should be 20-60 lines. The total plan should be 150-500 lines.",
        "DO NOT generate thin plans that just list field names — the agent needs actionable depth.",
        "",
    ]

    # ── Field inventory (if registry available) ──
    if registry:
        parts.append(_build_field_summary(registry, step.id))
        parts.append("")

    if description:
        parts.append("## Additional Instructions from the Developer")
        parts.append("")
        parts.append(description)
        parts.append("")

    parts.append("## Substeps and Their Full Definitions (from YAML)")
    parts.append("")
    parts.append("Below is EVERY detail from the step definition. Use ALL of this information")
    parts.append("to write detailed substep instructions in the plan.")
    parts.append("")

    # Determine if any substep writes
    any_writes = any(ss.field_updates for ss in step.substeps)
    if any_writes:
        parts.append("⚠️ **This step WRITES to Encompass** — clearly mark which substeps write and which fields.")
        parts.append("")

    for ss in step.substeps:
        parts.append(f"### Substep {ss.id} — {ss.name}")
        parts.append(f"- **Tool:** `{ss.tool}`")
        if ss.description:
            parts.append(f"- **Description:** {ss.description}")

        if ss.los_fields_read:
            parts.append("- **LOS fields read from state:**")
            for f in ss.los_fields_read:
                purpose_str = f" — {f.purpose}" if f.purpose else ""
                parts.append(f"  - `{f.key}` (Encompass field `{f.field_id}`){purpose_str}")

        if ss.doc_types:
            parts.append("- **Document types read:**")
            for dt in ss.doc_types:
                keys = ", ".join(f"`{f.key}`" for f in dt.fields)
                copies = " (ALL COPIES)" if dt.all_copies else ""
                parts.append(f"  - **{dt.document_type}**{copies}: {keys}")

        if ss.rules:
            parts.append("- **Business rules (MUST be included with full detail in the plan):**")
            for r in ss.rules:
                parts.append(f"  - **{r.name}** ({r.type}): {r.logic}")

        if ss.flags:
            parts.append("- **Flags (include severity, condition, and remedy in the plan):**")
            for fl in ss.flags:
                parts.append(f"  - [{fl.severity.upper()}] \"{fl.title}\"")
                parts.append(f"    Condition: {fl.condition}")
                parts.append(f"    Remedy: {fl.suggestion}")

        if ss.field_updates:
            parts.append("- **⚠️ Field updates (writes to Encompass):**")
            for u in ss.field_updates:
                parts.append(f"  - Field `{u.field_id}` = `{u.value}` (when: {u.condition})")

        if ss.subagent:
            parts.append(f"- **Subagent call:** `{ss.subagent.type}` with inputs: {ss.subagent.inputs}")

        parts.append("")

    parts.append("## Step Completion Requirements")
    parts.append("")
    parts.append("The plan MUST end with a Step Completion section:")
    parts.append("")
    parts.append(f'1. Call `save_step_report(step_name="{step.id}", status="completed", ...)`')
    parts.append(f'2. Call `write_todo(step_id="{step.id}", status="completed")`')
    parts.append(f'3. Call `write_todo(step_id="STEP_XX", status="in_progress")` to start the next step')
    parts.append("")
    parts.append("⚠️ You MUST call write_todo to advance — do NOT produce a text-only response.")

    return "\n".join(parts)


async def generate_plan(
    step: StepDef,
    description: str = "",
    registry: Optional[FieldRegistry] = None,
) -> dict:
    """Generate plan markdown using Claude.

    Args:
        step: The step definition
        description: English description / additional context
        registry: Field registry — if provided, Claude gets a list of
                  every field available at runtime for this step.

    Returns:
        dict with keys: success, plan, model, usage, error
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return {"success": False, "plan": "", "error": "ANTHROPIC_API_KEY not set"}

    prompt = _build_prompt(step, description, registry=registry)

    try:
        client = anthropic.AsyncAnthropic(api_key=api_key)
        response = await client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )

        plan = response.content[0].text

        # Strip markdown fences if included
        if plan.startswith("```markdown"):
            plan = plan[len("```markdown"):].strip()
        if plan.startswith("```"):
            plan = plan[3:].strip()
        if plan.endswith("```"):
            plan = plan[:-3].strip()

        usage = {
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
        }

        logger.info(f"[LLM_PLAN_MAKER] Generated {len(plan)} chars for {step.id} (tokens={usage})")

        return {
            "success": True,
            "plan": plan,
            "model": MODEL,
            "usage": usage,
            "error": None,
        }

    except Exception as e:
        logger.error(f"[LLM_PLAN_MAKER] Error: {e}")
        return {"success": False, "plan": "", "error": str(e)}


def generate_plan_sync(
    step: StepDef,
    description: str = "",
    registry: Optional[FieldRegistry] = None,
) -> dict:
    """Synchronous wrapper for generate_plan."""
    import asyncio
    return asyncio.run(generate_plan(step, description, registry=registry))
