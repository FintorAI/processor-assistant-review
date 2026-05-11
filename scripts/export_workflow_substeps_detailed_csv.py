"""Export a rich, per-substep CSV mirroring the DrawDoc / DocsOrch format.

For every substep declared in `definitions/step_*.yaml`, emits one row with:

    Step, ID, Substep Name, Summary,
    Fields Read (ID: Name), Documents Used,
    Fields Written (ID: Name), Potential Flags

Fields with multi-line values use embedded newlines inside the CSV cell
(properly quoted), so the output renders cleanly when imported to Sheets/Excel.

Usage:
    python3.11 scripts/export_workflow_substeps_detailed_csv.py \
        [--out workflow_substeps_detailed.csv]
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import re
import sys

import yaml

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFINITIONS_DIR = os.path.join(PROJECT_ROOT, "definitions")
FIELDS_CONFIG = os.path.join(PROJECT_ROOT, "output", "config", "fields_config.json")

EM_DASH = "\u2014"


def load_field_name_index() -> dict[str, str]:
    """Map every known field_id -> field_name from the generated fields_config."""
    if not os.path.exists(FIELDS_CONFIG):
        return {}
    with open(FIELDS_CONFIG, "r") as f:
        cfg = json.load(f)
    idx: dict[str, str] = {}
    for entry in cfg.get("los_fields", []):
        fid = entry.get("field_id")
        name = entry.get("field_name") or entry.get("key", "")
        if fid:
            idx[str(fid)] = name
    return idx


def step_num_from_id(step_id: str) -> int:
    m = re.match(r"STEP_(\d+)", step_id or "")
    return int(m.group(1)) if m else -1


def collapse_ws(text: str) -> str:
    """Collapse YAML folded-style whitespace into a single line."""
    if not text:
        return ""
    return re.sub(r"\s+", " ", str(text)).strip()


def fmt_field_read(entry: dict) -> str:
    fid = entry.get("field_id", "")
    name = entry.get("field_name") or entry.get("key", "")
    if fid and name:
        return f"{fid}: {name}"
    return fid or name or ""


def fmt_doc(entry: dict) -> str:
    name = entry.get("document_type", "")
    if entry.get("all_copies"):
        return f"{name} (all copies)"
    return name


def fmt_field_write(entry: dict, name_idx: dict[str, str], local_idx: dict[str, str]) -> str:
    fid = entry.get("field_id", "")
    name = local_idx.get(fid) or name_idx.get(fid, "")
    value = entry.get("value", "")
    cond = entry.get("condition", "")

    head = f"{fid}: {name}" if name else fid
    suffix_parts: list[str] = []
    if value not in (None, ""):
        suffix_parts.append(f"-> {value}")
    if cond and cond != "always":
        suffix_parts.append(f"[if {cond}]")
    if suffix_parts:
        return f"{head} {' '.join(suffix_parts)}"
    return head


def fmt_flag(entry: dict, modifier: dict | None = None) -> str:
    title = collapse_ws(entry.get("title", ""))
    severity = entry.get("severity", "")
    base = f"{title} ({severity})" if severity else title
    if modifier:
        cond = modifier.get("condition", {}) or {}
        field = cond.get("field", "")
        equals = cond.get("equals", "")
        tag = f"{field}={equals}" if field else "rule_modifier"
        base += f" [rule_modifier: {tag}]"
    return base


def join_lines(items: list[str]) -> str:
    items = [x for x in items if x]
    return "\n".join(items) if items else EM_DASH


def collect_modifier_flags(substep: dict) -> list[str]:
    out: list[str] = []
    for mod in substep.get("rule_modifiers") or []:
        for flag in mod.get("flags") or []:
            out.append(fmt_flag(flag, modifier=mod))
    return out


def build_rows(name_idx: dict[str, str]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    yaml_files = sorted(glob.glob(os.path.join(DEFINITIONS_DIR, "step_*.yaml")))

    for path in yaml_files:
        with open(path, "r") as f:
            doc = yaml.safe_load(f) or {}

        step = doc.get("step") or {}
        step_id = step.get("id", "")
        step_name = step.get("name", "")
        n = step_num_from_id(step_id)
        step_label = f"Step {n}: {step_name}" if n >= 0 else step_name

        substeps = doc.get("substeps") or []
        if not substeps:
            rows.append({
                "Step":          step_label,
                "ID":            "",
                "Substep Name":  "",
                "Summary":       collapse_ws(step.get("description", "")),
                "Fields Read (ID: Name)": EM_DASH,
                "Documents Used":         EM_DASH,
                "Fields Written (ID: Name)": EM_DASH,
                "Potential Flags":        EM_DASH,
            })
            continue

        for ss in substeps:
            local_idx: dict[str, str] = {}
            for fr in ss.get("los_fields_read") or []:
                fid = fr.get("field_id")
                if fid:
                    local_idx[str(fid)] = fr.get("field_name") or fr.get("key", "")

            fields_read = [fmt_field_read(e) for e in (ss.get("los_fields_read") or [])]
            docs_used = [fmt_doc(e) for e in (ss.get("doc_types") or [])]
            fields_written = [
                fmt_field_write(e, name_idx, local_idx)
                for e in (ss.get("field_updates") or [])
            ]
            flags = [fmt_flag(e) for e in (ss.get("flags") or [])]
            flags.extend(collect_modifier_flags(ss))

            rows.append({
                "Step":          step_label,
                "ID":            ss.get("id", ""),
                "Substep Name":  ss.get("name", ""),
                "Summary":       collapse_ws(ss.get("description", "")),
                "Fields Read (ID: Name)":    join_lines(fields_read),
                "Documents Used":            join_lines(docs_used),
                "Fields Written (ID: Name)": join_lines(fields_written),
                "Potential Flags":           join_lines(flags),
            })

    return rows


def export(out_path: str) -> int:
    if not os.path.isdir(DEFINITIONS_DIR):
        print(f"ERROR: definitions/ not found at {DEFINITIONS_DIR}")
        return 1

    name_idx = load_field_name_index()
    rows = build_rows(name_idx)

    fieldnames = [
        "Step", "ID", "Substep Name", "Summary",
        "Fields Read (ID: Name)", "Documents Used",
        "Fields Written (ID: Name)", "Potential Flags",
    ]
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    with open(out_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, quoting=csv.QUOTE_MINIMAL)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote {len(rows)} substep rows -> {out_path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--out",
        default=os.path.join(PROJECT_ROOT, "workflow_substeps_detailed.csv"),
        help="Output CSV path (default: <project_root>/workflow_substeps_detailed.csv)",
    )
    args = parser.parse_args()
    return export(args.out)


if __name__ == "__main__":
    sys.exit(main())
