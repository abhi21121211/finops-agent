import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field

from app.db.models import InvoiceSource, InvoiceStatus, ReviewAction


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
    route_reasons: list[str]
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
    extraction_attempts: int
    validation_issues: list[dict[str, Any]]
    route: str | None
    match: dict[str, Any] | None  # MatchResult: PO line comparison + payment allocations
    reviews: list["ReviewOut"]


class ReviewIn(BaseModel):
    action: ReviewAction
    field_edits: dict[str, Any] = Field(default_factory=dict)
    comment: str | None = Field(default=None, max_length=2000)


class ReviewOut(BaseModel):
    action: ReviewAction
    field_edits: dict[str, Any] | None
    comment: str | None
    user_email: str | None
    created_at: datetime
