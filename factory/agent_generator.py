"""Agent Generator — Generate the agent entry point, registry, and step_loader.

Orchestrates all generation: calls code_generator, config_generator,
and renders the agent.py, registry.py, step_loader.py templates.
"""

from __future__ import annotations

import glob
import logging
import os
import re
import shutil

from .schema import AgentConfig, StepDef, load_all_definitions
from .field_registry import FieldRegistry, build_field_registry
from .step0_generator import generate_step0_definition
from .validator import validate_definitions
from .code_generator import (
    _get_jinja_env,
    generate_substep_tool_file,
    generate_tool_helpers,
    generate_plan_file,
    generate_step0_files,
    generate_general_tools,
    generate_init_tools,
    generate_system_prompt,
)
from .config_generator import (
    generate_workflow_config,
    generate_fields_config,
)

logger = logging.getLogger(__name__)


def generate_agent_entry(agent_config: AgentConfig, output_dir: str) -> str:
    """Generate the main agent entry point (docs_orch_agent.py)."""
    env = _get_jinja_env()
    template = env.get_template("agent.py.j2")
    content = template.render(agent=agent_config)

    filepath = os.path.join(output_dir, f"{agent_config.name.lower()}_agent.py")

    if os.path.exists(filepath):
        logger.info(f"[AGENT] Skipping {os.path.basename(filepath)} (already exists — delete to regenerate)")
        return filepath

    with open(filepath, "w") as f:
        f.write(content)

    logger.info(f"[AGENT] Generated agent entry: {filepath}")
    return filepath


def generate_registry(output_dir: str) -> str:
    """Generate registry.py from template."""
    env = _get_jinja_env()
    template = env.get_template("registry.py.j2")
    content = template.render()

    filepath = os.path.join(output_dir, "registry.py")

    if os.path.exists(filepath):
        logger.info(f"[AGENT] Skipping registry.py (already exists — delete to regenerate)")
        return filepath

    with open(filepath, "w") as f:
        f.write(content)

    logger.info(f"[AGENT] Generated registry: {filepath}")
    return filepath


def generate_step_loader(output_dir: str) -> str:
    """Generate step_loader.py from template."""
    env = _get_jinja_env()
    template = env.get_template("step_loader.py.j2")
    content = template.render()

    filepath = os.path.join(output_dir, "step_loader.py")

    if os.path.exists(filepath):
        logger.info(f"[AGENT] Skipping step_loader.py (already exists — delete to regenerate)")
        return filepath

    with open(filepath, "w") as f:
        f.write(content)

    logger.info(f"[AGENT] Generated step_loader: {filepath}")
    return filepath


