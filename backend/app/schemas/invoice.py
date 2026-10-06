import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel

from app.db.models import InvoiceSource, InvoiceStatus


class InvoiceSummary(BaseModel):
    id: uuid.UUID
    status: InvoiceStatus
    source: InvoiceSource
    original_filename: str | None
    vendor_name: str | None
    invoice_number: str | None
    invoice_date: date | None
    total: Decimal | None
    min_confidence: float | None
    created_at: datetime


class InvoiceList(BaseModel):
    items: list[InvoiceSummary]
    total: int


class PageOut(BaseModel):
    page_no: int
    url: str


class InvoiceDetail(InvoiceSummary):
    extraction: dict[str, Any] | None
    field_confidence: dict[str, float] | None
    original_url: str | None
    original_content_type: str | None
    pages: list[PageOut]
    events: list[dict[str, Any]]
    model_used: str | None
    cost_usd: Decimal
    list_price_usd: Decimal
    latency_ms: int | None
    error_message: str | None
