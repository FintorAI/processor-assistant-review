"""Validator - Check step definitions for consistency and completeness.

Validates:
- Substep IDs are sequential
- Tool names are unique across all steps
- Phase names are valid
- Required YAML fields are present
- Dev fixtures reference valid field keys
- No orphan fields (extracted but never used)
- workflow_config.json is in sync with YAML definitions
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .field_registry import FieldRegistry
from .schema import LOAN_PROFILE_FIELDS, AgentConfig, ModifierAction, StepDef


class ValidationLevel(str, Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


@dataclass
class ValidationResult:
    level: ValidationLevel
    message: str
    location: str = ""  # e.g., "step_02/substep_2.3"

    def __str__(self) -> str:
        prefix = {"error": "ERROR", "warning": "WARN ", "info": "INFO "}[self.level]
        loc = f" [{self.location}]" if self.location else ""
        return f"  {prefix}{loc}: {self.message}"


@dataclass
class ValidationReport:
    results: list[ValidationResult] = field(default_factory=list)

    @property
    def errors(self) -> list[ValidationResult]:
        return [r for r in self.results if r.level == ValidationLevel.ERROR]

    @property
    def warnings(self) -> list[ValidationResult]:
        return [r for r in self.results if r.level == ValidationLevel.WARNING]

    @property
    def infos(self) -> list[ValidationResult]:
        return [r for r in self.results if r.level == ValidationLevel.INFO]

    @property
    def is_valid(self) -> bool:
        return len(self.errors) == 0

    def summary(self) -> str:
        lines = [
            f"Validation: {'PASSED' if self.is_valid else 'FAILED'}",
            f"  Errors:   {len(self.errors)}",
            f"  Warnings: {len(self.warnings)}",
            f"  Info:     {len(self.infos)}",
        ]
        if self.results:
            lines.append("")
            for r in self.results:
                lines.append(str(r))
        return "\n".join(lines)

    def add(self, level: ValidationLevel, message: str, location: str = "") -> None:
        self.results.append(ValidationResult(level=level, message=message, location=location))

    def error(self, message: str, location: str = "") -> None:
        self.add(ValidationLevel.ERROR, message, location)

    def warning(self, message: str, location: str = "") -> None:
        self.add(ValidationLevel.WARNING, message, location)

    def info(self, message: str, location: str = "") -> None:
        self.add(ValidationLevel.INFO, message, location)


def validate_definitions(
    agent_config: AgentConfig,
    steps: list[StepDef],
    registry: FieldRegistry,
    output_dir: str | None = None,
) -> ValidationReport:
    """Run all validations on the step definitions.

    Args:
        agent_config: Agent configuration
        steps: All step definitions
        registry: Built field registry
        output_dir: Optional output directory to validate workflow_config.json sync

    Returns:
        ValidationReport with all findings
    """
    report = ValidationReport()

    _validate_agent_config(agent_config, report)
    _validate_step_ids(steps, report)
    _validate_substep_ids(steps, report)
    _validate_tool_names(steps, report)
    _validate_phases(agent_config, steps, report)
    _validate_fields(steps, registry, report)
    _validate_rule_modifiers(steps, report)
    _validate_fixtures(steps, registry, report)

    # Validate workflow_config.json is in sync
    if output_dir:
        _validate_workflow_config_sync(agent_config, steps, registry, output_dir, report)

    # Summary info
    report.info(
        f"Total: {len(steps)} steps, {len(registry.los_fields)} LOS fields, "
        f"{len(registry.doc_fields)} doc fields, {len(registry.required_documents)} doc types"
    )

    return report


def _validate_agent_config(config: AgentConfig, report: ValidationReport) -> None:
    """Validate agent-level configuration."""
    if not config.name:
        report.error("Agent name is required", "_agent.yaml")
    if not config.phases:
        report.error("At least one phase must be defined", "_agent.yaml")


def _validate_step_ids(steps: list[StepDef], report: ValidationReport) -> None:
    """Validate step IDs are properly formatted and sequential."""
    seen_ids = set()
    for step in steps:
        if step.id in seen_ids:
            report.error(f"Duplicate step ID: {step.id}", step.id)
        seen_ids.add(step.id)

        if not step.name:
            report.error(f"Step name is required", step.id)

        if not step.substeps:
            report.warning(f"Step has no substeps defined", step.id)


def _validate_substep_ids(steps: list[StepDef], report: ValidationReport) -> None:
    """Validate substep IDs are sequential within each step."""
    for step in steps:
        step_num = step.step_number
        seen_sub_ids = set()

        for i, ss in enumerate(step.substeps):
            # Check format: should be "N.M" where N is step number
            parts = ss.id.split(".")
            if len(parts) != 2:
                report.error(
                    f"Substep ID '{ss.id}' should be in format 'N.M'",
                    f"{step.id}/{ss.id}",
                )
                continue

            try:
                prefix = int(parts[0])
                suffix = int(parts[1])
            except ValueError:
                report.error(
                    f"Substep ID '{ss.id}' has non-numeric parts",
                    f"{step.id}/{ss.id}",
                )
                continue

            if prefix != step_num:
                report.error(
                    f"Substep '{ss.id}' prefix doesn't match step number {step_num}",
                    f"{step.id}/{ss.id}",
                )

            if ss.id in seen_sub_ids:
                report.error(f"Duplicate substep ID: {ss.id}", f"{step.id}/{ss.id}")
            seen_sub_ids.add(ss.id)

            if not ss.name:
                report.error(f"Substep name is required", f"{step.id}/{ss.id}")

            if not ss.tool:
                report.error(f"Substep tool name is required", f"{step.id}/{ss.id}")


def _validate_tool_names(steps: list[StepDef], report: ValidationReport) -> None:
    """Validate tool names are unique across all steps."""
    tool_to_step: dict[str, list[str]] = {}

    for step in steps:
        for ss in step.substeps:
            tool_name = ss.tool
            if tool_name not in tool_to_step:
                tool_to_step[tool_name] = []
            tool_to_step[tool_name].append(f"{step.id}/{ss.id}")

    for tool_name, locations in tool_to_step.items():
        if len(locations) > 1:
            # Same tool in multiple substeps is OK (e.g., run_category_verification)
            # But same tool in different STEPS is a warning
            step_ids = set(loc.split("/")[0] for loc in locations)
            if len(step_ids) > 1:
                report.warning(
                    f"Tool '{tool_name}' is used in multiple steps: {', '.join(sorted(step_ids))}",
                    tool_name,
                )


def _validate_phases(
    config: AgentConfig, steps: list[StepDef], report: ValidationReport
) -> None:
    """Validate all steps reference valid phases."""
    valid_phases = set(config.phases)

    for step in steps:
        if step.phase not in valid_phases:
            report.error(
                f"Phase '{step.phase}' is not in agent phases: {config.phases}",
                step.id,
            )


def _validate_fields(
    steps: list[StepDef], registry: FieldRegistry, report: ValidationReport
) -> None:
    """Validate field references."""
    # Check for fields with empty field_id
    for step in steps:
        for ss in step.substeps:
            for fref in ss.los_fields_read:
                if not fref.field_id:
                    report.error(
                        f"LOS field '{fref.key}' has empty field_id",
                        f"{step.id}/{ss.id}",
                    )
                if not fref.key:
                    report.error(
                        f"LOS field with field_id '{fref.field_id}' has empty key",
                        f"{step.id}/{ss.id}",
                    )

            for dt in ss.doc_types:
                if not dt.document_type:
                    report.error(
                        f"Doc type has empty document_type",
                        f"{step.id}/{ss.id}",
                    )
                for dref in dt.fields:
                    if not dref.key:
                        report.error(
                            f"Doc field in '{dt.document_type}' has empty key",
                            f"{step.id}/{ss.id}",
                        )

    # Check for duplicate field keys with different field_ids
    key_to_ids: dict[str, set[str]] = {}
    for info in registry.los_fields.values():
        if info.key not in key_to_ids:
            key_to_ids[info.key] = set()
        key_to_ids[info.key].add(info.field_id)

    for key, ids in key_to_ids.items():
        if len(ids) > 1:
            report.error(
                f"Field key '{key}' maps to multiple field_ids: {sorted(ids)}",
                "field_registry",
            )

    # Check for same field_id used with different keys (data inconsistency)
    id_to_keys: dict[str, set[str]] = {}
    for step in steps:
        for ss in step.substeps:
            for fref in ss.los_fields_read:
                if fref.field_id not in id_to_keys:
                    id_to_keys[fref.field_id] = set()
                id_to_keys[fref.field_id].add(fref.key)

    for fid, keys in id_to_keys.items():
        if len(keys) > 1:
            report.error(
                f"Field ID '{fid}' is used with different keys: {sorted(keys)}. "
                f"Use the same key everywhere for consistency.",
                "field_registry",
            )


def _validate_rule_modifiers(
    steps: list[StepDef], report: ValidationReport
) -> None:
    """Validate rule_modifiers on all substeps."""
    valid_actions = {a.value for a in ModifierAction}
    modifier_count = 0

    for step in steps:
        for ss in step.substeps:
            for i, mod in enumerate(ss.rule_modifiers):
                loc = f"{step.id}/{ss.id}/rule_modifier[{i}]"
                modifier_count += 1

                # Condition field must be one of the 5 discriminators
                if mod.condition.field not in LOAN_PROFILE_FIELDS:
                    report.error(
                        f"rule_modifier condition.field '{mod.condition.field}' "
                        f"must be one of: {sorted(LOAN_PROFILE_FIELDS)}",
                        loc,
                    )

                # Exactly one comparison operator must be set
                ops_set = sum([
                    mod.condition.equals is not None,
                    mod.condition.in_values is not None,
                    mod.condition.not_equals is not None,
                ])
                if ops_set == 0:
                    report.error(
                        "rule_modifier condition must have at least one of: equals, in, not_equals",
                        loc,
                    )
                elif ops_set > 1:
                    report.warning(
                        "rule_modifier condition has multiple operators set; only one will match",
                        loc,
                    )

                # Action must be valid
                action_val = mod.action if isinstance(mod.action, str) else mod.action.value
                if action_val not in valid_actions:
                    report.error(
                        f"rule_modifier action '{action_val}' must be one of: {sorted(valid_actions)}",
                        loc,
                    )

                # Skip modifiers shouldn't have rules/flags
                if action_val == "skip" and (mod.rules or mod.flags or mod.field_updates):
                    report.warning(
                        "rule_modifier with action='skip' has rules/flags/field_updates that will be ignored",
                        loc,
                    )

                # Warn if modifier condition overlaps with substep's own condition
                if ss.condition and mod.condition.field in (ss.condition or {}):
                    report.warning(
                        f"rule_modifier condition.field '{mod.condition.field}' "
                        f"also appears in substep condition — may be redundant",
                        loc,
                    )

    if modifier_count > 0:
        report.info(f"Validated {modifier_count} rule_modifiers across all substeps")


def _validate_fixtures(
    steps: list[StepDef], registry: FieldRegistry, report: ValidationReport
) -> None:
    """Validate dev fixtures reference valid field keys."""
    all_los_keys = set(registry.los_fields_by_key.keys())
    all_doc_keys = set(registry.doc_fields.keys())

    for step in steps:
        for ss in step.substeps:
            if ss.dev and ss.dev.fixture:
                for key in ss.dev.fixture.los_fields:
                    if key not in all_los_keys:
                        report.warning(
                            f"Fixture LOS field '{key}' is not in any step's los_fields_read",
                            f"{step.id}/{ss.id}/fixture",
                        )
                for key in ss.dev.fixture.doc_fields:
                    if key not in all_doc_keys:
                        report.warning(
                            f"Fixture doc field '{key}' is not in any step's doc_types",
                            f"{step.id}/{ss.id}/fixture",
                        )


def _validate_workflow_config_sync(
    agent_config: AgentConfig,
    steps: list[StepDef],
    registry: FieldRegistry,
    output_dir: str,
    report: ValidationReport,
) -> None:
    """Validate that workflow_config.json is in sync with YAML definitions.

    This ensures the runtime's dynamic tool/plan resolution matches
    the current step definitions.
    """
    config_path = os.path.join(output_dir, "config", "workflow_config.json")

    if not os.path.exists(config_path):
        report.warning(
            "workflow_config.json not found — run 'factory generate' to create it. "
            "Dynamic tooling/planning will not work without this file.",
            "workflow_config.json",
        )
        return

    try:
        with open(config_path, "r") as f:
            config = json.load(f)
    except Exception as e:
        report.error(f"Cannot parse workflow_config.json: {e}", "workflow_config.json")
        return

    config_steps = config.get("steps", {})
    config_step_order = config.get("step_order", [])

    # Check step order includes Step 0 + all defined steps
    expected_step_ids = {"STEP_00"} | {s.id for s in steps}
    config_step_ids = set(config_step_order)

    missing = expected_step_ids - config_step_ids
    extra = config_step_ids - expected_step_ids

    if missing:
        report.warning(
            f"workflow_config.json is missing steps: {sorted(missing)}. "
            "Run 'factory generate' or sync config to fix.",
            "workflow_config.json",
        )
    if extra:
        report.warning(
            f"workflow_config.json has extra steps not in definitions: {sorted(extra)}. "
            "Run 'factory generate' or sync config to fix.",
            "workflow_config.json",
        )

    # Check each step's tools match the YAML definition
    for step in steps:
        step_cfg = config_steps.get(step.id)
        if not step_cfg:
            continue

        # Expected tools: union of all substep tools
        yaml_tools = sorted(set(ss.tool for ss in step.substeps if ss.tool))
        config_tools = sorted(step_cfg.get("tools", []))

        if yaml_tools != config_tools:
            report.warning(
                f"Tool mismatch for {step.id}: "
                f"YAML defines tools {yaml_tools}, "
                f"config has {config_tools}. "
                "Run 'factory generate' or sync config to fix.",
                f"workflow_config.json/{step.id}",
            )

        # Check substep count
        yaml_ss_count = len(step.substeps)
        config_ss_count = len(step_cfg.get("substeps", {}))
        if yaml_ss_count != config_ss_count:
            report.warning(
                f"Substep count mismatch for {step.id}: "
                f"YAML has {yaml_ss_count} substeps, config has {config_ss_count}. "
                "Run 'factory generate' or sync config to fix.",
                f"workflow_config.json/{step.id}",
            )

        # Check each substep's tools
        config_substeps = step_cfg.get("substeps", {})
        for ss in step.substeps:
            ss_parts = str(ss.id).split(".")
            ss_key = ss_parts[-1] if len(ss_parts) > 1 else ss_parts[0]

            config_ss = config_substeps.get(ss_key)
            if not config_ss:
                report.warning(
                    f"Substep {ss.id} not found in workflow_config.json",
                    f"workflow_config.json/{step.id}/{ss.id}",
                )
                continue

            config_ss_tools = config_ss.get("tools", [])
            yaml_ss_tools = [ss.tool] if ss.tool else []
            if yaml_ss_tools != config_ss_tools:
                report.warning(
                    f"Tool mismatch for substep {ss.id}: "
                    f"YAML tool='{ss.tool}', config has {config_ss_tools}",
                    f"workflow_config.json/{step.id}/{ss.id}",
                )
