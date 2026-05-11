"""Export the generated workflow to a flat CSV (one row per substep).

Reads `output/config/workflow_config.json` (produced by `python3.11 -m factory
factory-reset` or `update-agent`) and writes a CSV mirroring the format used by
LG-discOrch's `discOrch_workflow_steps.csv`.

Usage:
    python3.11 scripts/export_workflow_csv.py [--out workflow_steps.csv]

Columns:
    Phase, Phase_Name, Step_ID, Step_Num, Step_Name,
    Substep_ID, Substep_Name, Plan_File, Tools

`Tools` is a `;`-separated list of tool names for that substep (CSV-safe).
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKFLOW_CONFIG = os.path.join(PROJECT_ROOT, "output", "config", "workflow_config.json")

PHASE_DISPLAY = {
    "VERIFICATION":     "Verification",
    "INTAKE":           "Intake",
    "DATA_REVIEW":      "Data Review",
    "FORM_UPDATES":     "Form Updates",
    "ORDERS":           "Orders",
    "PREP":             "Prep",
    "PROCESSOR_UPDATE": "Processor Update",
    "SUBMISSION":       "Submission",
}


def step_num(step_id: str) -> int:
    try:
        return int(step_id.replace("STEP_", ""))
    except ValueError:
        return -1


def export(workflow_path: str, out_path: str) -> int:
    if not os.path.exists(workflow_path):
        print(f"ERROR: workflow_config.json not found at {workflow_path}")
        print("Run `python3.11 -m factory factory-reset` first.")
        return 1

    with open(workflow_path, "r") as f:
        cfg = json.load(f)

    step_order = cfg.get("step_order") or sorted(cfg.get("steps", {}).keys())
    steps = cfg.get("steps", {})

    rows: list[dict[str, str]] = []
    for step_id in step_order:
        step = steps.get(step_id)
        if not step:
            continue
        phase = step.get("phase", "")
        phase_name = PHASE_DISPLAY.get(phase, phase.title().replace("_", " "))
        step_name = step.get("name", "")
        plan_file = step.get("plan_file", "")
        substeps = step.get("substeps", {}) or {}

        if not substeps:
            rows.append({
                "Phase":        phase,
                "Phase_Name":   phase_name,
                "Step_ID":      step_id,
                "Step_Num":     str(step_num(step_id)),
                "Step_Name":    step_name,
                "Substep_ID":   "",
                "Substep_Name": "",
                "Plan_File":    plan_file,
                "Tools":        ";".join(step.get("tools", []) or []),
            })
            continue

        for ss_key in sorted(substeps.keys(), key=lambda x: float(x) if x.replace(".", "").isdigit() else 0):
            ss = substeps[ss_key] or {}
            full_substep_id = f"{step_num(step_id)}.{ss_key}"
            rows.append({
                "Phase":        phase,
                "Phase_Name":   phase_name,
                "Step_ID":      step_id,
                "Step_Num":     str(step_num(step_id)),
                "Step_Name":    step_name,
                "Substep_ID":   full_substep_id,
                "Substep_Name": ss.get("name", ""),
                "Plan_File":    plan_file,
                "Tools":        ";".join(ss.get("tools", []) or []),
            })

    fieldnames = [
        "Phase", "Phase_Name", "Step_ID", "Step_Num", "Step_Name",
        "Substep_ID", "Substep_Name", "Plan_File", "Tools",
    ]
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    with open(out_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote {len(rows)} rows -> {out_path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--workflow",
        default=WORKFLOW_CONFIG,
        help=f"Path to workflow_config.json (default: {os.path.relpath(WORKFLOW_CONFIG, PROJECT_ROOT)})",
    )
    parser.add_argument(
        "--out",
        default=os.path.join(PROJECT_ROOT, "workflow_steps.csv"),
        help="Output CSV path (default: <project_root>/workflow_steps.csv)",
    )
    args = parser.parse_args()
    return export(args.workflow, args.out)


if __name__ == "__main__":
    sys.exit(main())
