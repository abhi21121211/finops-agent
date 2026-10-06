"""Read-only data the workflow needs (vendor master, other invoices, tenant settings).

Nodes get this through `config["configurable"]["lookups"]` so tests can pass a fake;
the default reads Postgres, always scoped to the invoice's tenant."""

import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from langchain_core.runnables import RunnableConfig
from sqlalchemy import select

from app.agents.tools.validation import ExistingInvoice
from app.db.models import Invoice, InvoiceStatus, Tenant, Vendor
from app.db.session import SessionLocal


@dataclass(frozen=True)
class TenantSettings:
    confidence_threshold: float = 0.85
    auto_approve_limit: Decimal = Decimal("50000")


class Lookups(Protocol):
    async def known_vendor_gstins(self, tenant_id: str) -> set[str]: ...
    async def other_invoices(self, tenant_id: str, exclude_id: str) -> list[ExistingInvoice]: ...
    async def tenant_settings(self, tenant_id: str) -> TenantSettings: ...


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
            ]

    async def tenant_settings(self, tenant_id: str) -> TenantSettings:
        async with SessionLocal() as s:
            t = await s.get(Tenant, uuid.UUID(tenant_id))
            if t is None:
                return TenantSettings()
            return TenantSettings(float(t.confidence_threshold), t.auto_approve_limit)


def get_lookups(config: RunnableConfig | None) -> Lookups:
    return (config or {}).get("configurable", {}).get("lookups") or DbLookups()
