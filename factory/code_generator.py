"""Code Generator — Render Jinja2 templates into Python tool and plan files.

This is the heart of the factory: takes step definitions and produces
runnable tool code, plan markdown, AND the workflow_config.json that drives
the runtime's dynamic tool/plan resolution (registry.py).

Tool files are generated PER SUBSTEP (one file per tool function).
"""

from __future__ import annotations

import os
import logging

from jinja2 import Environment, FileSystemLoader

from .schema import AgentConfig, StepDef, SubstepDef
from .field_registry import FieldRegistry
from .step0_generator import generate_step0_definition, generate_step0_tool_code, generate_step0_plan

logger = logging.getLogger(__name__)

TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")


def _get_jinja_env() -> Environment:
    return Environment(
        loader=FileSystemLoader(TEMPLATES_DIR),
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )


# ── Substep-based tool generation ─────────────────────────────────────


def _substep_tool_filename(substep: SubstepDef) -> str:
    """Generate the tool filename for a substep (named after the tool function)."""
    return f"{substep.tool}.py"


def generate_substep_tool_file(step: StepDef, substep: SubstepDef, output_dir: str) -> str:
    """Generate a tool Python file for a single substep.

    Each substep gets its own file named after its tool function,
    e.g., validate_usps_address.py, run_preflight_checks.py.

    Args:
        step: Parent step definition (for context in the template)
        substep: Substep definition
        output_dir: Directory to write to (output/tools/)

    Returns:
        Path to generated file
    """
    env = _get_jinja_env()
    template = env.get_template("substep_tool.py.j2")
    content = template.render(step=step, substep=substep)

    filename = _substep_tool_filename(substep)
    filepath = os.path.join(output_dir, filename)
    os.makedirs(output_dir, exist_ok=True)

    if os.path.exists(filepath):
        logger.info(f"[CODEGEN] Skipping {filename} (already exists — delete to regenerate)")
        return filepath

    with open(filepath, "w") as f:
        f.write(content)

    logger.info(f"[CODEGEN] Generated substep tool: {filepath}")
    return filepath


def generate_tool_helpers(output_dir: str) -> str:
    """Generate the shared _helpers.py file for tool state access utilities.

    Always regenerated — this is a derived file that must stay in sync
    with the template.

    Args:
        output_dir: Directory to write to (output/tools/)

    Returns:
        Path to generated file
    """
    env = _get_jinja_env()
    template = env.get_template("tool_helpers.py.j2")
    content = template.render()

    filepath = os.path.join(output_dir, "_helpers.py")
    os.makedirs(output_dir, exist_ok=True)

    with open(filepath, "w") as f:
        f.write(content)

    logger.info(f"[CODEGEN] Generated tool helpers: {filepath}")
    return filepath


# ── Legacy step-based tool generation (deprecated) ────────────────────


def _tool_filename(step: StepDef) -> str:
    """Generate the tool filename for a step (DEPRECATED — use _substep_tool_filename)."""
    name = step.name.lower().replace(" ", "_").replace("/", "_").replace("-", "_")
    return f"{name}.py"


def generate_tool_file(step: StepDef, output_dir: str) -> str:
    """Generate a tool Python file for a step (DEPRECATED).

    This is kept for backward compatibility. New code should use
    generate_substep_tool_file() instead.
    """
    env = _get_jinja_env()
    template = env.get_template("tool.py.j2")
    content = template.render(step=step)

    filename = _tool_filename(step)
    filepath = os.path.join(output_dir, filename)
    os.makedirs(output_dir, exist_ok=True)

    if os.path.exists(filepath):
        logger.info(f"[CODEGEN] Skipping {filename} (already exists — delete to regenerate)")
        return filepath

    with open(filepath, "w") as f:
        f.write(content)

    logger.info(f"[CODEGEN] Generated tool: {filepath}")
    return filepath


# ── Plan generation (still step-level) ────────────────────────────────


def generate_plan_file(step: StepDef, output_dir: str, next_step: StepDef | None = None) -> str:
    """Generate a plan markdown file for a step.

    Args:
        step: Step definition
        output_dir: Directory to write to (output/plans/)
        next_step: The next step in the workflow (for transition instructions)

    Returns:
        Path to generated file
    """
    env = _get_jinja_env()
    template = env.get_template("plan.md.j2")
    content = template.render(step=step, next_step=next_step)

    name = step.name.lower().replace(" ", "_").replace("/", "_").replace("-", "_")
    filename = f"{name}.md"
    filepath = os.path.join(output_dir, filename)
    os.makedirs(output_dir, exist_ok=True)

    if os.path.exists(filepath):
        logger.info(f"[CODEGEN] Skipping {filename} (already exists — delete to regenerate)")
        return filepath

    with open(filepath, "w") as f:
        f.write(content)

    logger.info(f"[CODEGEN] Generated plan: {filepath}")
    return filepath


# ── Step 0 generation ─────────────────────────────────────────────────


