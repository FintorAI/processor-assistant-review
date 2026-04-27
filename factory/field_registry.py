"""Field Registry - Scan all step definitions and collect field usage.

The field registry is the central index of every LOS field and doc field
used anywhere across the workflow. It drives Step 0 auto-generation:
Step 0 fetches exactly the union of all fields needed by all steps.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from .schema import (
    AgentConfig,
    DocFieldRef,
    DocTypeConfig,
    FieldRef,
    StepDef,
    load_all_definitions,
)


@dataclass
class LOSFieldInfo:
    """Aggregated info about a single LOS field across all steps."""

    key: str
    field_id: str
    field_name: str
    category: str
    purpose: str
    used_by_steps: list[str] = field(default_factory=list)
    used_by_substeps: list[str] = field(default_factory=list)


@dataclass
class DocFieldInfo:
    """Aggregated info about a single doc field across all steps."""

    key: str
    source_documents: list[str] = field(default_factory=list)
    purpose: str = ""
    used_by_steps: list[str] = field(default_factory=list)
    used_by_substeps: list[str] = field(default_factory=list)


@dataclass
class DocTypeInfo:
    """Aggregated info about a document type across all steps."""

    document_type: str
    all_copies: bool = False
    fields: list[str] = field(default_factory=list)  # field keys
    used_by_steps: list[str] = field(default_factory=list)


@dataclass
class FieldRegistry:
    """Complete field registry built from all step definitions."""

    # All LOS fields indexed by field_id
    los_fields: dict[str, LOSFieldInfo] = field(default_factory=dict)

    # All LOS fields indexed by key (for key-based lookup)
    los_fields_by_key: dict[str, LOSFieldInfo] = field(default_factory=dict)

    # All doc fields indexed by key
    doc_fields: dict[str, DocFieldInfo] = field(default_factory=dict)

    # Required document types (union of all source_documents)
    required_documents: set[str] = field(default_factory=set)

    # Doc type info indexed by document_type
    doc_type_info: dict[str, DocTypeInfo] = field(default_factory=dict)

    # Fields grouped by step
    los_fields_by_step: dict[str, list[FieldRef]] = field(default_factory=dict)
    doc_fields_by_step: dict[str, list[DocFieldRef]] = field(default_factory=dict)

    # Fields grouped by category
    los_fields_by_category: dict[str, list[LOSFieldInfo]] = field(default_factory=dict)

    # Step definitions (for reference)
    steps: list[StepDef] = field(default_factory=list)
    agent_config: AgentConfig | None = None

    # ── Query Methods ──────────────────────────────────────────────

    def get_all_field_ids(self) -> list[str]:
        """Get all unique LOS field IDs for Step 0 batch read."""
        return sorted(self.los_fields.keys())

    def get_all_field_keys(self) -> list[str]:
        """Get all unique LOS field keys."""
        return sorted(self.los_fields_by_key.keys())

    def get_all_doc_types(self) -> list[str]:
        """Get all unique document types for Step 0 doc extraction."""
        return sorted(self.required_documents)

    def get_los_fields_for_step(self, step_id: str) -> list[FieldRef]:
        """Get LOS fields needed by a specific step."""
        return self.los_fields_by_step.get(step_id, [])

    def get_doc_fields_for_step(self, step_id: str) -> list[DocFieldRef]:
        """Get doc fields needed by a specific step."""
        return self.doc_fields_by_step.get(step_id, [])

    def get_field_by_id(self, field_id: str) -> LOSFieldInfo | None:
        """Get field info by Encompass field ID."""
        return self.los_fields.get(field_id)

    def get_field_by_key(self, key: str) -> LOSFieldInfo | None:
        """Get field info by internal key."""
        return self.los_fields_by_key.get(key)

    # ── Stats ──────────────────────────────────────────────────────

    @property
    def stats(self) -> dict[str, Any]:
        return {
            "total_los_fields": len(self.los_fields),
            "total_doc_fields": len(self.doc_fields),
            "total_document_types": len(self.required_documents),
            "total_steps": len(self.steps),
            "categories": sorted(self.los_fields_by_category.keys()),
            "document_types": sorted(self.required_documents),
        }

    def summary(self) -> str:
        """Human-readable summary."""
        lines = [
            f"Field Registry Summary",
            f"  LOS Fields:      {len(self.los_fields)} unique field IDs",
            f"  Doc Fields:      {len(self.doc_fields)} unique keys",
            f"  Document Types:  {len(self.required_documents)}",
            f"  Steps Defined:   {len(self.steps)}",
            f"  Categories:      {', '.join(sorted(self.los_fields_by_category.keys()))}",
        ]
        return "\n".join(lines)


def _merge_standalone_los_fields(registry: FieldRegistry, definitions_dir: str):
    """Merge standalone LOS fields from los_fields_standalone.json into the registry.

    Standalone fields are those defined directly in the Field Registry UI,
    not yet attached to any substep. They get a `used_by: ["standalone"]` marker
    so Step 0 still fetches them.
    """
    import json

    project_root = os.path.dirname(definitions_dir)
    standalone_path = os.path.join(project_root, "output", "config", "los_fields_standalone.json")
    if not os.path.exists(standalone_path):
        return

    try:
        with open(standalone_path) as f:
            data = json.load(f)
    except Exception:
        return

    for field_id, info in data.get("fields", {}).items():
        if field_id in registry.los_fields:
            continue

        key = info.get("key", field_id)
        los_info = LOSFieldInfo(
            key=key,
            field_id=field_id,
            field_name=info.get("field_name", key),
            category=info.get("category", ""),
            purpose="Standalone field (defined in Field Registry)",
            used_by_steps=["standalone"],
            used_by_substeps=[],
        )
        registry.los_fields[field_id] = los_info
        if key not in registry.los_fields_by_key:
            registry.los_fields_by_key[key] = los_info

        cat = los_info.category
        if cat:
            if cat not in registry.los_fields_by_category:
                registry.los_fields_by_category[cat] = []
            existing_ids = {f.field_id for f in registry.los_fields_by_category[cat]}
            if field_id not in existing_ids:
                registry.los_fields_by_category[cat].append(los_info)


def build_field_registry(definitions_dir: str) -> FieldRegistry:
    """Build a complete field registry by scanning all step definitions.

    Also merges standalone LOS fields from los_fields_standalone.json.

    Args:
        definitions_dir: Path to the definitions/ directory

    Returns:
        Populated FieldRegistry
    """
    agent_config, steps = load_all_definitions(definitions_dir)

    registry = FieldRegistry(
        steps=steps,
        agent_config=agent_config,
    )

    for step_def in steps:
        step_los_fields: list[FieldRef] = []
        step_doc_fields: list[DocFieldRef] = []

        for substep in step_def.substeps:
            # ── Process LOS fields ──
            for fref in substep.los_fields_read:
                fid = fref.field_id
                key = fref.key

                if fid not in registry.los_fields:
                    info = LOSFieldInfo(
                        key=key,
                        field_id=fid,
                        field_name=fref.field_name if fref.field_name else key,
                        category=fref.category,
                        purpose=fref.purpose,
                    )
                    registry.los_fields[fid] = info
                    registry.los_fields_by_key[key] = info
                elif key not in registry.los_fields_by_key:
                    # Same field_id seen before — also index by this key alias
                    registry.los_fields_by_key[key] = registry.los_fields[fid]

                registry.los_fields[fid].used_by_steps.append(step_def.id)
                registry.los_fields[fid].used_by_substeps.append(substep.id)
                step_los_fields.append(fref)

                # Track by category
                cat = fref.category
                if cat not in registry.los_fields_by_category:
                    registry.los_fields_by_category[cat] = []
                # Avoid duplicates within same category
                existing_ids = {f.field_id for f in registry.los_fields_by_category[cat]}
                if fid not in existing_ids:
                    registry.los_fields_by_category[cat].append(registry.los_fields[fid])

            # ── Process Doc types + fields ──
            for dt_config in substep.doc_types:
                doc_type_name = dt_config.document_type
                registry.required_documents.add(doc_type_name)

                # Track doc type info
                if doc_type_name not in registry.doc_type_info:
                    registry.doc_type_info[doc_type_name] = DocTypeInfo(
                        document_type=doc_type_name,
                        all_copies=dt_config.all_copies,
                    )
                dti = registry.doc_type_info[doc_type_name]
                if dt_config.all_copies:
                    dti.all_copies = True
                if step_def.id not in dti.used_by_steps:
                    dti.used_by_steps.append(step_def.id)

                for dref in dt_config.fields:
                    key = dref.key
                    if key not in dti.fields:
                        dti.fields.append(key)

                    if key not in registry.doc_fields:
                        registry.doc_fields[key] = DocFieldInfo(
                            key=key,
                            purpose=dref.purpose,
                        )

                    info = registry.doc_fields[key]
                    info.used_by_steps.append(step_def.id)
                    info.used_by_substeps.append(substep.id)

                    if doc_type_name not in info.source_documents:
                        info.source_documents.append(doc_type_name)

                    step_doc_fields.append(DocFieldRef(key=key, purpose=dref.purpose))

        registry.los_fields_by_step[step_def.id] = step_los_fields
        registry.doc_fields_by_step[step_def.id] = step_doc_fields

    # Deduplicate used_by lists
    for info in registry.los_fields.values():
        info.used_by_steps = sorted(set(info.used_by_steps))
        info.used_by_substeps = sorted(set(info.used_by_substeps))
    for info in registry.doc_fields.values():
        info.used_by_steps = sorted(set(info.used_by_steps))
        info.used_by_substeps = sorted(set(info.used_by_substeps))

    # Merge standalone LOS fields from JSON config
    _merge_standalone_los_fields(registry, definitions_dir)

    return registry
