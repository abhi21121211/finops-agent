"""ORM models for M1. Remaining tables from spec §5 are added in their milestones."""

import enum
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# none_as_null: Python None is stored as SQL NULL, not the JSON value `null`, so
# `IS NULL` filters mean what they say.
JSONType = JSON(none_as_null=True).with_variant(JSONB(none_as_null=True), "postgresql")


class Base(DeclarativeBase):
    pass


def _uuid() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def _tenant_fk() -> Mapped[uuid.UUID]:
    return mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )


class Timestamped:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class UserRole(enum.StrEnum):
    admin = "admin"
    reviewer = "reviewer"


class InvoiceStatus(enum.StrEnum):
    received = "received"
    processing = "processing"
    needs_review = "needs_review"
    awaiting_vendor = "awaiting_vendor"
    approved = "approved"
    rejected = "rejected"
    failed = "failed"


class InvoiceSource(enum.StrEnum):
    upload = "upload"
    email = "email"
    cli = "cli"


def _enum(e: type[enum.Enum], name: str) -> Enum:
    return Enum(e, name=name, values_callable=lambda x: [m.value for m in x])


class Tenant(Base, Timestamped):
    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = _uuid()
    name: Mapped[str] = mapped_column(String(200))
    confidence_threshold: Mapped[Decimal] = mapped_column(Numeric(4, 3), default=Decimal("0.85"))
    auto_approve_limit: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("50000"))


class User(Base, Timestamped):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = _uuid()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    email: Mapped[str] = mapped_column(String(320), unique=True)
    role: Mapped[UserRole] = mapped_column(_enum(UserRole, "user_role"), default=UserRole.admin)


class Vendor(Base, Timestamped):
    __tablename__ = "vendors"
    __table_args__ = (UniqueConstraint("tenant_id", "gstin"),)

    id: Mapped[uuid.UUID] = _uuid()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    name: Mapped[str] = mapped_column(String(300))
    gstin: Mapped[str | None] = mapped_column(String(15))
    email: Mapped[str | None] = mapped_column(String(320))
    bank_account_last4: Mapped[str | None] = mapped_column(String(4))
    bank_ifsc: Mapped[str | None] = mapped_column(String(11))


class Invoice(Base, Timestamped):
    __tablename__ = "invoices"

    id: Mapped[uuid.UUID] = _uuid()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    vendor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("vendors.id", ondelete="SET NULL")
    )
    status: Mapped[InvoiceStatus] = mapped_column(
        _enum(InvoiceStatus, "invoice_status"), default=InvoiceStatus.received, index=True
    )
    source: Mapped[InvoiceSource] = mapped_column(
        _enum(InvoiceSource, "invoice_source"), default=InvoiceSource.upload
    )
    original_filename: Mapped[str | None] = mapped_column(String(500))
    extraction: Mapped[dict | None] = mapped_column(JSONType)
    field_confidence: Mapped[dict | None] = mapped_column(JSONType)
    total: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    invoice_date: Mapped[date | None] = mapped_column(Date)
    model_used: Mapped[str | None] = mapped_column(String(200))
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), default=Decimal("0"))
    list_price_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), default=Decimal("0"))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    error_message: Mapped[str | None] = mapped_column(Text)
    extraction_attempts: Mapped[int] = mapped_column(Integer, default=0)
    validation_issues: Mapped[list | None] = mapped_column(JSONType)
    route: Mapped[str | None] = mapped_column(String(30))
    route_reasons: Mapped[list | None] = mapped_column(JSONType)
    # Workflow timeline (WorkflowEvent dicts). The append-only audit_log arrives in M7.
    events: Mapped[list] = mapped_column(JSONType, default=list)

    files: Mapped[list["InvoiceFile"]] = relationship(
        back_populates="invoice", cascade="all, delete-orphan", order_by="InvoiceFile.page_no"
    )
    lines: Mapped[list["InvoiceLine"]] = relationship(
        back_populates="invoice", cascade="all, delete-orphan", order_by="InvoiceLine.position"
    )


class InvoiceLine(Base):
    __tablename__ = "invoice_lines"

    id: Mapped[uuid.UUID] = _uuid()
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("invoices.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(Integer, default=0)
    description: Mapped[str] = mapped_column(Text)
    hsn_sac: Mapped[str | None] = mapped_column(String(20))
    quantity: Mapped[Decimal] = mapped_column(Numeric(14, 3))
    unit_price: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    tax_rate: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))

    invoice: Mapped[Invoice] = relationship(back_populates="lines")


class InvoiceFile(Base, Timestamped):
    __tablename__ = "invoice_files"

    id: Mapped[uuid.UUID] = _uuid()
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("invoices.id", ondelete="CASCADE"), index=True
    )
    s3_key: Mapped[str] = mapped_column(String(1000))
    content_type: Mapped[str] = mapped_column(String(100))
    # 0 = original upload, 1..n = rendered page images
    page_no: Mapped[int] = mapped_column(Integer, default=0)
    sha256: Mapped[str] = mapped_column(String(64), index=True)

    invoice: Mapped[Invoice] = relationship(back_populates="files")