def update_agent(
    definitions_dir: str,
    output_dir: str,
) -> dict:
    """Incremental update: regenerate only derived config/data files.

    Safe to call after any UI change (field/doc/condition/step edits).
    Only overwrites files that are always safe to regenerate:
      - workflow_config.json   (computed from YAML definitions)
      - fields_config.json     (computed from field registry)
      - system_prompt.template.md  (has workflow overview baked in)
      - tools/__init__.py      (import list derived from step definitions)

    Never touches tool code, plan markdown, agent entry, or other scaffolded files.
    Never cleans up stale files.

    Args:
        definitions_dir: Path to definitions/ directory with YAML files
        output_dir: Path to output/ directory for generated code

    Returns:
        Dict with update results
    """
    results = {
        "success": False,
        "files_updated": [],
        "validation": None,
        "errors": [],
    }

    try:
        # ── Load definitions ──
        logger.info(f"[UPDATE] Loading definitions from {definitions_dir}")
        agent_config, steps = load_all_definitions(definitions_dir)
        logger.info(f"[UPDATE] Loaded: {agent_config.name} with {len(steps)} steps")

        # ── Build field registry ──
        logger.info(f"[UPDATE] Building field registry")
        registry = build_field_registry(definitions_dir)
        logger.info(f"[UPDATE] {registry.summary()}")

        # ── Validate ──
        logger.info(f"[UPDATE] Validating definitions")
        report = validate_definitions(agent_config, steps, registry)
        results["validation"] = report

        if not report.is_valid:
            logger.warning(f"[UPDATE] Validation issues:\n{report.summary()}")

        # ── Ensure output directories exist ──
        for subdir in ["config", "plans", "tools"]:
            os.makedirs(os.path.join(output_dir, subdir), exist_ok=True)

        # ── Regenerate configs (always overwrite — these are data files) ──
        logger.info("[UPDATE] Regenerating workflow_config.json")
        wf_path = generate_workflow_config(agent_config, steps, registry, output_dir)
        results["files_updated"].append(wf_path)

        logger.info("[UPDATE] Regenerating fields_config.json")
        fc_path = generate_fields_config(registry, output_dir)
        results["files_updated"].append(fc_path)

        # ── Regenerate system prompt (force — has workflow overview) ──
        logger.info("[UPDATE] Regenerating system prompt")
        step0_def = generate_step0_definition(registry)
        all_steps_with_step0 = [step0_def] + steps
        sp_path = generate_system_prompt(
            agent_config, registry, output_dir,
            all_steps=all_steps_with_step0, force=True,
        )
        results["files_updated"].append(sp_path)

        # ── Regenerate tools/__init__.py (force — import list) ──
        logger.info("[UPDATE] Regenerating tools/__init__.py")
        init_path = generate_init_tools(steps, output_dir, force=True)
        results["files_updated"].append(init_path)

        results["success"] = True
        logger.info(f"[UPDATE] Complete! {len(results['files_updated'])} files updated.")

    except Exception as e:
        logger.error(f"[UPDATE] Error: {e}", exc_info=True)
        results["errors"].append(str(e))

    return results


def renumber_steps(definitions_dir: str, output_dir: str) -> dict:
    """Renumber all steps sequentially (1, 2, 3, ...) closing any gaps.

    Step 0 is auto-generated and never renumbered. Only user-defined steps
    (step_*.yaml) are processed.

    For each step that needs a new number:
      - Updates the step `id` field (STEP_07 -> STEP_06)
      - Updates all substep `id` fields (7.1 -> 6.1)
      - Renames the YAML file
      - Removes old plan files from output/plans/

    After renumbering, call update_agent() to regenerate derived files.

    Returns:
        Dict with renaming results
    """
    results = {"renamed": [], "plans_removed": [], "errors": []}

    yaml_files = sorted(glob.glob(os.path.join(definitions_dir, "step_*.yaml")))
    if not yaml_files:
        logger.info("[RENUMBER] No step files found")
        return results

    entries = []
    for fpath in yaml_files:
        basename = os.path.basename(fpath)
        match = re.match(r"step_(\d+)_(.+)\.yaml", basename)
        if not match:
            continue
        old_num = int(match.group(1))
        name_slug = match.group(2)
        entries.append({"path": fpath, "old_num": old_num, "slug": name_slug})

    entries.sort(key=lambda e: e["old_num"])

    new_num = 1
    for entry in entries:
        old_num = entry["old_num"]
        if old_num == new_num:
            new_num += 1
            continue

        old_path = entry["path"]
        slug = entry["slug"]
        new_path = os.path.join(definitions_dir, f"step_{new_num:02d}_{slug}.yaml")

        with open(old_path, "r") as f:
            content = f.read()

        old_id = f"STEP_{old_num:02d}"
        new_id = f"STEP_{new_num:02d}"
        content = content.replace(f"id: {old_id}", f"id: {new_id}", 1)

        content = re.sub(
            rf"id: '({old_num})\.(\d+)'",
            lambda m: f"id: '{new_num}.{m.group(2)}'",
            content,
        )

        with open(old_path, "w") as f:
            f.write(content)

        os.rename(old_path, new_path)

        logger.info(f"[RENUMBER] {old_id} -> {new_id}: {os.path.basename(old_path)} -> {os.path.basename(new_path)}")
        results["renamed"].append({
            "old_id": old_id, "new_id": new_id,
            "old_file": os.path.basename(old_path),
            "new_file": os.path.basename(new_path),
        })

        new_num += 1

    plans_dir = os.path.join(output_dir, "plans")
    if os.path.isdir(plans_dir):
        for fname in os.listdir(plans_dir):
            if fname.startswith("step_") and fname.endswith(".md"):
                fpath = os.path.join(plans_dir, fname)
                os.remove(fpath)
                results["plans_removed"].append(fname)
                logger.info(f"[RENUMBER] Removed old plan: {fname}")

    if results["renamed"]:
        logger.info(f"[RENUMBER] Renumbered {len(results['renamed'])} steps, removed {len(results['plans_removed'])} old plan files")
    else:
        logger.info("[RENUMBER] No gaps found — steps are already sequential")

    return results


