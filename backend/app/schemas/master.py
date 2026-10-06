import uuid
from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator

from app.agents.tools.gstin import gstin_problem


class VendorIn(BaseModel):
    name: str = Field(min_length=1, max_length=300)
    gstin: str | None = None
    email: str | None = Field(default=None, max_length=320)
    bank_account_last4: str | None = Field(default=None, pattern=r"^\d{4}$")
    bank_ifsc: str | None = Field(default=None, pattern=r"^[A-Z]{4}0[A-Z0-9]{6}$")

    @field_validator("gstin")
    @classmethod
    def _valid_gstin(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = v.strip().upper()
        if problem := gstin_problem(v):
            raise ValueError(f"invalid GSTIN: {problem}")
        return v


class VendorOut(VendorIn):
    id: uuid.UUID


class POLineIn(BaseModel):
    description: str = Field(min_length=1)
    quantity: Decimal = Field(gt=0)
    unit_price: Decimal = Field(ge=0)


class POIn(BaseModel):
    vendor_id: uuid.UUID
    po_number: str = Field(min_length=1, max_length=100)
    date: date
    lines: list[POLineIn] = Field(min_length=1)


class POLineOut(POLineIn):
    billed: Decimal = Decimal("0")


class POOut(BaseModel):
    id: uuid.UUID
    po_number: str
    date: date
    status: str
    total: Decimal
    vendor_id: uuid.UUID
    vendor_name: str
    lines: list[POLineOut]


class StatementUploadOut(BaseModel):
    rows: int
    inserted: int
    duplicates: int
    money_out: int


class BankTxnOut(BaseModel):
    id: uuid.UUID
    date: date
    amount: Decimal
    narration: str
    reference: str | None
    matched_invoice_ids: list[uuid.UUID]


class ReconciliationRow(BaseModel):
    invoice_id: uuid.UUID
    invoice_status: str
    vendor_name: str | None
    invoice_number: str | None
    invoice_date: date | None
    total: Decimal | None
    status: str
    po_number: str | None
    paid: Decimal
    outstanding: Decimal
    tds_amount: Decimal
    explanation: str
