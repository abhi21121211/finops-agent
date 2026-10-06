"""The invoice StateGraph (spec §4). One run per invoice, thread_id = invoice_id.

    intake → extract → validate ⇄ extract (fixable issues, < 3 attempts)
                              → route → post                (auto_approve)
                                      → human_review → post (approve / edit / reject)

Reconcile (M3) and detect_anomalies (M5) slot in between validate and route.
"""

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from app.agents.nodes.extract import extract
from app.agents.nodes.human_review import human_review
from app.agents.nodes.intake import intake
from app.agents.nodes.post import post
from app.agents.nodes.route import after_route, route
from app.agents.nodes.validate import after_validate, validate
from app.agents.state import InvoiceState


def after_extract(state: InvoiceState) -> str:
    return "validate" if state.get("extraction") else END


def build_graph(checkpointer: BaseCheckpointSaver | None = None):
    g = StateGraph(InvoiceState)
    g.add_node("intake", intake)
    g.add_node("extract", extract)
    g.add_node("validate", validate)
    g.add_node("route", route)
    g.add_node("human_review", human_review)
    g.add_node("post", post)

    g.add_edge(START, "intake")
    g.add_edge("intake", "extract")
    g.add_conditional_edges("extract", after_extract, ["validate", END])
    g.add_conditional_edges("validate", after_validate, ["extract", "route"])
    g.add_conditional_edges("route", after_route, ["post", "human_review"])
    g.add_edge("human_review", "post")
    g.add_edge("post", END)
    return g.compile(checkpointer=checkpointer)
