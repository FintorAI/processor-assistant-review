#!/usr/bin/env python3
"""Export a detailed per-substep view to workflow_substeps_detailed.csv (root).

Source of truth: definitions/*.yaml (loaded via the factory schema). One row per
substep (STEP_01 onward; STEP_00 data gathering is auto-generated and omitted).
Columns: fields read, documents used, fields written, and potential flags.

Run after any YAML change so the root CSV stays in sync:

    python3.11 scripts/export_workflow_substeps_detailed_csv.py
"""

from __future__ import annotations

import csv
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from factory.schema import load_all_definitions  # noqa: E402

DEFINITIONS = os.path.join(PROJECT_ROOT, "definitions")
OUT = os.path.join(PROJECT_ROOT, "workflow_substeps_detailed.csv")
DASH = "\u2014"  # em dash, used for empty cells

COLUMNS = [
    "Step", "ID", "Substep Name", "Summary",
    "Fields Read (ID: Name)", "Documents Used",
    "Fields Written (ID: Name)", "Potential Flags",
]


def _collapse(text: str) -> str:
    return " ".join((text or "").split())


def _join(lines: list[str]) -> str:
    return "\n".join(lines) if lines else DASH


def _modifier_cond(cond) -> str:
    if cond.equals is not None:
        return f"{cond.field}={cond.equals}"
    if cond.in_values is not None:
        return f"{cond.field} in {cond.in_values}"
    if cond.not_equals is not None:
        return f"{cond.field}!={cond.not_equals}"
    return cond.field


def _written_annotation(condition: str) -> str:
    cond = (condition or "always").strip()
    if cond in ("", "always"):
        return ""
    if cond == "empty":
        return " [if empty]"
    return f" [if {cond}]"


def main() -> None:
    _agent, steps = load_all_definitions(DEFINITIONS)

    # field_id -> human name, gathered from every substep's los_fields_read.
    name_map: dict[str, str] = {}
    for step in steps:
        for ss in step.substeps:
            for fr in ss.los_fields_read:
                if fr.field_id and fr.field_id not in name_map:
                    name_map[fr.field_id] = fr.field_name or ""

    rows = []
    for step in steps:
        for ss in step.substeps:
            fields_read = [
                f"{fr.field_id}: {fr.field_name}".rstrip(": ").rstrip()
                if fr.field_name else fr.field_id
                for fr in ss.los_fields_read
            ]
            docs = [
                dt.document_type + (" (all copies)" if dt.all_copies else "")
                for dt in ss.doc_types
            ]

            written = []
            for fu in ss.field_updates:
                name = name_map.get(fu.field_id, "")
                head = f"{fu.field_id}: {name}" if name else fu.field_id
                written.append(f"{head} -> {fu.value}{_written_annotation(fu.condition)}")

            flags = [f"{fl.title} ({fl.severity})" for fl in ss.flags]
            for mod in ss.rule_modifiers:
                tag = f" [rule_modifier: {_modifier_cond(mod.condition)}]"
                for fl in mod.flags:
                    flags.append(f"{fl.title} ({fl.severity}){tag}")

            rows.append({
                "Step": f"Step {step.step_number}: {step.name}",
                "ID": ss.id,
                "Substep Name": ss.name,
                "Summary": _collapse(ss.description),
                "Fields Read (ID: Name)": _join(fields_read),
                "Documents Used": _join(docs),
                "Fields Written (ID: Name)": _join(written),
                "Potential Flags": _join(flags),
            })

    with open(OUT, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote {len(rows)} substep rows → {os.path.relpath(OUT, PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
