"""The invoice StateGraph. M1: intake → extract. Validate, reconcile, route and human
review are added in M2–M3; a Postgres checkpointer arrives with interrupt() in M2."""

from langgraph.graph import END, START, StateGraph

from app.agents.nodes.extract import extract
from app.agents.nodes.intake import intake
from app.agents.state import InvoiceState


def build_graph():
    g = StateGraph(InvoiceState)
    g.add_node("intake", intake)
    g.add_node("extract", extract)
    g.add_edge(START, "intake")
    g.add_edge("intake", "extract")
    g.add_edge("extract", END)
    return g.compile()


invoice_graph = build_graph()
