"""Read-only data the workflow needs (vendor master, other invoices, tenant settings).

Nodes get this through `config["configurable"]["lookups"]` so tests can pass a fake;
the default reads Postgres, always scoped to the invoice's tenant."""

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Protocol

from langchain_core.runnables import RunnableConfig
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.agents.tools.validation import ExistingInvoice
from app.db.models import (
    BankTransaction,
    Invoice,
    InvoiceStatus,
    Match,
    PurchaseOrder,
    Tenant,
    Vendor,
)
from app.db.session import SessionLocal
from app.reconcile.payments import OpenInvoice, Txn
from app.reconcile.po_match import POData, POLineData


@dataclass(frozen=True)
class TenantSettings:
    confidence_threshold: float = 0.85
    auto_approve_limit: Decimal = Decimal("50000")


class Lookups(Protocol):
    async def known_vendor_gstins(self, tenant_id: str) -> set[str]: ...
    async def other_invoices(self, tenant_id: str, exclude_id: str) -> list[ExistingInvoice]: ...
    async def tenant_settings(self, tenant_id: str) -> TenantSettings: ...
    async def purchase_orders(self, tenant_id: str, exclude_id: str) -> list[POData]: ...
    async def payment_data(self, tenant_id: str) -> tuple[list[OpenInvoice], list[Txn]]: ...


class DbLookups:
    async def known_vendor_gstins(self, tenant_id: str) -> set[str]:
        async with SessionLocal() as s:
            rows = await s.scalars(
                select(Vendor.gstin).where(
                    Vendor.tenant_id == uuid.UUID(tenant_id), Vendor.gstin.isnot(None)
                )
            )
            return set(rows)

    async def other_invoices(self, tenant_id: str, exclude_id: str) -> list[ExistingInvoice]:
        async with SessionLocal() as s:
            rows = await s.execute(
                select(Invoice.id, Invoice.extraction).where(
                    Invoice.tenant_id == uuid.UUID(tenant_id),
                    Invoice.id != uuid.UUID(exclude_id),
                    Invoice.extraction.isnot(None),
                    Invoice.status.notin_([InvoiceStatus.rejected, InvoiceStatus.failed]),
                )
            )
            return [
                ExistingInvoice(
                    invoice_id=str(iid),
                    vendor_gstin=ext.get("vendor_gstin"),
                    invoice_number=ext.get("invoice_number") or "",
                )
                for iid, ext in rows
                if ext  # defensive: tolerate legacy JSON-null rows
            ]

    async def tenant_settings(self, tenant_id: str) -> TenantSettings:
        async with SessionLocal() as s:
            t = await s.get(Tenant, uuid.UUID(tenant_id))
            if t is None:
                return TenantSettings()
            return TenantSettings(float(t.confidence_threshold), t.auto_approve_limit)

    async def purchase_orders(self, tenant_id: str, exclude_id: str) -> list[POData]:
        async with SessionLocal() as s:
            return await load_purchase_orders(s, tenant_id, exclude_id)

    async def payment_data(self, tenant_id: str) -> tuple[list[OpenInvoice], list[Txn]]:
        async with SessionLocal() as s:
            return await load_payment_data(s, tenant_id)


# Rejected and failed invoices neither use up PO quantity nor claim payments.
EXCLUDED = (InvoiceStatus.rejected, InvoiceStatus.failed)


def open_invoice(iid, ext: dict) -> OpenInvoice:
    return OpenInvoice(
        id=str(iid),
        vendor_gstin=ext.get("vendor_gstin"),
        vendor_name=ext.get("vendor_name") or "",
        invoice_number=ext.get("invoice_number") or "",
        invoice_date=date.fromisoformat(ext["invoice_date"]),
        total=Decimal(str(ext["total"])),
        subtotal=Decimal(str(ext["subtotal"])),
    )


async def load_purchase_orders(s, tenant_id: str, exclude_id: str | None) -> list[POData]:
    tid = uuid.UUID(tenant_id)
    pos = (
        await s.scalars(
            select(PurchaseOrder)
            .where(PurchaseOrder.tenant_id == tid)
            .options(selectinload(PurchaseOrder.lines), selectinload(PurchaseOrder.vendor))
        )
    ).all()
    # Quantity already billed per PO line, from other live invoices' match details.
    q = (
        select(Match.po_id, Match.details)
        .join(Invoice, Invoice.id == Match.invoice_id)
        .where(Match.tenant_id == tid, Match.po_id.isnot(None), Invoice.status.notin_(EXCLUDED))
    )
    if exclude_id:
        q = q.where(Match.invoice_id != uuid.UUID(exclude_id))
    billed: dict[tuple[str, int], Decimal] = {}
    for po_id, details in await s.execute(q):
        for lm in ((details or {}).get("po") or {}).get("lines", []):
            if lm.get("po_line") is not None:
                key = (str(po_id), lm["po_line"])
                billed[key] = billed.get(key, Decimal(0)) + Decimal(str(lm["quantity"]))
    return [
        POData(
            po_id=str(po.id),
            po_number=po.po_number,
            vendor_gstin=po.vendor.gstin,
            vendor_name=po.vendor.name,
            total=po.total,
            status=po.status.value,
            lines=[
                POLineData(
                    pl.description,
                    pl.quantity,
                    pl.unit_price,
                    billed.get((str(po.id), n), Decimal(0)),
                )
                for n, pl in enumerate(po.lines)
            ],
        )
        for po in pos
    ]


async def load_payment_data(s, tenant_id: str) -> tuple[list[OpenInvoice], list[Txn]]:
    tid = uuid.UUID(tenant_id)
    rows = await s.execute(
        select(Invoice.id, Invoice.extraction).where(
            Invoice.tenant_id == tid,
            Invoice.extraction.isnot(None),
            Invoice.status.notin_(EXCLUDED),
        )
    )
    invoices = [open_invoice(iid, ext) for iid, ext in rows if ext]
    txns = [
        Txn(str(t.id), t.date, -t.amount, t.narration, t.reference)
        for t in await s.scalars(
            select(BankTransaction).where(
                BankTransaction.tenant_id == tid, BankTransaction.amount < 0
            )
        )
    ]
    return invoices, txns


def get_lookups(config: RunnableConfig | None) -> Lookups:
    return (config or {}).get("configurable", {}).get("lookups") or DbLookups()
