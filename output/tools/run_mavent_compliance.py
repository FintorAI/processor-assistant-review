"""run_mavent_compliance — Tool for substep 13.2: Run Mavent Compliance

Step 13 (STEP_13): Fraud & Compliance
Phase: DATA_REVIEW

Runs Mavent via Encompass ECS API (checklist §15 #3–#4):
  • GET existing compliance report
  • POST Review report when missing or force_refresh=True
  • Per-category flags + full messages in state.mavent_results

Ported from LG-discOrch verify_mavent_compliance with full message persistence
for the processor dashboard detail panel.

# FACTORY-LOCK: true
"""

import json
import logging
from datetime import datetime, timezone
from typing import Annotated, Any, Dict, List, Optional

from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.prebuilt import InjectedState
from langgraph.types import Command

logger = logging.getLogger(__name__)

SUBSTEP = "13.2"
_NON_PASS = frozenset({"FAIL", "ALERT", "WARNING", "DID NOT PASS", "DIDNOTPASS"})
_SUMMARY_BULLET_LIMIT = 3
_SUMMARY_CHAR_LIMIT = 250


def _status_to_flag(check_name: str, status: str) -> Dict[str, Any]:
    status_upper = (status or "N/A").upper()
    slug = check_name.lower().replace("/", "_").replace(" ", "_")

    if status_upper == "PASS":
        return {
            "severity": "info",
            "title": f"Mavent {check_name}: PASS",
            "details": f"{check_name} compliance check passed.",
            "action": "info",
            "check_id": f"mavent_{slug}_pass",
        }
    if status_upper in ("FAIL", "DID NOT PASS", "DIDNOTPASS"):
        return {
            "severity": "error",
            "title": f"Mavent {check_name}: FAIL",
            "details": f"{check_name} compliance check FAILED — requires processor attention.",
            "remedy": "Escalate",
            "action": "escalation_required",
            "check_id": f"mavent_{slug}_fail",
        }
    if status_upper == "ALERT":
        return {
            "severity": "warning",
            "title": f"Mavent {check_name}: ALERT",
            "details": f"{check_name} requires processor attention.",
            "remedy": "Manual",
            "action": "processor_attention_required",
            "check_id": f"mavent_{slug}_alert",
        }
    if status_upper == "WARNING":
        return {
            "severity": "warning",
            "title": f"Mavent {check_name}: WARNING",
            "details": f"{check_name} has warnings — processor should review.",
            "remedy": "Manual",
            "action": "processor_review_required",
            "check_id": f"mavent_{slug}_warning",
        }
    return {
        "severity": "warning",
        "title": f"Mavent {check_name}: {status_upper}",
        "details": f"{check_name} check was not processed — Mavent report may need regeneration.",
        "remedy": "Manual",
        "action": "processor_review_required",
        "check_id": f"mavent_{slug}_not_processed",
    }


def _stamp_flags(raw_flags: List[dict]) -> List[dict]:
    ts = datetime.now(timezone.utc).isoformat()
    out = []
    for f in raw_flags:
        stamped = dict(f)
        stamped.setdefault("substep", SUBSTEP)
        stamped.setdefault("resolved", False)
        stamped.setdefault("timestamp", ts)
        out.append(stamped)
    return out


def _normalize_message(m: dict) -> dict:
    return {
        "status": m.get("Status") or m.get("status") or "",
        "message": m.get("Message") or m.get("message") or "",
        "service_group": m.get("ServiceGroup") or m.get("serviceGroup") or "",
    }


def _group_messages_by_category(
    compliance_messages: List[dict],
    category_results: Dict[str, dict],
) -> Dict[str, List[dict]]:
    grouped: Dict[str, List[dict]] = {name: [] for name in category_results}
    for raw in compliance_messages:
        msg = _normalize_message(raw)
        sg = (msg["service_group"] or "").strip()
        if not sg:
            continue
        matched = None
        for cat_name in category_results:
            if cat_name.lower() in sg.lower() or sg.lower() in cat_name.lower():
                matched = cat_name
                break
        if matched is None:
            matched = sg
            grouped.setdefault(matched, [])
        status = (msg["status"] or "").strip().upper()
        if status in ("PASS", "PASS "):
            continue
        grouped.setdefault(matched, []).append(msg)
    return grouped


