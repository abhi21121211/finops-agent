"""Persisting reconciliation results and refreshing payment matches tenant-wide.

Payment allocation depends on every invoice and every transaction of the tenant (one
transfer can pay several invoices), so after a bank statement upload or any change in
which invoices are live, all payment matches are recomputed together."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.lookups import load_payment_data
from app.agents.nodes.reconcile import combine
from app.db.models import Match, MatchStatus
from app.reconcile.payments import allocate_payments
from app.schemas.reconcile import MatchResult, POMatch


async def save_match(
    session: AsyncSession, tenant_id: uuid.UUID, invoice_id: uuid.UUID, result: dict
) -> None:
    match = MatchResult.model_validate(result)
    row = await session.scalar(select(Match).where(Match.invoice_id == invoice_id))
    if row is None:
        row = Match(tenant_id=tenant_id, invoice_id=invoice_id)
        session.add(row)
    row.po_id = uuid.UUID(match.po.po_id) if match.po.po_id else None
    row.bank_transaction_ids = [a.transaction_id for a in match.payment.allocations]
    row.status = MatchStatus(match.status)
    row.explanation = match.explanation
    row.details = match.model_dump(mode="json")


async def refresh_payments(session: AsyncSession, tenant_id: uuid.UUID) -> int:
    """Recompute payment matches for every live invoice; returns how many changed."""
    invoices, txns = await load_payment_data(session, str(tenant_id))
    payments = allocate_payments(invoices, txns)
    rows = await session.scalars(select(Match).where(Match.tenant_id == tenant_id))
    changed = 0
    for row in rows:
        payment = payments.get(str(row.invoice_id))
        if payment is None or not row.details:
            continue  # rejected/failed invoices keep their last result
        updated = combine(POMatch.model_validate(row.details["po"]), payment)
        new = updated.model_dump(mode="json")
        if new != row.details:
            row.details = new
            row.status = MatchStatus(updated.status)
            row.explanation = updated.explanation
            row.bank_transaction_ids = [a.transaction_id for a in payment.allocations]
            changed += 1
    await session.commit()
    return changed
