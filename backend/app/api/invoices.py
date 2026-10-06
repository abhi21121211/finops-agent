import hashlib
import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.agents.tools.documents import SUPPORTED_TYPES, sniff_content_type
from app.core.auth import CurrentUserDep
from app.core.config import get_settings
from app.core.queue import Queue, get_queue
from app.db.models import Invoice, InvoiceFile, InvoiceSource, InvoiceStatus
from app.db.session import get_session
from app.schemas.invoice import InvoiceDetail, InvoiceList, InvoiceSummary, PageOut
from app.storage import Storage, get_storage, tenant_key

router = APIRouter(prefix="/invoices", tags=["invoices"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
StorageDep = Annotated[Storage, Depends(get_storage)]
QueueDep = Annotated[Queue, Depends(get_queue)]

_EXT = {"application/pdf": "pdf", "image/png": "png", "image/jpeg": "jpg"}


def _summary(inv: Invoice) -> InvoiceSummary:
    ext = inv.extraction or {}
    conf = inv.field_confidence or {}
    return InvoiceSummary(
        id=inv.id,
        status=inv.status,
        source=inv.source,
        original_filename=inv.original_filename,
        vendor_name=ext.get("vendor_name"),
        invoice_number=ext.get("invoice_number"),
        invoice_date=inv.invoice_date,
        total=inv.total,
        min_confidence=min(conf.values()) if conf else None,
        created_at=inv.created_at,
    )


def _detail(inv: Invoice, storage: Storage) -> InvoiceDetail:
    original = next((f for f in inv.files if f.page_no == 0), None)
    pages = [
        PageOut(page_no=f.page_no, url=storage.presigned_url(f.s3_key))
        for f in inv.files
        if f.page_no > 0
    ]
    return InvoiceDetail(
        **_summary(inv).model_dump(),
        extraction=inv.extraction,
        field_confidence=inv.field_confidence,
        original_url=storage.presigned_url(original.s3_key) if original else None,
        original_content_type=original.content_type if original else None,
        pages=pages,
        events=inv.events or [],
        model_used=inv.model_used,
        cost_usd=inv.cost_usd,
        list_price_usd=inv.list_price_usd,
        latency_ms=inv.latency_ms,
        error_message=inv.error_message,
    )


@router.post("", response_model=InvoiceDetail, status_code=status.HTTP_201_CREATED)
async def upload_invoice(
    user: CurrentUserDep,
    session: SessionDep,
    storage: StorageDep,
    queue: QueueDep,
    file: Annotated[UploadFile, File(description="PDF, PNG or JPG")],
) -> InvoiceDetail:
    max_bytes = get_settings().max_upload_mb * 1024 * 1024
    data = await file.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "File too large")
    if not data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Empty file")
    # Trust the bytes, not the client's Content-Type header.
    ctype = sniff_content_type(data)
    if ctype not in SUPPORTED_TYPES:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Upload a PDF, PNG or JPG")

    invoice_id = uuid.uuid4()
    key = tenant_key(user.tenant_id, "invoices", invoice_id, f"original.{_EXT[ctype]}")
    await storage.put(key, data, ctype)

    inv = Invoice(
        id=invoice_id,
        tenant_id=user.tenant_id,
        status=InvoiceStatus.received,
        source=InvoiceSource.upload,
        original_filename=(file.filename or "upload")[:500],
        events=[],
    )
    inv.files = [
        InvoiceFile(
            s3_key=key, content_type=ctype, page_no=0, sha256=hashlib.sha256(data).hexdigest()
        )
    ]
    session.add(inv)
    await session.commit()

    await queue.enqueue_invoice(str(invoice_id))
    return _detail(await _load(session, user.tenant_id, invoice_id), storage)


@router.get("", response_model=InvoiceList)
async def list_invoices(
    user: CurrentUserDep,
    session: SessionDep,
    status_: Annotated[InvoiceStatus | None, Query(alias="status")] = None,
    vendor: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> InvoiceList:
    q = select(Invoice).where(Invoice.tenant_id == user.tenant_id)
    if status_:
        q = q.where(Invoice.status == status_)
    if vendor:
        q = q.where(Invoice.extraction["vendor_name"].as_string().ilike(f"%{vendor}%"))
    if date_from:
        q = q.where(Invoice.invoice_date >= date_from)
    if date_to:
        q = q.where(Invoice.invoice_date <= date_to)

    count = await session.scalar(select(func.count()).select_from(q.subquery()))
    rows = await session.scalars(q.order_by(Invoice.created_at.desc()).limit(limit).offset(offset))
    return InvoiceList(items=[_summary(i) for i in rows], total=count or 0)


async def _load(session: AsyncSession, tenant_id: uuid.UUID, invoice_id: uuid.UUID) -> Invoice:
    inv = await session.scalar(
        select(Invoice)
        .where(Invoice.id == invoice_id, Invoice.tenant_id == tenant_id)
        .options(selectinload(Invoice.files))
        .execution_options(populate_existing=True)
    )
    if inv is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invoice not found")
    return inv


@router.get("/{invoice_id}", response_model=InvoiceDetail)
async def get_invoice(
    invoice_id: uuid.UUID, user: CurrentUserDep, session: SessionDep, storage: StorageDep
) -> InvoiceDetail:
    return _detail(await _load(session, user.tenant_id, invoice_id), storage)