def generate_all(
    definitions_dir: str,
    output_dir: str,
    shared_dir: str | None = None,
    force: bool = False,
) -> dict:
    """Orchestrate full code generation from definitions to runnable agent.

    Args:
        definitions_dir: Path to definitions/ directory with YAML files
        output_dir: Path to output/ directory for generated code
        shared_dir: Path to shared/ utilities (optional, for symlinking)
        force: Force overwrite of existing tool files

    Returns:
        Dict with generation results and validation report
    """
    results = {
        "success": False,
        "files_generated": [],
        "validation": None,
        "errors": [],
    }

    try:
        # ── Load definitions ──
        logger.info(f"[GENERATE] Loading definitions from {definitions_dir}")
        agent_config, steps = load_all_definitions(definitions_dir)
        logger.info(f"[GENERATE] Loaded: {agent_config.name} with {len(steps)} steps")

        # ── Build field registry ──
        logger.info(f"[GENERATE] Building field registry")
        registry = build_field_registry(definitions_dir)
        logger.info(f"[GENERATE] {registry.summary()}")

        # ── Validate ──
        logger.info(f"[GENERATE] Validating definitions")
        report = validate_definitions(agent_config, steps, registry)
        results["validation"] = report

        if not report.is_valid:
            logger.error(f"[GENERATE] Validation FAILED:\n{report.summary()}")
            results["errors"].append("Validation failed. Fix errors before generating.")
            # Continue anyway — errors might be non-critical during development
            logger.warning("[GENERATE] Continuing despite validation errors (dev mode)")

        # ── Create output directories ──
        for subdir in ["config", "plans", "tools"]:
            os.makedirs(os.path.join(output_dir, subdir), exist_ok=True)

        # ── Generate Step 0 (auto-generated from field registry) ──
        logger.info("[GENERATE] Generating Step 0 (data gathering)")
        first_step = steps[0] if steps else None
        tool_path, plan_path = generate_step0_files(registry, output_dir, first_step=first_step)
        results["files_generated"].extend([tool_path, plan_path])

        # ── Generate tool helpers (_helpers.py) ──
        logger.info("[GENERATE] Generating tool helpers (_helpers.py)")
        helpers_path = generate_tool_helpers(os.path.join(output_dir, "tools"))
        results["files_generated"].append(helpers_path)

        # ── Generate step tools (one file per substep) and plans ──
        for idx, step in enumerate(steps):
            logger.info(f"[GENERATE] Generating {step.id}: {step.name} ({len(step.substeps)} substep tools)")

            for substep in step.substeps:
                tool_path = generate_substep_tool_file(step, substep, os.path.join(output_dir, "tools"))
                results["files_generated"].append(tool_path)

            # Pass next step for transition instructions in the plan
            next_step = steps[idx + 1] if idx + 1 < len(steps) else None
            plan_path = generate_plan_file(step, os.path.join(output_dir, "plans"), next_step=next_step)
            results["files_generated"].append(plan_path)

        # ── Generate general tools ──
        logger.info("[GENERATE] Generating general tools (write_todo, etc.)")
        gen_path = generate_general_tools(output_dir)
        results["files_generated"].append(gen_path)

        # ── Generate tools/__init__.py ──
        logger.info("[GENERATE] Generating tools/__init__.py")
        init_path = generate_init_tools(steps, output_dir)
        results["files_generated"].append(init_path)

        # ── Generate configs ──
        logger.info("[GENERATE] Generating workflow_config.json")
        wf_path = generate_workflow_config(agent_config, steps, registry, output_dir)
        results["files_generated"].append(wf_path)

        logger.info("[GENERATE] Generating fields_config.json")
        fc_path = generate_fields_config(registry, output_dir)
        results["files_generated"].append(fc_path)

        # ── Generate system prompt (with full workflow overview baked in) ──
        logger.info("[GENERATE] Generating system prompt")
        step0_def = generate_step0_definition(registry)
        all_steps_with_step0 = [step0_def] + steps
        sp_path = generate_system_prompt(agent_config, registry, output_dir, all_steps=all_steps_with_step0)
        results["files_generated"].append(sp_path)

        # ── Generate agent entry point ──
        # DISABLED: the live LangGraph entry point is the hand-maintained
        # output/proc_agent.py (see langgraph.json). The generated
        # "<name>_agent.py" scaffold was unused, so we no longer emit it.
        # logger.info("[GENERATE] Generating agent entry point")
        # agent_path = generate_agent_entry(agent_config, output_dir)
        # results["files_generated"].append(agent_path)

        # ── Generate registry.py ──
        logger.info("[GENERATE] Generating registry.py")
        reg_path = generate_registry(output_dir)
        results["files_generated"].append(reg_path)

        # ── Generate step_loader.py ──
        logger.info("[GENERATE] Generating step_loader.py")
        sl_path = generate_step_loader(output_dir)
        results["files_generated"].append(sl_path)

        # ── Clean up stale files ──
        # Remove tool/plan files that are no longer in the definitions.
        # This ensures removed steps/substeps don't leave orphaned files.
        generated_set = {os.path.abspath(f) for f in results["files_generated"]}

        # Protected files that should never be cleaned up
        protected_basenames = {"__init__.py", "_helpers.py", "general.py", "__pycache__"}

        def _is_factory_locked(path: str) -> bool:
            """True if the file opts out of factory management via FACTORY-LOCK: true.

            Hand-wired tools that are not derived from a YAML substep (e.g.
            build_action_items, STEP_11.3) declare ``# FACTORY-LOCK: true`` and
            must never be overwritten OR deleted by factory-reset. The lock
            contract is documented in .cursor/rules/factory-lock-protection.mdc.
            """
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    head = fh.read(2048)
            except (OSError, UnicodeDecodeError):
                return False
            return "FACTORY-LOCK: true" in head

        for scan_dir in ["tools", "plans"]:
            scan_path = os.path.join(output_dir, scan_dir)
            if not os.path.isdir(scan_path):
                continue
            for fname in os.listdir(scan_path):
                fpath = os.path.abspath(os.path.join(scan_path, fname))
                if fname in protected_basenames:
                    continue
                if fname.startswith("__"):
                    continue
                if os.path.isdir(fpath):
                    continue
                if fpath not in generated_set:
                    if _is_factory_locked(fpath):
                        logger.info(
                            f"[CLEANUP] Keeping FACTORY-LOCKed stale file: "
                            f"{os.path.relpath(fpath, output_dir)}"
                        )
                        continue
                    logger.warning(f"[CLEANUP] Removing stale file: {os.path.relpath(fpath, output_dir)}")
                    os.remove(fpath)
                    results.setdefault("files_removed", []).append(fpath)

        # ── Link shared utilities ──
        if shared_dir:
            shared_dest = os.path.join(output_dir, "shared")
            if os.path.exists(shared_dest):
                if os.path.islink(shared_dest):
                    os.unlink(shared_dest)
                else:
                    shutil.rmtree(shared_dest)
            os.symlink(os.path.abspath(shared_dir), shared_dest)
            logger.info(f"[GENERATE] Linked shared/ -> {shared_dir}")

        results["success"] = True
        logger.info(f"[GENERATE] Complete! {len(results['files_generated'])} files generated.")

    except Exception as e:
        logger.error(f"[GENERATE] Error: {e}", exc_info=True)
        results["errors"].append(str(e))

    return results
