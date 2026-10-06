"""post: settle the outcome. Approval happens here, in code, after the routing rules or a
human decision; no LLM output can reach this node's decision directly."""

from app.agents.state import InvoiceState, event


async def post(state: InvoiceState) -> InvoiceState:
    decision = state.get("human_decision")
    if decision is None:
        if state.get("route") != "auto_approve":
            raise RuntimeError("post reached without approval")  # graph wiring bug
        outcome, by = "approved", "auto-approved by rules"
    elif decision["action"] == "reject":
        outcome, by = "rejected", f"rejected by {decision.get('user_email') or 'reviewer'}"
    else:
        outcome, by = "approved", f"approved by {decision.get('user_email') or 'reviewer'}"
    return {"outcome": outcome, "events": [event("post", "completed", by)]}
