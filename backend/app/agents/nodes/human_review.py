"""human_review: pause the run until a reviewer acts. The pause is a LangGraph interrupt
checkpointed in Postgres, so it survives restarts and can wait for days."""

from typing import Any

from langgraph.types import interrupt

from app.agents.state import InvoiceState, event
from app.schemas.extraction import ExtractedInvoice


def apply_field_edits(extraction: dict[str, Any], edits: dict[str, Any]) -> dict[str, Any]:
    """Merge reviewer edits and re-validate the shape. Raises ValueError on bad input."""
    unknown = set(edits) - set(ExtractedInvoice.model_fields)
    if unknown:
        raise ValueError(f"Unknown field(s): {', '.join(sorted(unknown))}")
    merged = {**extraction, **edits}
    return ExtractedInvoice.model_validate(merged).model_dump(mode="json")


async def human_review(state: InvoiceState) -> InvoiceState:
    decision: dict[str, Any] = interrupt(
        {"invoice_id": state["invoice_id"], "reasons": state.get("route_reasons", [])}
    )
    # Code after interrupt() runs only on resume, with the reviewer's decision.
    action = decision["action"]
    update: InvoiceState = {"human_decision": decision}
    if action == "edit":
        edits = decision["field_edits"]
        update["extraction"] = apply_field_edits(state["extraction"], edits)
        # A human-entered value is certain.
        update["field_confidence"] = {
            **(state.get("field_confidence") or {}),
            **dict.fromkeys(edits, 1.0),
        }
    comment = f" ({decision['comment']})" if decision.get("comment") else ""
    edited = (
        f", {len(decision.get('field_edits') or {})} field(s) edited" if action == "edit" else ""
    )
    update["events"] = [event("human_review", "completed", f"{action}{edited}{comment}")]
    return update
