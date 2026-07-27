"""analyze_uw_conditions — dashboard-facing post-UW condition triage graph.

Single-node, stateless LangGraph endpoint (same family as write_los_fields):
the dashboard Post-UW page calls it via /runs/wait on every visit. Encompass
is the state store — no thread persistence, re-running re-triages.

For each underwriting condition it assigns a *lane*:

    request_borrower  — needs a document/letter from the borrower; carries the
                        matching callout template family id for the comms
                        graph (processor_condition_request)
    fulfill_from_file — evidence usually already lands in the eFolder; the UI
                        cross-references its bucket list and offers
                        attach + Fulfilled (via doc-mgmt update_uw_condition)
    auto_fulfill      — rule fired on loan data (today: the EMD sourcing
                        waiver — EMD < 1% purchase price AND < 50% monthly
                        income); proposes Fulfilled + waiver comment
    uw_owned          — informative / UW-generated (MI, AUS type, QA…);
                        display only
    done              — already Fulfilled / Cleared / Waived
    needs_review      — no rule matched and LLM fallback declined; processor
                        decides

Input  : {"loan_number": "...", "env": "Prod"|"Test"}
Output : conditions[], suggestions[], email_action (comms_actions-shaped item
         for the borrower items-needed email), counts, error?

Callout family ids MUST stay in sync with
processor-assistant-communications/config/callouts.yaml.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Annotated, NotRequired, Optional

from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict

logger = logging.getLogger(__name__)


def _last_value(existing, new):
    return new if new is not None else existing


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ═══════════════════════════════════════════════════════════════════════
# Rule table — mirrors docs/post_uw_conditions_design.md playbook
# ═══════════════════════════════════════════════════════════════════════
# Each rule: pattern matched (case-insensitive) against "title\ndescription".
# First match wins; order matters (specific before generic).

_DONE_STATUSES = {"fulfilled", "cleared", "waived", "expired"}

_RULES: list[dict] = [
    # ── UW-owned / informative ─────────────────────────────────────────
    {"pattern": r"aus type|app mi\b|mi cert|app program|transmittal summary|fraud guard",
     "lane": "uw_owned",
     "reason": "UW-generated/informative — no processor action; UW updates it at final approval."},
    {"pattern": r"^qa \d",
     "lane": "uw_owned",
     "reason": "QA-owned checkpoint (QC / lock desk). Notify emails are a later phase."},

    # ── auto-fulfill candidates (rule evaluated on loan data) ──────────
    {"pattern": r"\bemd\b|earnest money",
     "lane": "auto_fulfill",
     "rule": "emd_waiver",
     "family": "emd_clearing",
     "reason": "EMD sourcing can be waived when EMD < 1% of purchase price and < 50% of monthly income."},

    # ── fulfill from file (evidence lands in eFolder) ──────────────────
    {"pattern": r"title prelim|prelim.*title",
     "lane": "fulfill_from_file",
     "buckets": ["Preliminary Title Report", "Title Report"],
     "reason": "Title company delivers the prelim — attach from eFolder and mark Fulfilled when present."},
    {"pattern": r"appraisal",
     "lane": "fulfill_from_file",
     "buckets": ["Appraisal"],
     "reason": "Appraisal PDF arrives via AMC — attach from eFolder and mark Fulfilled when present."},
    {"pattern": r"ocrolus|income work(sheet|book)",
     "lane": "fulfill_from_file",
     "buckets": ["Income Calculation Worksheets"],
     "reason": "Ocrolus worksheet output — attach from the Income Calculation Worksheets bucket (automation lands with the Ocrolus integration)."},
    {"pattern": r"flood cert",
     "lane": "fulfill_from_file",
     "buckets": ["Flood Certificate"],
     "reason": "Flood cert correction — re-pull/attach the corrected cert from eFolder."},

    # ── request from borrower (with callout family) ────────────────────
    {"pattern": r"undisclosed debt",
     "lane": "request_borrower", "family": "undisclosed_debt",
     "reason": "Bank-statement activity needs borrower explanation/LOE (transaction scan automation is a later phase)."},
    {"pattern": r"bank statement|assets?\s*-\s*stmts|statement required",
     "lane": "request_borrower", "family": "updated_bank_statement",
     "reason": "Missing statement month/pages — request from borrower per account."},
    {"pattern": r"large deposit",
     "lane": "request_borrower", "family": "large_deposit",
     "reason": "Deposit sourcing documentation needed from borrower."},
    {"pattern": r"gift (letter|funds)|\bgift\b",
     "lane": "request_borrower", "family": "gift_letter",
     "reason": "Gift documentation needed from borrower/donor."},
    {"pattern": r"counseling|homebuyer|home buyer|education course",
     "lane": "request_borrower", "family": "homebuyers_education",
     "reason": "Borrower completes the course and returns the certificate."},
    {"pattern": r"remote|work from home",
     "lane": "request_borrower", "family": "remote_work_letter",
     "reason": "Employer letter confirming remote authorization (commute-map evidence is a later phase)."},
    {"pattern": r"employment gap|gap in employment",
     "lane": "request_borrower", "family": "employment_gap_loe",
     "reason": "Borrower LOE covering the employment gap."},
    {"pattern": r"inquir",
     "lane": "request_borrower", "family": "credit_inquiry",
     "reason": "Borrower LOE for recent credit inquiries."},
    {"pattern": r"insurance|\bhoi\b|\brcc\b|replacement cost",
     "lane": "request_borrower", "family": "homeowners_insurance",
     "buckets": ["Hazard Insurance", "Homeowners Insurance"],
     "reason": "Policy/declarations (with RCC where required) from the borrower's insurance agent."},
    {"pattern": r"\bead\b|residency|green card|visa",
     "lane": "request_borrower", "family": "id_document",
     "reason": "Copy of the identity/residency document from the borrower."},
    {"pattern": r"paystub|pay stub|w-?2|pay documents",
     "lane": "request_borrower", "family": "pay_documents",
     "reason": "Current income documents from the borrower."},
    {"pattern": r"\bhoa\b",
     "lane": "request_borrower", "family": "hoa_statement",
     "reason": "HOA statement/cert needed."},
    {"pattern": r"divorce",
     "lane": "request_borrower", "family": "divorce_decree",
     "reason": "Recorded divorce decree / settlement pages needed."},

    # ── needs processor judgment ───────────────────────────────────────
    {"pattern": r"credit refresh|\budx\b|\budn\b|soft pull",
     "lane": "needs_review",
     "reason": "Timing-bound: order the UDX/refresh within 10 days of the NOTE date — track, don't action yet."},
    {"pattern": r"1003|encompass to be updated|missing data",
     "lane": "needs_review",
     "reason": "LOS data correction — fix in Encompass, then mark Fulfilled."},
    {"pattern": r"payoff",
     "lane": "needs_review",
     "reason": "Payoff handling depends on approval terms — processor decides."},
]

_LLM_LANES = ["request_borrower", "fulfill_from_file", "uw_owned", "needs_review"]

# Callout families the LLM fallback may pick from (subset that maps to
# borrower requests; keep in sync with callouts.yaml family ids).
_LLM_FAMILIES = [
    "updated_bank_statement", "bank_transaction_history", "emd_clearing",
    "gift_letter", "gift_funds_documentation", "undisclosed_debt",
    "large_deposit", "credit_inquiry", "address_explanation",
    "aka_explanation", "employment_gap_loe", "remote_work_letter",
    "homeowners_insurance", "homebuyers_education", "id_document",
    "pay_documents", "retirement_withdrawal", "hoa_statement",
    "esignature_reminder", "divorce_decree",
]

# LOS fields for the EMD waiver rule + email payload.
_FIELD_PURCHASE_PRICE = "136"
_FIELD_MONTHLY_INCOME = "736"   # total monthly income (all borrowers)
_FIELD_EMD_DEPOSIT = "142"      # details of transaction — deposit / EMD
_FIELD_BORROWER_FIRST = "4000"
_FIELD_BORROWER_LAST = "4002"
_FIELD_BORROWER_EMAIL = "1240"


def _to_float(value) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(str(value).replace("$", "").replace(",", "").strip())
    except ValueError:
        return None


def _match_rule(condition: dict) -> Optional[dict]:
    text = f"{condition.get('title') or ''}\n{condition.get('description') or ''}".lower()
    for rule in _RULES:
        if re.search(rule["pattern"], text):
            return rule
    return None


def _llm_fallback(condition: dict) -> Optional[dict]:
    """Classify an unmatched condition via the repo's structured LLM helper.

    Returns a rule-shaped dict or None (→ needs_review) on any failure —
    the graph must stay usable without an LLM key.
    """
    try:
        from shared.llm_call import llm_structured_call

        result = llm_structured_call(
            prompt=(
                "Classify this mortgage underwriting condition for a loan "
                "processor working post-approval conditions.\n\n"
                f"Title: {condition.get('title')}\n"
                f"Category: {condition.get('category')}\n"
                f"Description: {condition.get('description')}\n\n"
                "Lanes: request_borrower (borrower must send a document or "
                "letter), fulfill_from_file (evidence arrives from a third "
                "party into the eFolder), uw_owned (informative/UW-internal, "
                "no processor action), needs_review (processor judgment).\n"
                "If request_borrower, also pick the closest callout family."
            ),
            schema={
                "lane": {"type": "string", "enum": _LLM_LANES,
                         "description": "Action lane for the processor"},
                "family": {"type": "string", "enum": _LLM_FAMILIES + ["none"],
                           "description": "Callout template family (request_borrower only)"},
                "reason": {"type": "string",
                           "description": "One-sentence rationale shown to the processor"},
            },
            tool_name="classify_condition",
            tool_description="Classify an underwriting condition into a processor action lane",
        )
        if not result or result.get("lane") not in _LLM_LANES:
            return None
        rule: dict = {"lane": result["lane"],
                      "reason": result.get("reason") or "LLM-classified.",
                      "llm": True}
        family = result.get("family")
        if rule["lane"] == "request_borrower" and family and family != "none":
            rule["family"] = family
        return rule
    except Exception as exc:  # noqa: BLE001 — degrade to needs_review
        logger.warning(f"[ANALYZE_UW_CONDITIONS] LLM fallback failed: {exc}")
        return None


def _emd_waiver(fields: dict) -> dict:
    """Evaluate the EMD sourcing-waiver rule. Returns {fires, detail, missing}."""
    price = _to_float(fields.get(_FIELD_PURCHASE_PRICE))
    income = _to_float(fields.get(_FIELD_MONTHLY_INCOME))
    emd = _to_float(fields.get(_FIELD_EMD_DEPOSIT))
    missing = [name for name, v in
               [("purchase_price", price), ("monthly_income", income), ("emd_amount", emd)]
               if v in (None, 0)]
    if missing:
        return {"fires": False, "missing": missing,
                "detail": f"Cannot evaluate waiver — missing loan data: {', '.join(missing)}"}
    fires = emd < 0.01 * price and emd < 0.5 * income
    detail = (
        f"EMD ${emd:,.2f} vs 1% of purchase price ${0.01 * price:,.2f} "
        f"and 50% of monthly income ${0.5 * income:,.2f}"
    )
    return {"fires": fires, "missing": [], "detail": detail,
            "emd": emd, "price": price, "income": income}


# ═══════════════════════════════════════════════════════════════════════
# Graph
# ═══════════════════════════════════════════════════════════════════════

class AnalyzeConditionsState(TypedDict):
    """Input: loan_number (or GUID), env ("Prod"|"Test").
    Optional: use_llm_fallback (default True), prior_to (default ["Docs"]).
    Output: loan_id, conditions, suggestions, email_action, counts, error.
    """
    loan_number: str
    env: str
    use_llm_fallback: NotRequired[bool]
    prior_to: NotRequired[list]
    loan_id: Annotated[NotRequired[str], _last_value]
    conditions: Annotated[NotRequired[list], _last_value]
    suggestions: Annotated[NotRequired[list], _last_value]
    email_action: Annotated[NotRequired[dict], _last_value]
    counts: Annotated[NotRequired[dict], _last_value]
    error: Annotated[NotRequired[str], _last_value]


def _analyze_node(state: AnalyzeConditionsState) -> dict:
    from write_graphs import _resolve_loan  # same resolution path as write graphs
    from encompass_client import get_underwriting_conditions
    from shared.encompass_io import read_fields

    env = state.get("env", "Prod")
    prior_to = [p.lower() for p in (state.get("prior_to") or ["Docs"])]
    use_llm = state.get("use_llm_fallback", True)

    loan_id, err = _resolve_loan(state.get("loan_number", ""), env)
    if err:
        return {"error": err, "conditions": [], "suggestions": [], "counts": {}}

    io_state = {"env": env}
    try:
        raw = get_underwriting_conditions(loan_id, state=io_state) or []
    except Exception as exc:  # noqa: BLE001
        return {"loan_id": loan_id, "error": f"get_underwriting_conditions: {exc}",
                "conditions": [], "suggestions": [], "counts": {}}

    fields = read_fields(
        loan_id,
        [_FIELD_PURCHASE_PRICE, _FIELD_MONTHLY_INCOME, _FIELD_EMD_DEPOSIT,
         _FIELD_BORROWER_FIRST, _FIELD_BORROWER_LAST, _FIELD_BORROWER_EMAIL],
        context="analyze_uw_conditions",
        state=io_state,
    )
    borrower_name = " ".join(
        p for p in [fields.get(_FIELD_BORROWER_FIRST), fields.get(_FIELD_BORROWER_LAST)] if p
    ).strip()

    conditions, suggestions = [], []
    for cond in raw:
        # v1 list uses statusDescription in some tenants; prefer `status`.
        status = (cond.get("status") or cond.get("statusDescription") or "").strip()
        slim = {
            "id": cond.get("id"),
            "title": cond.get("title"),
            "description": cond.get("description"),
            "category": cond.get("category"),
            "prior_to": cond.get("priorTo"),
            "status": status,
            "date_added": cond.get("addedDate") or cond.get("createdDate"),
            "source": cond.get("source"),
        }
        conditions.append(slim)

        if (slim["prior_to"] or "").lower() not in prior_to:
            continue

        if status.lower() in _DONE_STATUSES:
            suggestions.append({**_base_suggestion(slim), "lane": "done",
                                "reason": f"Already {status}."})
            continue

        rule = _match_rule(slim)
        if rule is None and use_llm:
            rule = _llm_fallback(slim)
        if rule is None:
            suggestions.append({**_base_suggestion(slim), "lane": "needs_review",
                                "reason": "No rule matched — processor to triage."})
            continue

        suggestion = {**_base_suggestion(slim),
                      "lane": rule["lane"],
                      "reason": rule["reason"],
                      "llm_classified": bool(rule.get("llm"))}
        if rule.get("family"):
            suggestion["callout_family"] = rule["family"]
        if rule.get("buckets"):
            suggestion["evidence_bucket_titles"] = rule["buckets"]
            suggestion["proposed_update"] = {
                "status": "Fulfilled",
                "attach_document_titles": rule["buckets"][:1],
            }

        if rule.get("rule") == "emd_waiver":
            waiver = _emd_waiver(fields)
            suggestion["rule_detail"] = waiver["detail"]
            if waiver["fires"]:
                suggestion["proposed_update"] = {
                    "status": "Fulfilled",
                    "comment": (
                        f"EMD of ${waiver['emd']:,.2f} is less than 1% of the "
                        f"purchase price (${waiver['price']:,.2f}) and less than "
                        f"50% of the borrower's monthly income "
                        f"(${waiver['income']:,.2f}). Requesting sourcing be "
                        f"waived per guidelines. Receipt uploaded to the eFolder."
                    ),
                }
            else:
                # Rule didn't fire (or data missing) — fall back to asking the
                # borrower to source the EMD. Pre-fill the slots we know.
                suggestion["lane"] = "request_borrower"
                suggestion["needs"] = waiver.get("missing") or []
                if waiver.get("emd"):
                    suggestion["slots"] = {"emd_amount": f"${waiver['emd']:,.2f}"}
        suggestions.append(suggestion)

    email_conditions = [s for s in suggestions if s["lane"] == "request_borrower"]
    email_action = None
    if email_conditions:
        email_action = {
            "id": "send_condition_request_email",
            "component": "communications",
            "action_type": "send_condition_request_email",
            "title": "Email borrower — items needed for conditions",
            "description": (
                f"{len(email_conditions)} condition(s) need documents from the "
                f"borrower. Draft the items-needed email for review."
            ),
            "severity": "action",
            "status": "actionable",
            "blockers": [],
            "needs_input": [] if fields.get(_FIELD_BORROWER_EMAIL) else ["to"],
            "trigger": {
                "agent": "processor_communications",
                "graph_id": "processor_condition_request",
                "resume_contract": "email",
                "payload": {
                    "loan_number": state.get("loan_number", ""),
                    "loan_id": loan_id,
                    "env": env,
                    "action": "send_condition_request_email",
                    "inputs": {
                        "borrower_name": borrower_name,
                        "borrower_email": fields.get(_FIELD_BORROWER_EMAIL),
                        "conditions": [
                            {
                                "condition_id": s["condition_id"],
                                "title": s["title"],
                                "callout_family": s.get("callout_family"),
                                "description": s.get("description"),
                                "slots": s.get("slots") or {},
                            }
                            for s in email_conditions
                        ],
                    },
                },
            },
            "created_at": _now(),
        }

    lanes = [s["lane"] for s in suggestions]
    counts = {lane: lanes.count(lane) for lane in sorted(set(lanes))}
    counts["total_conditions"] = len(conditions)
    counts["triaged"] = len(suggestions)

    logger.info(
        f"[ANALYZE_UW_CONDITIONS] loan {loan_id[:8]}: {len(conditions)} conditions, "
        f"lanes={counts}"
    )
    return {
        "loan_id": loan_id,
        "conditions": conditions,
        "suggestions": suggestions,
        "email_action": email_action,
        "counts": counts,
    }


def _base_suggestion(slim: dict) -> dict:
    return {
        "condition_id": slim["id"],
        "title": slim["title"],
        "description": slim["description"],
        "category": slim["category"],
        "status": slim["status"],
        "prior_to": slim["prior_to"],
    }


_builder = StateGraph(AnalyzeConditionsState)
_builder.add_node("analyze", _analyze_node)
_builder.add_edge(START, "analyze")
_builder.add_edge("analyze", END)
graph = _builder.compile()
graph.name = "analyze_uw_conditions"
