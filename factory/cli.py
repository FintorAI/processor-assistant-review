"""CLI for Agent Factory — manage step definitions and code generation.

Usage:
    python -m factory update-agent                        # incremental config sync after UI changes
    python -m factory factory-reset                       # scaffold all (skip existing files)
    python -m factory factory-reset --force               # full reset (overwrite everything)
    python -m factory new-step STEP_03 "MERS/MIN Check" --phase PREPARATION
    python -m factory renumber-steps                      # close gaps in step numbering
    python -m factory validate
    python -m factory status
    python -m factory dashboard
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import textwrap

# Resolve project root (parent of factory/)
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFINITIONS_DIR = os.path.join(PROJECT_ROOT, "definitions")
OUTPUT_DIR = os.path.join(PROJECT_ROOT, "output")
SHARED_DIR = os.path.join(PROJECT_ROOT, "shared")

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


def cmd_new_step(args: argparse.Namespace) -> None:
    """Create a new step definition YAML file."""
    step_id = args.step_id.upper()
    if not step_id.startswith("STEP_"):
        step_id = f"STEP_{step_id}"

    name = args.name
    phase = args.phase.upper()

    # Extract step number
    try:
        step_num = int(step_id.replace("STEP_", ""))
    except ValueError:
        print(f"Error: Invalid step ID: {step_id}")
        return

    # Generate filename
    safe_name = name.lower().replace(" ", "_").replace("/", "_").replace("-", "_")
    filename = f"step_{step_num:02d}_{safe_name}.yaml"
    filepath = os.path.join(DEFINITIONS_DIR, filename)

    if os.path.exists(filepath):
        print(f"Error: File already exists: {filepath}")
        return

    # Generate YAML template
    template = textwrap.dedent(f"""\
    step:
      id: "{step_id}"
      name: "{name}"
      phase: "{phase}"
      description: ""

    substeps:
      - id: "{step_num}.1"
        name: "TODO: First substep"
        tool: "todo_tool_name"
        description: ""
        los_fields_read: []
        doc_types: []
        rules: []
        flags: []
        field_updates: []

    dev:
      skip: false
      fixture:
        los_fields: {{}}
        doc_fields: {{}}
    """)

    os.makedirs(DEFINITIONS_DIR, exist_ok=True)
    with open(filepath, "w") as f:
        f.write(template)

    print(f"Created: {filepath}")
    print(f"  Step: {step_id} — {name}")
    print(f"  Phase: {phase}")
    print(f"  Edit the YAML to add substeps, fields, rules, and flags.")


def cmd_update_agent(args: argparse.Namespace) -> None:
    """Incremental update: sync only config/data files after UI changes."""
    from .agent_generator import update_agent

    if not os.path.exists(DEFINITIONS_DIR):
        print(f"Error: Definitions directory not found: {DEFINITIONS_DIR}")
        return

    print(f"Updating agent from: {DEFINITIONS_DIR}")
    print(f"Output to:           {OUTPUT_DIR}")
    print()

    results = update_agent(
        definitions_dir=DEFINITIONS_DIR,
        output_dir=OUTPUT_DIR,
    )

    if results["validation"]:
        print(results["validation"].summary())
        print()

    if results["success"]:
        print(f"Updated {len(results['files_updated'])} files:")
        for fp in results["files_updated"]:
            rel = os.path.relpath(fp, PROJECT_ROOT)
            print(f"  {rel}")
    else:
        print("Update failed!")
        for err in results["errors"]:
            print(f"  ERROR: {err}")


def cmd_factory_reset(args: argparse.Namespace) -> None:
    """Factory reset: scaffold/regenerate all files from definitions."""
    from .agent_generator import generate_all

    if not os.path.exists(DEFINITIONS_DIR):
        print(f"Error: Definitions directory not found: {DEFINITIONS_DIR}")
        return

    print(f"Factory reset from: {DEFINITIONS_DIR}")
    print(f"Output to:          {OUTPUT_DIR}")
    print()

    results = generate_all(
        definitions_dir=DEFINITIONS_DIR,
        output_dir=OUTPUT_DIR,
        shared_dir=SHARED_DIR,
        force=args.force if hasattr(args, "force") else False,
    )

    if results["validation"]:
        print(results["validation"].summary())
        print()

    if results["success"]:
        print(f"Generated {len(results['files_generated'])} files:")
        for fp in results["files_generated"]:
            rel = os.path.relpath(fp, PROJECT_ROOT)
            print(f"  {rel}")

        removed = results.get("files_removed", [])
        if removed:
            print(f"\nCleaned up {len(removed)} stale file(s):")
            for fp in removed:
                rel = os.path.relpath(fp, PROJECT_ROOT)
                print(f"  REMOVED: {rel}")
    else:
        print("Generation failed!")
        for err in results["errors"]:
            print(f"  ERROR: {err}")


def cmd_validate(args: argparse.Namespace) -> None:
    """Validate all definitions."""
    from .schema import load_all_definitions
    from .field_registry import build_field_registry
    from .validator import validate_definitions

    if not os.path.exists(DEFINITIONS_DIR):
        print(f"Error: Definitions directory not found: {DEFINITIONS_DIR}")
        return

    try:
        agent_config, steps = load_all_definitions(DEFINITIONS_DIR)
        registry = build_field_registry(DEFINITIONS_DIR)
        report = validate_definitions(agent_config, steps, registry)
        print(report.summary())
    except Exception as e:
        print(f"Error loading definitions: {e}")


def cmd_status(args: argparse.Namespace) -> None:
    """Show overview of current definitions."""
    from .schema import load_all_definitions
    from .field_registry import build_field_registry

    if not os.path.exists(DEFINITIONS_DIR):
        print(f"No definitions found at: {DEFINITIONS_DIR}")
        return

    try:
        agent_config, steps = load_all_definitions(DEFINITIONS_DIR)
        registry = build_field_registry(DEFINITIONS_DIR)
    except Exception as e:
        print(f"Error loading definitions: {e}")
        return

    print(f"Agent: {agent_config.name} v{agent_config.version}")
    print(f"Model: {agent_config.model}")
    print(f"Phases: {', '.join(agent_config.phases)}")
    print()
    print(f"Steps Defined: {len(steps)}")
    for step in steps:
        ss_count = len(step.substeps)
        los_count = len(step.all_los_fields)
        doc_count = len(step.all_doc_fields)
        print(f"  {step.id}: {step.name} ({step.phase}) — {ss_count} substeps, {los_count} LOS, {doc_count} doc fields")
    print()
    print(registry.summary())
    print()

    # Check output status
    if os.path.exists(OUTPUT_DIR):
        output_files = []
        for root, dirs, files in os.walk(OUTPUT_DIR):
            for f in files:
                output_files.append(os.path.relpath(os.path.join(root, f), OUTPUT_DIR))
        print(f"Output: {len(output_files)} files generated")
    else:
        print("Output: Not yet generated (run 'factory-reset')")


def cmd_renumber_steps(args: argparse.Namespace) -> None:
    """Renumber steps sequentially, closing any gaps."""
    from .agent_generator import renumber_steps, update_agent

    if not os.path.exists(DEFINITIONS_DIR):
        print(f"Error: Definitions directory not found: {DEFINITIONS_DIR}")
        return

    print(f"Renumbering steps in: {DEFINITIONS_DIR}")
    print()

    results = renumber_steps(DEFINITIONS_DIR, OUTPUT_DIR)

    if not results["renamed"]:
        print("No gaps found — steps are already sequential.")
        return

    print(f"Renumbered {len(results['renamed'])} steps:")
    for r in results["renamed"]:
        print(f"  {r['old_id']} -> {r['new_id']}  ({r['old_file']} -> {r['new_file']})")

    if results["plans_removed"]:
        print(f"\nRemoved {len(results['plans_removed'])} old plan files:")
        for p in results["plans_removed"]:
            print(f"  {p}")

    print("\nRunning update-agent to regenerate derived files...")
    update_results = update_agent(DEFINITIONS_DIR, OUTPUT_DIR)
    if update_results["success"]:
        print(f"Updated {len(update_results['files_updated'])} files.")
    else:
        print("Update failed!")
        for err in update_results["errors"]:
            print(f"  ERROR: {err}")


def cmd_dashboard(args: argparse.Namespace) -> None:
    """Launch the dashboard."""
    from .dashboard.app import run_dashboard
    port = args.port if hasattr(args, "port") else 8501
    run_dashboard(
        definitions_dir=DEFINITIONS_DIR,
        output_dir=OUTPUT_DIR,
        shared_dir=SHARED_DIR,
        port=port,
    )


def main():
    parser = argparse.ArgumentParser(
        prog="factory",
        description="Agent Factory — Build workflow agents from YAML step definitions",
    )
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # update-agent (incremental — the primary command for day-to-day use)
    subparsers.add_parser("update-agent", help="Incremental update: sync configs after UI changes")

    # factory-reset (full scaffold/regenerate)
    p_reset = subparsers.add_parser("factory-reset", help="Full reset: scaffold all files from definitions")
    p_reset.add_argument("--force", action="store_true", help="Force overwrite of ALL files (even existing ones)")

    # generate (backward-compat alias for factory-reset)
    p_gen = subparsers.add_parser("generate", help="(alias for factory-reset)")
    p_gen.add_argument("--all", action="store_true", default=True)
    p_gen.add_argument("--step")
    p_gen.add_argument("--force", action="store_true")

    # new-step
    p_new = subparsers.add_parser("new-step", help="Create a new step definition")
    p_new.add_argument("step_id", help="Step ID (e.g., STEP_03 or just 03)")
    p_new.add_argument("name", help="Step name (e.g., 'MERS/MIN Check')")
    p_new.add_argument("--phase", default="VERIFICATION", help="Phase name")

    # renumber-steps
    subparsers.add_parser("renumber-steps", help="Renumber steps sequentially, closing gaps")

    # validate
    subparsers.add_parser("validate", help="Validate all definitions")

    # status
    subparsers.add_parser("status", help="Show overview of definitions")

    # dashboard
    p_dash = subparsers.add_parser("dashboard", help="Launch the web dashboard")
    p_dash.add_argument("--port", type=int, default=8501, help="Port number")

    args = parser.parse_args()

    if args.command == "update-agent":
        cmd_update_agent(args)
    elif args.command in ("factory-reset", "generate"):
        cmd_factory_reset(args)
    elif args.command == "new-step":
        cmd_new_step(args)
    elif args.command == "renumber-steps":
        cmd_renumber_steps(args)
    elif args.command == "validate":
        cmd_validate(args)
    elif args.command == "status":
        cmd_status(args)
    elif args.command == "dashboard":
        cmd_dashboard(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
