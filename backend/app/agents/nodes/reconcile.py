"""reconcile: three-way match of invoice, purchase order and bank statement.

Matching is code (app/reconcile); the LLM is consulted only to decide whether two
differently worded line descriptions are the same item."""

import json

from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel

from app.agents.lookups import get_lookups, open_invoice
from app.agents.nodes.extract import _router
from app.agents.prompts import load_prompt
from app.agents.state import InvoiceState, event
from app.llm.router import Tier, text_part
from app.reconcile.payments import allocate_payments
from app.reconcile.po_match import match_po
from app.schemas.extraction import ExtractedInvoice
from app.schemas.reconcile import MatchResult, PaymentMatch, POMatch

JUDGE_PROMPT = "po_line_judge_v1"


class JudgeOutput(BaseModel):
    same: list[bool]


def combine(po: POMatch, payment: PaymentMatch) -> MatchResult:
    """Overall status per spec: PO problems first, then payment state."""
    if po.status in ("no_po", "mismatch"):
        status = po.status
    else:
        status = {"paid": "matched", "partial": "partial", "unpaid": "unpaid"}[payment.status]
    return MatchResult(
        status=status, po=po, payment=payment, explanation=f"{po.explanation} {payment.explanation}"
    )


async def reconcile(state: InvoiceState, config: RunnableConfig) -> InvoiceState:
    lookups = get_lookups(config)
    tenant_id, invoice_id = state["tenant_id"], state["invoice_id"]
    inv = ExtractedInvoice.model_validate(state["extraction"])
    llm_cost = {"cost_usd": 0.0, "list_price_usd": 0.0, "llm_latency_ms": 0}

    async def judge(pairs: list[tuple[str, str]]) -> list[bool]:
        payload = [{"invoice_line": a, "po_line": b} for a, b in pairs]
        result = await _router(config).structured(
            tier=Tier.normal,
            system=load_prompt(JUDGE_PROMPT),
            user_content=[text_part(json.dumps(payload, ensure_ascii=False))],
            schema=JudgeOutput,
        )
        llm_cost["cost_usd"] += float(result.cost_usd)
        llm_cost["list_price_usd"] += float(result.list_price_usd)
        llm_cost["llm_latency_ms"] += result.latency_ms
        verdicts = result.output.same
        return (verdicts + [False] * len(pairs))[: len(pairs)]  # never trust the length

    po = await match_po(inv, await lookups.purchase_orders(tenant_id, invoice_id), judge)

    others, txns = await lookups.payment_data(tenant_id)
    this = open_invoice(invoice_id, state["extraction"])
    everyone = [o for o in others if o.id != invoice_id] + [this]
    payment = allocate_payments(everyone, txns)[invoice_id]

    match = combine(po, payment)
    return {
        "match_result": match.model_dump(mode="json"),
        **llm_cost,
        "events": [event("reconcile", "completed", f"{match.status}: {match.explanation}")],
    }
