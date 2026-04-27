"""Config Generator — Generate workflow_config.json and fields_config.json."""

from __future__ import annotations

import json
import logging
import os

from .field_registry import FieldRegistry
from .schema import AgentConfig, StepDef
from .step0_generator import generate_step0_definition

logger = logging.getLogger(__name__)


def generate_workflow_config(
    agent_config: AgentConfig,
    steps: list[StepDef],
    registry: FieldRegistry,
    output_dir: str,
) -> str:
    """Generate workflow_config.json.

    Args:
        agent_config: Agent configuration
        steps: All step definitions (NOT including Step 0)
        registry: Field registry
        output_dir: Output directory

    Returns:
        Path to generated file
    """
    # Generate Step 0 definition
    step0 = generate_step0_definition(registry)
    all_steps = [step0] + steps

    # Build step order
    step_order = [s.id for s in all_steps]

    # Build steps config
    steps_config = {}
    for step in all_steps:
        step_num = step.step_number

        # Collect all tools for this step
        all_tools = set()
        substeps_config = {}

        for ss in step.substeps:
            # Extract substep number (e.g., "1" from "2.1")
            parts = str(ss.id).split(".", 1)
            sub_num = parts[1] if len(parts) > 1 else parts[0]
            if ss.tool:
                all_tools.add(ss.tool)

            ss_entry: dict = {
                "name": ss.name,
                "tools": [ss.tool] if ss.tool else [],
            }

            if ss.rule_modifiers:
                ss_entry["rule_modifiers"] = [
                    {
                        "condition": {
                            "field": mod.condition.field,
                            **({"equals": mod.condition.equals} if mod.condition.equals is not None else {}),
                            **({"in": mod.condition.in_values} if mod.condition.in_values is not None else {}),
                            **({"not_equals": mod.condition.not_equals} if mod.condition.not_equals is not None else {}),
                        },
                        "action": mod.action if isinstance(mod.action, str) else mod.action.value,
                        "description": mod.description,
                        "source": mod.source,
                    }
                    for mod in ss.rule_modifiers
                ]

            substeps_config[sub_num] = ss_entry

        steps_config[step.id] = {
            "name": step.name,
            "phase": step.phase,
            "plan_file": _plan_filename(step),
            "tools": sorted(all_tools),
            "substeps": substeps_config,
        }

    # Build phases
    phases = {}
    for step in all_steps:
        if step.phase not in phases:
            phases[step.phase] = []
        phases[step.phase].append(step.id)

    # Collect modifier stats
    total_modifiers = 0
    modifiers_by_field: dict[str, int] = {}
    for step in all_steps:
        for ss in step.substeps:
            for mod in ss.rule_modifiers:
                total_modifiers += 1
                f = mod.condition.field
                modifiers_by_field[f] = modifiers_by_field.get(f, 0) + 1

    filepath = os.path.join(output_dir, "config", "workflow_config.json")

    # Preserve runtime-only settings from existing config (set by dashboard, not in YAML schema)
    existing_skip_substeps: list = []
    existing_dry_run: bool = False
    existing_fixture: dict = {}
    if os.path.exists(filepath):
        try:
            with open(filepath) as f:
                existing = json.load(f)
            existing_dev = existing.get("dev_mode", {})
            existing_skip_substeps = existing_dev.get("skip_substeps", [])
            existing_dry_run = existing_dev.get("dry_run", False)
            existing_fixture = existing_dev.get("fixture", {})
        except Exception:
            pass

    config = {
        "agent": {
            "name": agent_config.name,
            "version": agent_config.version,
            "model": agent_config.model,
        },
        "loan_profile_fields": {
            "loan_type": {"source_field": "1172", "values": ["Conventional", "FHA", "VA", "USDA"]},
            "purpose": {"source_field": "19", "values": ["Purchase", "Refinance", "CashOutRefi"]},
            "state": {"source_field": "14", "values": "2-letter state code"},
            "trust": {"source_field": "CX.CLOSE.TRUST", "values": [True, False]},
            "note_llc": {"source_field": "LO/processor email", "values": [True, False]},
        },
        "rule_modifier_stats": {
            "total_modifiers": total_modifiers,
            "by_field": modifiers_by_field,
        },
        "phases": phases,
        "step_order": step_order,
        "steps": steps_config,
        "dev_mode": {
            "skip_steps": agent_config.skip_steps,
            "skip_substeps": existing_skip_substeps,
            "dry_run": existing_dry_run,
            **({"fixture": existing_fixture} if existing_fixture else {}),
        },
    }
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, "w") as f:
        json.dump(config, f, indent=2)

    logger.info(f"[CONFIG] Generated workflow_config.json: {filepath}")
    return filepath


def generate_fields_config(
    registry: FieldRegistry,
    output_dir: str,
) -> str:
    """Generate fields_config.json from the field registry.

    This is the flat field config that Step 0 uses to know which fields to fetch.
    """
    fields = []

    for fid in sorted(registry.los_fields.keys()):
        info = registry.los_fields[fid]
        fields.append({
            "key": info.key,
            "field_id": info.field_id,
            "field_name": info.field_name,
            "category": info.category,
            "used_by_steps": sorted(set(info.used_by_steps)),
        })

    doc_fields = []
    for key in sorted(registry.doc_fields.keys()):
        info = registry.doc_fields[key]
        doc_fields.append({
            "key": info.key,
            "source_documents": info.source_documents,
            "used_by_steps": sorted(set(info.used_by_steps)),
        })

    config = {
        "los_fields": fields,
        "doc_fields": doc_fields,
        "stats": registry.stats,
    }

    filepath = os.path.join(output_dir, "config", "fields_config.json")
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, "w") as f:
        json.dump(config, f, indent=2)

    logger.info(f"[CONFIG] Generated fields_config.json: {filepath}")
    return filepath


def _plan_filename(step: StepDef) -> str:
    """Get the plan filename for a step (name-based, no step number)."""
    name = step.name.lower().replace(" ", "_").replace("/", "_").replace("-", "_")
    return f"{name}.md"
