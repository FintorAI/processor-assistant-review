"""Pydantic models for YAML step definitions.

Defines the schema for step definition YAML files that are the single
source of truth for each workflow step.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator


# ── Enums ──────────────────────────────────────────────────────────────


class RuleType(str, Enum):
    VALUE_CHECK = "value_check"
    VALUE_SET = "value_set"
    EXISTENCE_CHECK = "existence_check"
    FIELD_COMPARISON = "field_comparison"
    VALUE_MATCH = "value_match"
    LOOKUP_TABLE = "lookup_table"
    COMPUTED = "computed"
    SUBAGENT_CALL = "subagent_call"
    CUSTOM = "custom"


class Severity(str, Enum):
    CRITICAL = "critical"
    BLOCKING = "blocking"
    WARNING = "warning"
    INFO = "info"


class ModifierAction(str, Enum):
    ADD = "add"
    REPLACE = "replace"
    SKIP = "skip"


LOAN_PROFILE_FIELDS = frozenset({"loan_type", "purpose", "state", "trust", "note_llc"})


# ── Field References ───────────────────────────────────────────────────


class FieldRef(BaseModel):
    """A reference to a LOS (Encompass) field.

    Only key + field_id are required. The rest is optional metadata.
    """

    key: str = Field(..., description="Internal field key, e.g. 'borrower_first_name'")
    field_id: str = Field(..., description="Encompass field ID, e.g. '4000'")
    field_name: str = Field(default="", description="Human-readable name (optional)")
    category: str = Field(default="", description="Category grouping (optional)")
    purpose: str = Field(default="", description="Why this field is needed (optional)")


class DocFieldRef(BaseModel):
    """A single field extracted from a document."""

    key: str = Field(..., description="Internal field key, e.g. 'borrower_first_name'")
    purpose: str = Field(default="", description="Why this field is needed")


class DocTypeConfig(BaseModel):
    """A document type with its extracted fields.

    Groups doc fields by the document they come from,
    with an option to get all copies (e.g., compare multiple Closing Disclosures).
    """

    document_type: str = Field(..., description="Document type, e.g. 'Closing Disclosure'")
    all_copies: bool = Field(default=False, description="True = get all copies, False = latest only")
    fields: list[DocFieldRef] = Field(default_factory=list, description="Fields to extract from this doc type")


# ── Rules ──────────────────────────────────────────────────────────────


class OnFailAction(BaseModel):
    """What to do when a rule fails."""

    flag: Optional[FlagDef] = None
    auto_write: bool = Field(default=False, description="Auto-write doc value to LOS")


class Rule(BaseModel):
    """A business rule applied during a substep."""

    name: str = Field(..., description="Rule name")
    type: RuleType = Field(default=RuleType.CUSTOM, description="Rule type")
    logic: str = Field(default="", description="Natural language description of the rule")
    check: str = Field(default="", description="Expression to evaluate (for value_check)")
    normalize: list[str] = Field(
        default_factory=list, description="Normalization steps: uppercase, strip, date_format"
    )
    input: list[str] = Field(
        default_factory=list, description="Input field keys (for lookup_table)"
    )
    table: list[dict[str, Any]] = Field(
        default_factory=list, description="Lookup table entries"
    )
    formula: str = Field(default="", description="Formula string (for computed)")
    on_fail: Optional[OnFailAction] = None
    on_mismatch: Optional[OnFailAction] = None

    class Config:
        use_enum_values = True


# ── Flags ──────────────────────────────────────────────────────────────


class FlagDef(BaseModel):
    """Definition of a flag that can be raised."""

    title: str = Field(..., description="Flag title")
    severity: Severity = Field(default=Severity.WARNING, description="Flag severity")
    condition: str = Field(default="", description="When this flag is raised")
    suggestion: str = Field(default="", description="Suggested remedy")

    class Config:
        use_enum_values = True


# ── Field Updates ──────────────────────────────────────────────────────


class FieldUpdate(BaseModel):
    """A field write-back to Encompass."""

    field_id: str = Field(..., description="Encompass field ID to write")
    value: str = Field(default="", description="Value or formula template, e.g. '{computed_vesting}'")
    condition: str = Field(default="always", description="When to write: 'always', condition expression")


# ── Loan Profile ──────────────────────────────────────────────────────


class LoanProfile(BaseModel):
    """Loan profile detected in Step 0, carried in state for rule modifiers."""

    loan_type: str = Field(default="Conventional", description="Conventional, FHA, VA, USDA")
    purpose: str = Field(default="Purchase", description="Purchase, Refinance, CashOutRefi")
    state: str = Field(default="", description="2-letter property state code")
    trust: bool = Field(default=False, description="Trust involvement")
    note_llc: bool = Field(default=False, description="Originated by Note Mortgage LLC")


# ── Rule Modifiers ────────────────────────────────────────────────────


class RuleModifierCondition(BaseModel):
    """Condition that triggers a rule modifier, based on loan profile fields."""

    field: str = Field(..., description="Loan profile field: loan_type, purpose, state, trust, note_llc")
    equals: Optional[str] = None
    in_values: Optional[list[str]] = Field(default=None, alias="in")
    not_equals: Optional[str] = None

    class Config:
        populate_by_name = True

    def matches(self, profile: dict) -> bool:
        """Evaluate this condition against a loan profile dict."""
        val = profile.get(self.field)
        if val is None:
            return False
        if self.equals is not None:
            if isinstance(val, bool):
                return val == (self.equals.lower() in ("true", "yes", "1"))
            return str(val) == str(self.equals)
        if self.in_values is not None:
            return str(val) in [str(v) for v in self.in_values]
        if self.not_equals is not None:
            return str(val) != str(self.not_equals)
        return False


class RuleModifier(BaseModel):
    """Conditional behavior on a substep based on loan profile.

    Attached to substeps via rule_modifiers list. When the condition matches
    the loan profile, the modifier's rules/flags/field_updates are applied
    in addition to (action=add), instead of (action=replace), or the substep
    is skipped (action=skip).
    """

    condition: RuleModifierCondition
    action: ModifierAction = Field(default=ModifierAction.ADD, description="add | replace | skip")
    description: str = Field(default="", description="What this modifier does")
    rules: list[Rule] = Field(default_factory=list)
    flags: list[FlagDef] = Field(default_factory=list)
    field_updates: list[FieldUpdate] = Field(default_factory=list)
    source: str = Field(default="", description="Traceability: FB14, FB15, etc.")

    class Config:
        use_enum_values = True


# ── Subagent ───────────────────────────────────────────────────────────


class SubagentDef(BaseModel):
    """Definition of a subagent call."""

    type: str = Field(..., description="Subagent type, e.g. 'lg-mers'")
    inputs: dict[str, str] = Field(
        default_factory=dict, description="Input mapping: param -> value/template"
    )
    expected: dict[str, Any] = Field(
        default_factory=dict, description="Expected output fields"
    )


# ── Dev Mode / Fixtures ────────────────────────────────────────────────


class FixtureData(BaseModel):
    """Dev mode fixture data for a substep.

    Provides mock inputs and expected outputs for testing generated tools.
    """

    los_fields: dict[str, Any] = Field(default_factory=dict,
        description="Mock LOS field values: {key: value}")
    doc_fields: dict[str, Any] = Field(default_factory=dict,
        description="Mock doc field values: {key: value}")
    subagent_response: dict[str, Any] = Field(default_factory=dict,
        description="Mock subagent response data")
    expected_flags: list[dict[str, Any]] = Field(default_factory=list,
        description="Flags expected to be raised: [{title, severity}]")
    expected_result: dict[str, Any] = Field(default_factory=dict,
        description="Expected keys/values in the tool's result message")
    expected_field_corrections: list[dict[str, Any]] = Field(default_factory=list,
        description="Expected field corrections: [{field_id, value}]")
    description: str = Field(default="",
        description="Human-readable description of what this test case covers")


class DevConfig(BaseModel):
    """Dev mode configuration for a step or substep."""

    skip: bool = Field(default=False, description="Skip this step/substep in dev mode")
    skip_reason: str = Field(default="", description="Why it's skipped")
    fixture: Optional[FixtureData] = None
    prod_fixture: Optional[FixtureData] = None


# ── Substep ────────────────────────────────────────────────────────────


class GuidelineRef(BaseModel):
    """A reference to a guideline JSON file used by a substep tool."""

    filename: str = Field(..., description="Filename in output/guidelines/, e.g. 'vesting_rules.json'")
    description: str = Field(default="", description="What this guideline contains")


class SubstepDef(BaseModel):
    """Definition of a single substep within a step."""

    id: str = Field(..., description="Substep ID, e.g. '1.1'")
    name: str = Field(..., description="Substep name")
    tool: str = Field(..., description="Tool function name for this substep")
    description: str = Field(default="", description="What this substep does")
    condition: Optional[dict[str, Any]] = Field(
        default=None, description="Condition for running: {field: x, equals: y}"
    )
    los_fields_read: list[FieldRef] = Field(default_factory=list)
    doc_types: list[DocTypeConfig] = Field(default_factory=list,
        description="Document types with their extracted fields")
    # Legacy: still accepted on load, converted to doc_types internally
    doc_fields_read: list[Any] = Field(default_factory=list, exclude=True)
    rules: list[Rule] = Field(default_factory=list)
    flags: list[FlagDef] = Field(default_factory=list)
    field_updates: list[FieldUpdate] = Field(default_factory=list)
    guidelines: list[GuidelineRef] = Field(default_factory=list,
        description="Guideline JSON files this substep's tool needs at runtime")
    subagent: Optional[SubagentDef] = None
    rule_modifiers: list[RuleModifier] = Field(
        default_factory=list,
        description="Conditional behavior based on loan profile (loan_type, purpose, state, trust, note_llc)",
    )
    dev: Optional[DevConfig] = None

    # Custom tool params (beyond tool_call_id and state)
    custom_params: list[dict[str, str]] = Field(
        default_factory=list,
        description="Extra tool parameters: [{name, type, description, default}]",
    )


# ── Step ───────────────────────────────────────────────────────────────


class StepDef(BaseModel):
    """Definition of a complete workflow step."""

    id: str = Field(..., description="Step ID, e.g. 'STEP_01'")
    name: str = Field(..., description="Step name")
    phase: str = Field(..., description="Phase name, e.g. 'VERIFICATION'")
    description: str = Field(default="", description="What this step does")
    substeps: list[SubstepDef] = Field(default_factory=list)
    dev: Optional[DevConfig] = None

    @field_validator("id")
    @classmethod
    def validate_step_id(cls, v: str) -> str:
        if not v.startswith("STEP_"):
            raise ValueError(f"Step ID must start with 'STEP_', got '{v}'")
        return v

    @property
    def step_number(self) -> int:
        return int(self.id.replace("STEP_", ""))

    @property
    def tool_names(self) -> list[str]:
        return [ss.tool for ss in self.substeps]

    @property
    def all_los_fields(self) -> list[FieldRef]:
        fields = []
        seen = set()
        for ss in self.substeps:
            for f in ss.los_fields_read:
                if f.field_id not in seen:
                    seen.add(f.field_id)
                    fields.append(f)
        return fields

    @property
    def all_doc_fields(self) -> list[DocFieldRef]:
        """Get all unique doc fields across all substeps."""
        fields = []
        seen = set()
        for ss in self.substeps:
            for dt in ss.doc_types:
                for f in dt.fields:
                    if f.key not in seen:
                        seen.add(f.key)
                        fields.append(f)
        return fields

    @property
    def all_doc_types(self) -> list[DocTypeConfig]:
        """Get all unique doc type configs across all substeps."""
        seen: dict[str, DocTypeConfig] = {}
        for ss in self.substeps:
            for dt in ss.doc_types:
                if dt.document_type not in seen:
                    seen[dt.document_type] = dt
                else:
                    # Merge fields
                    existing = seen[dt.document_type]
                    existing_keys = {f.key for f in existing.fields}
                    for f in dt.fields:
                        if f.key not in existing_keys:
                            existing.fields.append(f)
                    # If any substep needs all_copies, propagate
                    if dt.all_copies:
                        existing.all_copies = True
        return list(seen.values())


# ── Agent Config ───────────────────────────────────────────────────────


class AgentConfig(BaseModel):
    """Top-level agent configuration from _agent.yaml."""

    name: str = Field(..., description="Agent name")
    version: str = Field(default="1.0.0")
    description: str = Field(default="")
    model: str = Field(default="claude-sonnet-4-20250514")
    phases: list[str] = Field(
        default_factory=lambda: ["VERIFICATION", "PREPARATION", "COMPLIANCE", "ORDER_DOCS", "REVIEW"]
    )
    skip_steps: list[str] = Field(default_factory=list)
    system_prompt_hints: str = Field(
        default="", description="Additional instructions for the system prompt"
    )

    @model_validator(mode="before")
    @classmethod
    def _extract_legacy_dev_mode(cls, data: Any) -> Any:
        """Pull skip_steps out of legacy dev_mode block for backward compat."""
        if isinstance(data, dict) and "dev_mode" in data:
            dm = data.pop("dev_mode")
            if isinstance(dm, dict):
                if "skip_steps" in dm and "skip_steps" not in data:
                    data["skip_steps"] = dm["skip_steps"]
        return data


# ── Loading Functions ──────────────────────────────────────────────────


# Update forward refs now that FlagDef is defined
OnFailAction.model_rebuild()


def load_agent_config(path: str) -> AgentConfig:
    """Load agent configuration from _agent.yaml."""
    with open(path, "r") as f:
        data = yaml.safe_load(f)
    if not data or not isinstance(data, dict):
        raise ValueError(f"Invalid or empty agent config: {path}")
    agent_data = data.get("agent", data)
    if not isinstance(agent_data, dict):
        raise ValueError(f"Agent config 'agent' key must be a dict: {path}")
    return AgentConfig(**agent_data)


def load_step_definition(path: str) -> StepDef:
    """Load a step definition from a YAML file."""
    with open(path, "r") as f:
        data = yaml.safe_load(f)

    if not data or not isinstance(data, dict):
        raise ValueError(f"Invalid or empty step definition: {path}")

    step_data = data.get("step", {})
    if not isinstance(step_data, dict):
        raise ValueError(f"Step 'step' key must be a dict: {path}")
    substeps_data = data.get("substeps", [])
    dev_data = data.get("dev", None)

    # Build substeps with backward compatibility
    substeps = []
    for ss_raw in substeps_data:
        if not isinstance(ss_raw, dict):
            continue
        # Convert legacy doc_fields_read to doc_types if needed
        if "doc_fields_read" in ss_raw and "doc_types" not in ss_raw:
            legacy_fields = ss_raw.pop("doc_fields_read", [])
            if legacy_fields:
                # Group by source_document
                by_doc: dict[str, list] = {}
                for df in legacy_fields:
                    if not isinstance(df, dict):
                        continue
                    src = df.get("source_document", "Unknown")
                    if src not in by_doc:
                        by_doc[src] = []
                    by_doc[src].append({
                        "key": df.get("key", ""),
                        "purpose": df.get("purpose", ""),
                    })
                ss_raw["doc_types"] = [
                    {"document_type": doc, "all_copies": False, "fields": fields}
                    for doc, fields in by_doc.items()
                ]
        elif "doc_fields_read" in ss_raw:
            ss_raw.pop("doc_fields_read", None)

        substeps.append(SubstepDef(**ss_raw))

    # Sort substeps by numeric ID (e.g. "1.10" after "1.9", not after "1.1")
    def _substep_sort_key(ss: SubstepDef) -> tuple[int, ...]:
        try:
            return tuple(int(p) for p in ss.id.split("."))
        except ValueError:
            return (999999,)

    substeps.sort(key=_substep_sort_key)

    return StepDef(
        id=step_data.get("id", ""),
        name=step_data.get("name", ""),
        phase=step_data.get("phase", ""),
        description=step_data.get("description", ""),
        substeps=substeps,
        dev=DevConfig(**dev_data) if isinstance(dev_data, dict) else None,
    )


def load_all_definitions(definitions_dir: str) -> tuple[AgentConfig, list[StepDef]]:
    """Load agent config and all step definitions from a directory.

    Returns:
        Tuple of (AgentConfig, sorted list of StepDef)
    """
    import os

    # Load agent config
    agent_path = os.path.join(definitions_dir, "_agent.yaml")
    agent_config = load_agent_config(agent_path)

    # Load all step definitions
    steps = []
    for filename in sorted(os.listdir(definitions_dir)):
        if filename.startswith("step_") and filename.endswith(".yaml"):
            filepath = os.path.join(definitions_dir, filename)
            step_def = load_step_definition(filepath)
            steps.append(step_def)

    # Sort by step number
    steps.sort(key=lambda s: s.step_number)

    return agent_config, steps
