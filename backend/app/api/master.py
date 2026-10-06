"""Vendors, purchase orders, bank statements and the reconciliation view."""

import uuid
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.agents.lookups import load_purchase_orders
from app.core.auth import CurrentUserDep
from app.core.queue import Queue, get_queue
from app.db.models import BankTransaction, Invoice, Match, POLine, PurchaseOrder, Vendor
from app.db.session import get_session
from app.reconcile.bank_csv import StatementError, parse_statement
from app.schemas.master import (
    BankTxnOut,
    POIn,
    POLineOut,
    POOut,
    ReconciliationRow,
    StatementUploadOut,
    VendorIn,
    VendorOut,
)

router = APIRouter(tags=["master data"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
QueueDep = Annotated[Queue, Depends(get_queue)]
MAX_STATEMENT_MB = 5


@router.get("/vendors", response_model=list[VendorOut])
async def list_vendors(user: CurrentUserDep, session: SessionDep) -> list[Vendor]:
    rows = await session.scalars(
        select(Vendor).where(Vendor.tenant_id == user.tenant_id).order_by(Vendor.name)
    )
    return list(rows)


@router.post("/vendors", response_model=VendorOut, status_code=status.HTTP_201_CREATED)
async def create_vendor(body: VendorIn, user: CurrentUserDep, session: SessionDep) -> Vendor:
    if body.gstin and await session.scalar(
        select(Vendor.id).where(Vendor.tenant_id == user.tenant_id, Vendor.gstin == body.gstin)
    ):
        raise HTTPException(status.HTTP_409_CONFLICT, "A vendor with this GSTIN exists")
    vendor = Vendor(tenant_id=user.tenant_id, **body.model_dump())
    session.add(vendor)
    await session.commit()
    return vendor


async def _po_out(session: AsyncSession, tenant_id: uuid.UUID, po: PurchaseOrder) -> POOut:
    billed = {
        p.po_id: p for p in await load_purchase_orders(session, str(tenant_id), exclude_id=None)
    }.get(str(po.id))
    return POOut(
        id=po.id,
        po_number=po.po_number,
        date=po.date,
        status=po.status.value,
        total=po.total,
        vendor_id=po.vendor_id,
        vendor_name=po.vendor.name,
        lines=[
            POLineOut(
                description=pl.description,
                quantity=pl.quantity,
                unit_price=pl.unit_price,
                billed=billed.lines[n].billed_before if billed else Decimal(0),
            )
            for n, pl in enumerate(po.lines)
        ],
    )


def _po_query(tenant_id: uuid.UUID):
    return (
        select(PurchaseOrder)
        .where(PurchaseOrder.tenant_id == tenant_id)
        .options(selectinload(PurchaseOrder.lines), selectinload(PurchaseOrder.vendor))
    )


@router.get("/purchase-orders", response_model=list[POOut])
async def list_purchase_orders(user: CurrentUserDep, session: SessionDep) -> list[POOut]:
    pos = await session.scalars(_po_query(user.tenant_id).order_by(PurchaseOrder.date.desc()))
    return [await _po_out(session, user.tenant_id, po) for po in pos]


@router.post("/purchase-orders", response_model=POOut, status_code=status.HTTP_201_CREATED)
async def create_purchase_order(body: POIn, user: CurrentUserDep, session: SessionDep) -> POOut:
    vendor = await session.get(Vendor, body.vendor_id)
    if vendor is None or vendor.tenant_id != user.tenant_id:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Unknown vendor")
    if await session.scalar(
        select(PurchaseOrder.id).where(
            PurchaseOrder.tenant_id == user.tenant_id, PurchaseOrder.po_number == body.po_number
        )
    ):
        raise HTTPException(status.HTTP_409_CONFLICT, "PO number already exists")
    lines = [POLine(position=n, **ln.model_dump()) for n, ln in enumerate(body.lines)]
    po = PurchaseOrder(
        tenant_id=user.tenant_id,
        vendor_id=vendor.id,
        po_number=body.po_number,
        date=body.date,
        total=sum((ln.quantity * ln.unit_price for ln in lines), Decimal(0)),
        lines=lines,
    )
    session.add(po)
    await session.commit()
    po = await session.scalar(
        _po_query(user.tenant_id)
        .where(PurchaseOrder.id == po.id)
        .execution_options(populate_existing=True)
    )
    return await _po_out(session, user.tenant_id, po)


@router.post("/bank-statements", response_model=StatementUploadOut)
async def upload_bank_statement(
    user: CurrentUserDep,
    session: SessionDep,
    queue: QueueDep,
    file: Annotated[UploadFile, File(description="CSV export from the bank")],
) -> StatementUploadOut:
    data = await file.read(MAX_STATEMENT_MB * 1024 * 1024 + 1)
    if len(data) > MAX_STATEMENT_MB * 1024 * 1024:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "Statement too large")
    try:
        txns = parse_statement(data)
    except StatementError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e)) from e

    inserted = 0
    if txns:
        stmt = (
            insert(BankTransaction)
            .values(
                [
                    {
                        "id": uuid.uuid4(),
                        "tenant_id": user.tenant_id,
                        "date": t.date,
                        "amount": t.amount,
                        "narration": t.narration,
                        "reference": t.reference,
                        "fingerprint": t.fingerprint,
                    }
                    for t in txns
                ]
            )
            .on_conflict_do_nothing(index_elements=["tenant_id", "fingerprint"])
            .returning(BankTransaction.id)
        )
        inserted = len((await session.execute(stmt)).all())
        await session.commit()
    if inserted:
        await queue.enqueue_reconcile(str(user.tenant_id))
    return StatementUploadOut(
        rows=len(txns),
        inserted=inserted,
        duplicates=len(txns) - inserted,
        money_out=sum(1 for t in txns if t.amount < 0),
    )