class ReviewAction(enum.StrEnum):
    approve = "approve"
    edit = "edit"
    reject = "reject"


class ReviewDecision(Base):
    __tablename__ = "review_decisions"

    id: Mapped[uuid.UUID] = _uuid()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("invoices.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    action: Mapped[ReviewAction] = mapped_column(_enum(ReviewAction, "review_action"))
    field_edits: Mapped[dict | None] = mapped_column(JSONType)
    comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class POStatus(enum.StrEnum):
    open = "open"
    closed = "closed"


class PurchaseOrder(Base, Timestamped):
    __tablename__ = "purchase_orders"
    __table_args__ = (UniqueConstraint("tenant_id", "po_number"),)

    id: Mapped[uuid.UUID] = _uuid()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    vendor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("vendors.id", ondelete="RESTRICT"), index=True
    )
    po_number: Mapped[str] = mapped_column(String(100))
    date: Mapped[date] = mapped_column(Date)
    total: Mapped[Decimal] = mapped_column(Numeric(14, 2))  # before tax
    status: Mapped[POStatus] = mapped_column(_enum(POStatus, "po_status"), default=POStatus.open)

    lines: Mapped[list["POLine"]] = relationship(
        back_populates="po", cascade="all, delete-orphan", order_by="POLine.position"
    )
    vendor: Mapped[Vendor] = relationship()


class POLine(Base):
    __tablename__ = "po_lines"

    id: Mapped[uuid.UUID] = _uuid()
    po_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("purchase_orders.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(Integer, default=0)
    description: Mapped[str] = mapped_column(Text)
    quantity: Mapped[Decimal] = mapped_column(Numeric(14, 3))
    unit_price: Mapped[Decimal] = mapped_column(Numeric(14, 2))

    po: Mapped[PurchaseOrder] = relationship(back_populates="lines")


class BankTransaction(Base):
    __tablename__ = "bank_transactions"
    # Re-uploading the same statement must not duplicate rows.
    __table_args__ = (UniqueConstraint("tenant_id", "fingerprint"),)

    id: Mapped[uuid.UUID] = _uuid()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    date: Mapped[date] = mapped_column(Date, index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))  # negative = money out
    narration: Mapped[str] = mapped_column(Text)
    reference: Mapped[str | None] = mapped_column(String(200))
    fingerprint: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MatchStatus(enum.StrEnum):
    matched = "matched"
    partial = "partial"
    mismatch = "mismatch"
    no_po = "no_po"
    unpaid = "unpaid"


class Match(Base, Timestamped):
    __tablename__ = "matches"

    id: Mapped[uuid.UUID] = _uuid()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("invoices.id", ondelete="CASCADE"), unique=True
    )
    po_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("purchase_orders.id", ondelete="SET NULL"), index=True
    )
    bank_transaction_ids: Mapped[list | None] = mapped_column(JSONType)
    status: Mapped[MatchStatus] = mapped_column(_enum(MatchStatus, "match_status"))
    explanation: Mapped[str] = mapped_column(Text)
    # Machine-readable detail: PO line comparison and payment allocations.
    details: Mapped[dict | None] = mapped_column(JSONType)


class EvalRun(Base):
    """One evaluation run (spec §8). Global, not tenant data."""

    __tablename__ = "eval_runs"

    id: Mapped[uuid.UUID] = _uuid()
    git_sha: Mapped[str | None] = mapped_column(String(40))
    label: Mapped[str | None] = mapped_column(String(200))
    mode: Mapped[str] = mapped_column(String(20))  # full | smoke | oracle | custom
    model: Mapped[str | None] = mapped_column(String(200))  # most-used model
    n_cases: Mapped[int] = mapped_column(Integer)
    field_accuracy: Mapped[float] = mapped_column(Numeric(6, 4))
    line_item_f1: Mapped[float] = mapped_column(Numeric(6, 4))
    routing_accuracy: Mapped[float] = mapped_column(Numeric(6, 4))
    false_auto_approvals: Mapped[int] = mapped_column(Integer)
    auto_approval_rate: Mapped[float] = mapped_column(Numeric(6, 4))
    match_accuracy: Mapped[float | None] = mapped_column(Numeric(6, 4))
    avg_cost_usd: Mapped[float] = mapped_column(Numeric(12, 6))
    p50_latency_ms: Mapped[int] = mapped_column(Integer)
    p95_latency_ms: Mapped[int] = mapped_column(Integer)
    report: Mapped[dict] = mapped_column(JSONType)  # per-case detail for drill-down
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EvalResult(Base):
    __tablename__ = "eval_results"

    id: Mapped[uuid.UUID] = _uuid()
    eval_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("eval_runs.id", ondelete="CASCADE"), index=True
    )
    case_id: Mapped[str] = mapped_column(String(40))
    field: Mapped[str] = mapped_column(String(60))
    expected: Mapped[str | None] = mapped_column(Text)
    actual: Mapped[str | None] = mapped_column(Text)
    correct: Mapped[bool] = mapped_column()
