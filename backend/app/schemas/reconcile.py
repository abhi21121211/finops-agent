"""Reconciliation results (spec §4 "Three-way reconciliation"). Stored in workflow state
and in matches.details, so everything here is JSON-serialisable."""

from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class LineStatus(StrEnum):
    ok = "ok"
    price = "price"  # unit price outside tolerance
    quantity = "quantity"  # more than the PO has left to bill
    unmatched = "unmatched"  # no PO line for this invoice line


class LineMatch(BaseModel):
    invoice_line: int
    po_line: int | None
    invoice_description: str
    po_description: str | None
    similarity: float
    matched_by: Literal["text", "llm", "none"] = "text"
    quantity: Decimal
    quantity_available: Decimal | None  # PO quantity minus what earlier invoices billed
    unit_price: Decimal
    po_unit_price: Decimal | None
    price_diff_pct: float | None
    status: LineStatus
    note: str


class POMatch(BaseModel):
    status: Literal["matched", "mismatch", "no_po"]
    po_id: str | None = None
    po_number: str | None = None
    found_by: Literal["po_number", "vendor_amount", "none"] = "none"
    lines: list[LineMatch] = Field(default_factory=list)
    explanation: str


class Allocation(BaseModel):
    transaction_id: str
    amount: Decimal  # part of the transaction applied to this invoice
    date: date
    narration: str
    kind: Literal["reference", "exact", "combined"]


class PaymentMatch(BaseModel):
    status: Literal["paid", "partial", "unpaid"]
    paid: Decimal = Decimal("0")
    tds_rate: Decimal | None = None  # percent, when the shortfall is a known TDS deduction
    tds_amount: Decimal = Decimal("0")
    outstanding: Decimal
    allocations: list[Allocation] = Field(default_factory=list)
    explanation: str


class MatchResult(BaseModel):
    status: Literal["matched", "partial", "mismatch", "no_po", "unpaid"]
    po: POMatch
    payment: PaymentMatch
    explanation: str

    @property
    def needs_review(self) -> bool:
        """Only PO problems block approval; unpaid/partial describe payment, which normally
        happens after approval (ADR 0002)."""
        return self.status in ("mismatch", "no_po")
