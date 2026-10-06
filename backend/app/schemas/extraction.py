"""Extraction schema (spec §4). Shared by the extract node, the API and the evals."""

import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, Field, field_validator


def _to_decimal(v: Any) -> Any:
    """Accept 1234.5, "1,234.50", "₹ 1,234.50", "Rs. 1234" from the model."""
    if v is None or isinstance(v, (int, float, Decimal)):
        return Decimal("0") if v is None else v
    if isinstance(v, str):
        cleaned = re.sub(r"[^\d.\-]", "", v.replace("Rs.", ""))
        if cleaned in ("", "-", "."):
            return Decimal("0")
        try:
            return Decimal(cleaned)
        except InvalidOperation:
            return v
    return v


def _blank_to_none(v: Any) -> Any:
    if isinstance(v, str) and v.strip().lower() in ("", "null", "none", "n/a", "na", "-"):
        return None
    return v


Money = Annotated[Decimal, BeforeValidator(_to_decimal)]


def _optional_decimal(v: Any) -> Any:
    v = _blank_to_none(v)
    return None if v is None else _to_decimal(v)


OptMoney = Annotated[Decimal | None, BeforeValidator(_optional_decimal)]
OptStr = Annotated[str | None, BeforeValidator(_blank_to_none)]
OptDate = Annotated[date | None, BeforeValidator(_blank_to_none)]


class LineItem(BaseModel):
    description: str
    hsn_sac: OptStr = None
    quantity: Money
    unit_price: Money
    # Percent (18 = 18%). None when the invoice doesn't print a rate per line: many
    # don't, and a guessed rate would make the tax check reject correct invoices.
    tax_rate: OptMoney = Field(default=None, description="percent, e.g. 18 for 18%")
    amount: Money


class ExtractedInvoice(BaseModel):
    vendor_name: str
    vendor_gstin: OptStr = None
    buyer_gstin: OptStr = None
    invoice_number: str
    invoice_date: date
    due_date: OptDate = None
    po_number: OptStr = None
    currency: str = "INR"
    line_items: list[LineItem]
    subtotal: Money
    cgst: Money = Decimal("0")
    sgst: Money = Decimal("0")
    igst: Money = Decimal("0")
    total: Money
    bank_account_last4: OptStr = None
    bank_ifsc: OptStr = None

    @field_validator("vendor_gstin", "buyer_gstin", "bank_ifsc")
    @classmethod
    def _upper_no_spaces(cls, v: str | None) -> str | None:
        return re.sub(r"\s+", "", v).upper() if v else v

    @field_validator("bank_account_last4")
    @classmethod
    def _last4_only(cls, v: str | None) -> str | None:
        # PII rule (spec §9): never keep more than the last four digits.
        if not v:
            return v
        digits = re.sub(r"\D", "", v)
        return digits[-4:] or None


# Scalar fields that get a confidence score. Line items get one score for the table.
CONFIDENCE_FIELDS: tuple[str, ...] = (
    *(n for n in ExtractedInvoice.model_fields if n != "line_items"),
    "line_items",
)


class ExtractionOutput(BaseModel):
    """What the extract prompt must return."""

    invoice: ExtractedInvoice
    confidence: dict[str, float] = Field(default_factory=dict)

    @field_validator("confidence")
    @classmethod
    def _clamp(cls, v: dict[str, float]) -> dict[str, float]:
        out = {k: max(0.0, min(1.0, float(s))) for k, s in v.items() if k in CONFIDENCE_FIELDS}
        # Missing scores are treated as unsure, so routing sends them to a human.
        return {f: out.get(f, 0.5) for f in CONFIDENCE_FIELDS}