def _fetch_report(loan_id: str, state: dict, force_refresh: bool) -> tuple[Optional[dict], bool, Optional[str]]:
    """Return (report, api_ran, error_message)."""
    from encompass_client import get_mavent_results, run_mavent

    api_ran = False
    if not force_refresh:
        try:
            report = get_mavent_results(loan_id, state=state)
            if isinstance(report, dict) and report.get("found") is False:
                report = None
            elif isinstance(report, dict) and report:
                api_ran = True
                return report, api_ran, None
        except Exception as exc:
            logger.warning("[RUN_MAVENT_COMPLIANCE] ECS GET failed: %s", exc)

    try:
        report = run_mavent(loan_id, run_type="FULL", state=state)
        api_ran = True
        if isinstance(report, dict) and report.get("found") is False:
            return None, api_ran, None
        return report or None, api_ran, None
    except Exception as exc:
        return None, api_ran, str(exc)[:200]


@tool
def run_mavent_compliance(
    tool_call_id: Annotated[str, InjectedToolCallId],
    state: Annotated[dict, InjectedState],
    force_refresh: bool = False,
) -> Command:
    """Run Mavent ECS compliance audit and surface per-category results (§15 #3–#4)."""
    loan_id = state.get("loan_id")
    if not loan_id:
        return Command(update={"messages": [ToolMessage(
            content=json.dumps({"error": "No loan_id in state. Run data_gathering first."}),
            tool_call_id=tool_call_id,
        )]})

    if state.get("force_refresh") is True:
        force_refresh = True

    logger.info(
        "[RUN_MAVENT_COMPLIANCE] Starting for loan %s (force_refresh=%s)",
        str(loan_id)[:8], force_refresh,
    )

    flags: List[dict] = []
    report, api_ran, api_err = _fetch_report(loan_id, state, force_refresh)

    if api_err:
        fail_flag = {
            "severity": "error",
            "title": "Mavent Report Generation Failed",
            "details": (
                "Could not retrieve or generate a Mavent compliance report via the "
                f"Encompass ECS API ({api_err})."
            ),
            "suggestion": (
                "Open Encompass > Tools > Compliance Review > Preview to generate "
                "the Mavent report, then rerun this step."
            ),
            "remedy": "Escalate",
            "action": "escalation_required",
            "check_id": "mavent_report_generation_failed",
        }
        flags = _stamp_flags([fail_flag])
        result = {
            "success": False,
            "status": "error",
            "tool": "run_mavent_compliance",
            "api_ran": api_ran,
            "error": api_err,
            "fail_count": 1,
            "warning_count": 0,
            "info_count": 0,
        }
        return Command(update={
            "flags": flags,
            "messages": [ToolMessage(content=json.dumps(result), tool_call_id=tool_call_id)],
        })

    if not report:
        fail_flag = {
            "severity": "error",
            "title": "Mavent Report Generation Failed",
            "details": "The Encompass ECS API returned an empty response.",
            "suggestion": (
                "Open Encompass > Tools > Compliance Review > Preview to generate "
                "the Mavent report, then rerun this step."
            ),
            "remedy": "Escalate",
            "action": "escalation_required",
            "check_id": "mavent_report_generation_failed",
        }
        flags = _stamp_flags([fail_flag])
        result = {
            "success": False,
            "status": "error",
            "tool": "run_mavent_compliance",
            "api_ran": api_ran,
            "fail_count": 1,
            "warning_count": 0,
            "info_count": 0,
        }
        return Command(update={
            "flags": flags,
            "messages": [ToolMessage(content=json.dumps(result), tool_call_id=tool_call_id)],
        })

    report_status = (
        report.get("EcsReportStatus") or report.get("ecsReportStatus")
        or report.get("ReportStatus") or report.get("reportStatus")
        or "Unknown"
    )
    ordered_date = (
        report.get("OrderedDateTime") or report.get("orderedDateTime")
        or report.get("createdDate") or ""
    )

    reviewer_statuses: list = (
        report.get("BaseReviewerStatuses") or report.get("baseReviewerStatuses") or []
    )
    compliance_messages: list = (
        report.get("ComplianceMessages") or report.get("complianceMessages") or []
    )

    category_results: Dict[str, dict] = {}
    for rs in reviewer_statuses:
        name = rs.get("Name") or rs.get("name") or ""
        status_val = (rs.get("Status") or rs.get("status") or "UNKNOWN").strip()
        applicable = rs.get("Applicable", rs.get("applicable", True))
        included = rs.get("Included", rs.get("included", True))
        if name:
            category_results[name] = {
                "status": status_val,
                "applicable": applicable,
                "included": included,
            }

    messages_by_category = _group_messages_by_category(compliance_messages, category_results)

    for cat_name, cat_data in category_results.items():
        cat_status = cat_data["status"]
        applicable = cat_data["applicable"]
        included = cat_data["included"]

        if not applicable or not included:
            continue
        if cat_status.upper() in ("NOT APPLICABLE", "N/A", "NOTAPPLICABLE"):
            continue

        flag = _status_to_flag(cat_name, cat_status)
        related = messages_by_category.get(cat_name) or []

        if cat_status.upper() in _NON_PASS and related:
            shown = related[:_SUMMARY_BULLET_LIMIT]
            bullets = "\n".join(
                f"• {(m.get('message') or '')[:_SUMMARY_CHAR_LIMIT]}"
                for m in shown
            )
            total = len(related)
            shown_count = len(shown)
            flag["details"] = (
                f"Mavent flagged {cat_name} ({cat_status}). "
                f"{shown_count} of {total} issue{'s' if total != 1 else ''} shown:\n"
                + bullets
                + (f"\n(+{total - shown_count} more — see Mavent panel)" if total > shown_count else "")
            )
            flag["suggestion"] = (
                f"Resolve {cat_name} items in Encompass Compliance Review, then rerun Mavent."
            )
            flag["evidence"] = {
                "category": cat_name,
                "report_status": report_status,
                "message_count_total": total,
                "messages_shown": shown_count,
                "messages": related,
            }

        flags.append(flag)

    fail_cats = [
        n for n, d in category_results.items()
        if d["status"].upper() in _NON_PASS
        and d.get("applicable", True)
        and d.get("included", True)
    ]

    if str(report_status).upper() in ("PASS", "PASSED"):
        flags.append({
            "severity": "info",
            "title": f"Mavent Overall Status: {report_status}",
            "details": "Mavent compliance check passed.",
            "action": "info",
            "check_id": "mavent_overall_pass",
        })
    else:
        sev = "error" if str(report_status).upper() in ("FAIL", "FAILED") else "warning"
        flags.append({
            "severity": sev,
            "title": f"Mavent Overall Status: {report_status}",
            "details": (
                f"ECS report status is '{report_status}'. "
                + (
                    f"{len(fail_cats)} categor{'y' if len(fail_cats) == 1 else 'ies'} "
                    f"require attention: {', '.join(fail_cats[:6])}."
                    if fail_cats else "Review individual category flags."
                )
            ),
            "suggestion": "Resolve flagged categories in Encompass, then rerun Mavent.",
            "remedy": "Manual",
            "action": "processor_attention_required",
            "check_id": "mavent_overall_non_pass",
        })

    fail_count = sum(1 for f in flags if f.get("severity") == "error")
    warning_count = sum(1 for f in flags if f.get("severity") == "warning")
    info_count = sum(1 for f in flags if f.get("severity") == "info")
    overall_passed = fail_count == 0 and str(report_status).upper() not in ("FAIL", "FAILED")

    result = {
        "success": True,
        "status": "fail" if fail_count > 0 else ("warning" if warning_count > 0 else "pass"),
        "tool": "run_mavent_compliance",
        "loan_guid": loan_id,
        "data_source": "ecs_api",
        "report_status": report_status,
        "ordered_date": ordered_date,
        "api_ran": api_ran,
        "category_count": len(category_results),
        "passed": overall_passed,
        "fail_count": fail_count,
        "warning_count": warning_count,
        "info_count": info_count,
        "message": (
            f"Mavent compliance ({report_status}): "
            f"{fail_count} fails, {warning_count} warnings, {info_count} info — "
            f"{len(category_results)} categories evaluated"
        ),
    }

    logger.info("[RUN_MAVENT_COMPLIANCE] %s", result["message"])

    mavent_results = {
        "run_date": ordered_date,
        "report_status": report_status,
        "categories": {n: d["status"] for n, d in category_results.items()},
        "compliance_messages_by_category": messages_by_category,
        "api_ran": api_ran,
    }

    return Command(update={
        "mavent_verification": result,
        "mavent_results": mavent_results,
        "flags": _stamp_flags(flags),
        "messages": [ToolMessage(content=json.dumps(result), tool_call_id=tool_call_id)],
    })