@router.get("/bank-transactions", response_model=list[BankTxnOut])
async def list_bank_transactions(user: CurrentUserDep, session: SessionDep) -> list[BankTxnOut]:
    matched: dict[str, list[uuid.UUID]] = {}
    for invoice_id, ids in await session.execute(
        select(Match.invoice_id, Match.bank_transaction_ids).where(
            Match.tenant_id == user.tenant_id
        )
    ):
        for tid in ids or []:
            matched.setdefault(tid, []).append(invoice_id)
    rows = await session.scalars(
        select(BankTransaction)
        .where(BankTransaction.tenant_id == user.tenant_id)
        .order_by(BankTransaction.date.desc())
        .limit(500)
    )
    return [
        BankTxnOut(
            id=t.id,
            date=t.date,
            amount=t.amount,
            narration=t.narration,
            reference=t.reference,
            matched_invoice_ids=matched.get(str(t.id), []),
        )
        for t in rows
    ]


@router.get("/reconciliation", response_model=list[ReconciliationRow])
async def reconciliation(user: CurrentUserDep, session: SessionDep) -> list[ReconciliationRow]:
    rows = await session.execute(
        select(Match, Invoice)
        .join(Invoice, Invoice.id == Match.invoice_id)
        .where(Match.tenant_id == user.tenant_id)
        .order_by(Invoice.invoice_date.desc().nulls_last(), Invoice.created_at.desc())
    )
    out = []
    for m, inv in rows:
        ext = inv.extraction or {}
        payment = (m.details or {}).get("payment", {})
        out.append(
            ReconciliationRow(
                invoice_id=inv.id,
                invoice_status=inv.status.value,
                vendor_name=ext.get("vendor_name"),
                invoice_number=ext.get("invoice_number"),
                invoice_date=inv.invoice_date,
                total=inv.total,
                status=m.status.value,
                po_number=((m.details or {}).get("po") or {}).get("po_number"),
                paid=Decimal(str(payment.get("paid", 0))),
                outstanding=Decimal(str(payment.get("outstanding", inv.total or 0))),
                tds_amount=Decimal(str(payment.get("tds_amount", 0))),
                explanation=m.explanation,
            )
        )
    return out
