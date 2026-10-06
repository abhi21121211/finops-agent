"""human_review: pause the run until a reviewer acts. The pause is a LangGraph interrupt
checkpointed in Postgres, so it survives restarts and can wait for days."""

from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.types import interrupt

from app.agents.nodes.validate import check_extraction
from app.agents.state import InvoiceState, event
from app.schemas.extraction import ExtractedInvoice


def apply_field_edits(extraction: dict[str, Any], edits: dict[str, Any]) -> dict[str, Any]:
    """Merge reviewer edits and re-validate the shape. Raises ValueError on bad input."""
    unknown = set(edits) - set(ExtractedInvoice.model_fields)
    if unknown:
        raise ValueError(f"Unknown field(s): {', '.join(sorted(unknown))}")
    merged = {**extraction, **edits}
    return ExtractedInvoice.model_validate(merged).model_dump(mode="json")


async def human_review(state: InvoiceState, config: RunnableConfig) -> InvoiceState:
    decision: dict[str, Any] = interrupt(
        {"invoice_id": state["invoice_id"], "reasons": state.get("route_reasons", [])}
    )
    # Code after interrupt() runs only on resume, with the reviewer's decision.
    action = decision["action"]
    update: InvoiceState = {"human_decision": decision}
    detail = action
    if action == "edit":
        edits = decision["field_edits"]
        update["extraction"] = apply_field_edits(state["extraction"], edits)
        # A human-entered value is certain.
        confidence = dict(state.get("field_confidence") or {})
        update["field_confidence"] = {**confidence, **dict.fromkeys(edits, 1.0)}
        # Re-check the corrected values so the record shows what was approved. The reviewer
        # has the final say, so remaining issues are recorded, not enforced.
        issues = await check_extraction(state, config, update["extraction"])
        update["validation_issues"] = [i.model_dump(mode="json") for i in issues]
        detail += f", {len(edits)} field(s) edited, {len(issues)} issue(s) remain"
    if decision.get("comment"):
        detail += f" ({decision['comment']})"
    update["events"] = [event("human_review", "completed", detail)]
    return update
