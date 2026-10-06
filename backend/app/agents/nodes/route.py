"""route: deterministic routing (spec §4 "Routing rules"). The LLM never decides approval.

Every rule that fires is recorded as a reason so the reviewer sees all of them; the route
is the strictest outcome. Rules for later milestones are inert until their data exists:
anomaly detection (M5) sets anomalies, and
vendor_query (M5) needs the email tool, so those cases go to a human for now.
"""

from decimal import Decimal

from langchain_core.runnables import RunnableConfig

from app.agents.lookups import get_lookups
from app.agents.state import InvoiceState, event
from app.schemas.extraction import ExtractedInvoice

# Fields an invoice cannot be paid without (beyond what the schema already requires).
MANDATORY = ("vendor_gstin",)


async def route(state: InvoiceState, config: RunnableConfig) -> InvoiceState:
    settings = await get_lookups(config).tenant_settings(state["tenant_id"])
    inv = ExtractedInvoice.model_validate(state["extraction"])
    reasons: list[str] = []

    high = [a for a in state.get("anomalies", []) if a.get("severity") == "high"]
    if high:
        reasons.append(f"{len(high)} high-severity anomaly(ies)")

    missing = [f for f in MANDATORY if getattr(inv, f) in (None, "")]
    if missing:
        reasons.append(f"missing mandatory field(s): {', '.join(missing)}")

    errors = [i for i in state.get("validation_issues", []) if i["severity"] == "error"]
    if errors:
        reasons.append(f"{len(errors)} unresolved validation error(s)")

    conf = state.get("field_confidence") or {}
    low = sorted((v, k) for k, v in conf.items() if v < settings.confidence_threshold)
    if low:
        fields = ", ".join(f"{k} ({v:.0%})" for v, k in low[:4])
        reasons.append(f"low confidence below {settings.confidence_threshold:.0%}: {fields}")

    # Only PO problems block approval: unpaid/partial describe payment, which normally
    # comes after approval (ADR 0002).
    match = state.get("match_result")
    if match is not None and match["status"] in ("no_po", "mismatch"):
        label = "no purchase order" if match["status"] == "no_po" else "PO mismatch"
        reasons.append(f"{label}: {match['po']['explanation']}")

    if inv.total > settings.auto_approve_limit:
        limit = Decimal(settings.auto_approve_limit)
        reasons.append(f"total ₹{inv.total:,.2f} above auto-approve limit ₹{limit:,.2f}")

    decision = "human_review" if reasons else "auto_approve"
    detail = "all rules passed" if not reasons else "; ".join(reasons)
    return {
        "route": decision,
        "route_reasons": reasons,
        "events": [event("route", "completed", f"{decision}: {detail}")],
    }


def after_route(state: InvoiceState) -> str:
    return "post" if state["route"] == "auto_approve" else "human_review"