def generate_step0_files(
    registry: FieldRegistry,
    output_dir: str,
    first_step: StepDef | None = None,
) -> tuple[str, str]:
    """Generate Step 0 tool and plan files.

    Args:
        registry: Field registry
        output_dir: Base output directory
        first_step: The first user-defined step (for transition instructions)

    Returns:
        Tuple of (tool_path, plan_path)
    """
    # Tool code
    project_root = os.path.dirname(output_dir)
    tool_code = generate_step0_tool_code(registry, project_root=project_root)
    tool_path = os.path.join(output_dir, "tools", "data_gathering.py")
    os.makedirs(os.path.dirname(tool_path), exist_ok=True)

    if os.path.exists(tool_path):
        logger.info(f"[CODEGEN] Skipping data_gathering.py (already exists — delete to regenerate)")
    else:
        with open(tool_path, "w") as f:
            f.write(tool_code)
        logger.info(f"[CODEGEN] Generated Step 0 tool: {tool_path}")

    # Plan — generate if missing
    next_id = first_step.id if first_step else "STEP_01"
    next_name = first_step.name if first_step else "Verification"
    plan_content = generate_step0_plan(registry, next_step_id=next_id, next_step_name=next_name)
    plan_path = os.path.join(output_dir, "plans", "data_gathering.md")
    os.makedirs(os.path.dirname(plan_path), exist_ok=True)

    if os.path.exists(plan_path):
        logger.info(f"[CODEGEN] Skipping data_gathering.md (already exists — delete to regenerate)")
    else:
        with open(plan_path, "w") as f:
            f.write(plan_content)
        logger.info(f"[CODEGEN] Generated Step 0 plan: {plan_path}")

    return tool_path, plan_path


# ── General tools ─────────────────────────────────────────────────────


def generate_general_tools(output_dir: str) -> str:
    """Generate the general tools file (write_todo, etc.)."""
    env = _get_jinja_env()
    template = env.get_template("general_tools.py.j2")
    content = template.render()

    filepath = os.path.join(output_dir, "tools", "general.py")
    os.makedirs(os.path.dirname(filepath), exist_ok=True)

    if os.path.exists(filepath):
        logger.info(f"[CODEGEN] Skipping general.py (already exists — delete to regenerate)")
        return filepath

    with open(filepath, "w") as f:
        f.write(content)

    logger.info(f"[CODEGEN] Generated general tools: {filepath}")
    return filepath


# ── Init file ─────────────────────────────────────────────────────────


def generate_init_tools(steps: list[StepDef], output_dir: str, force: bool = False) -> str:
    """Generate tools/__init__.py with all imports (substep-based).

    Always regenerated — this is a derived file that must stay in sync
    with the current step definitions. Stale imports from deleted steps
    will cause runtime ImportErrors.

    Args:
        steps: All step definitions
        output_dir: Base output directory
        force: Ignored (kept for backward compat). Always overwrites.
    """
    env = _get_jinja_env()
    template = env.get_template("init_tools.py.j2")

    substep_entries = []
    for step in steps:
        for substep in step.substeps:
            substep_entries.append({
                "step_id": step.id,
                "substep_id": substep.id,
                "substep_name": substep.name,
                "tool_name": substep.tool,
            })

    content = template.render(substep_entries=substep_entries)

    filepath = os.path.join(output_dir, "tools", "__init__.py")
    os.makedirs(os.path.dirname(filepath), exist_ok=True)

    with open(filepath, "w") as f:
        f.write(content)

    logger.info(f"[CODEGEN] Generated tools/__init__.py: {filepath}")
    return filepath


# ── System prompt ─────────────────────────────────────────────────────


def _build_workflow_overview(all_steps: list[StepDef]) -> str:
    """Build a compact workflow overview listing only step names.

    Substep details are in the individual plan files loaded at each step.
    """
    lines = []
    current_phase = None

    for step in all_steps:
        if step.phase != current_phase:
            current_phase = step.phase
            lines.append(f"\n### Phase: {current_phase}")

        step_num = step.step_number
        ss_count = len(step.substeps)
        lines.append(f"- **Step {step_num}** — {step.name} ({ss_count} substeps)")

    return "\n".join(lines)


def generate_system_prompt(
    agent_config: AgentConfig,
    registry: FieldRegistry,
    output_dir: str,
    all_steps: list[StepDef] | None = None,
    force: bool = False,
) -> str:
    """Generate the system prompt with full workflow overview baked in.

    Always regenerated — the system prompt is derived from agent config
    and step definitions, so it must reflect the current state.

    Args:
        agent_config: Agent configuration
        registry: Field registry
        output_dir: Output directory
        all_steps: All steps (including Step 0) — used to render the
            workflow overview at generation time. If None, falls back
            to a runtime placeholder.
        force: Ignored (kept for backward compat). Always overwrites.
    """
    env = _get_jinja_env()
    template = env.get_template("system_prompt.md.j2")

    if all_steps:
        overview = _build_workflow_overview(all_steps)
    else:
        overview = "{{WORKFLOW_OVERVIEW}}"

    content = template.render(
        agent=agent_config,
        workflow_overview=overview,
    )

    filepath = os.path.join(output_dir, "plans", "system_prompt.template.md")
    os.makedirs(os.path.dirname(filepath), exist_ok=True)

    with open(filepath, "w") as f:
        f.write(content)

    logger.info(f"[CODEGEN] Generated system prompt: {filepath}")
    return filepath
