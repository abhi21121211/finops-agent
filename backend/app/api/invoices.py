import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import StreamingResponse
from pydantic import ValidationError
from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.agents.nodes.human_review import apply_field_edits
from app.core.auth import CurrentUserDep
from app.core.config import get_settings
from app.core.events import channel
from app.core.queue import Queue, get_queue
from app.core.ratelimit import limited
from app.core.redis import get_redis
from app.db.models import (
    Invoice,
    InvoiceStatus,
    Match,
    ReviewAction,
    ReviewDecision,
    User,
)
from app.db.session import get_session
from app.ingest import UnsupportedFile, create_invoice
from app.schemas.invoice import (
    InvoiceDetail,
    InvoiceList,
    InvoiceSummary,
    PageOut,
    ReviewIn,
    ReviewOut,
)
from app.storage import Storage, get_storage

router = APIRouter(prefix="/invoices", tags=["invoices"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
StorageDep = Annotated[Storage, Depends(get_storage)]
QueueDep = Annotated[Queue, Depends(get_queue)]

_EXT = {"application/pdf": "pdf", "image/png": "png", "image/jpeg": "jpg"}
_FINAL = {InvoiceStatus.approved, InvoiceStatus.rejected, InvoiceStatus.failed}
RedisDep = Annotated[Redis, Depends(get_redis)]


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
        route_reasons=inv.route_reasons or [],
        created_at=inv.created_at,
    )


async def _detail(session: AsyncSession, inv: Invoice, storage: Storage) -> InvoiceDetail:
    reviews = await session.execute(
        select(ReviewDecision, User.email)
        .join(User, User.id == ReviewDecision.user_id, isouter=True)
        .where(ReviewDecision.invoice_id == inv.id)
        .order_by(ReviewDecision.created_at)
    )
    match = await session.scalar(select(Match).where(Match.invoice_id == inv.id))
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
        extraction_attempts=inv.extraction_attempts,
        validation_issues=inv.validation_issues or [],
        route=inv.route,
        match=match.details if match else None,
        reviews=[
            ReviewOut(
                action=r.action,
                field_edits=r.field_edits,
                comment=r.comment,
                user_email=email,
                created_at=r.created_at,
            )
            for r, email in reviews
        ],
    )


@router.post(
    "",
    response_model=InvoiceDetail,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(limited("invoice"))],
)
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
    try:
        invoice_id = await create_invoice(
            session, storage, queue, user.tenant_id, data, file.filename or "upload"
        )
    except UnsupportedFile as e:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, str(e)) from e
    return await _detail(session, await _load(session, user.tenant_id, invoice_id), storage)


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


async def _load(
    session: AsyncSession, tenant_id: uuid.UUID, invoice_id: uuid.UUID, *, lock: bool = False
) -> Invoice:
    q = (
        select(Invoice)
        .where(Invoice.id == invoice_id, Invoice.tenant_id == tenant_id)
        .options(selectinload(Invoice.files))
        .execution_options(populate_existing=True)
    )
    inv = await session.scalar(q.with_for_update(of=Invoice) if lock else q)
    if inv is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invoice not found")
    return inv


@router.get("/{invoice_id}", response_model=InvoiceDetail)
async def get_invoice(
    invoice_id: uuid.UUID, user: CurrentUserDep, session: SessionDep, storage: StorageDep
) -> InvoiceDetail:
    return await _detail(session, await _load(session, user.tenant_id, invoice_id), storage)


@router.post("/{invoice_id}/review", response_model=InvoiceDetail)
async def review_invoice(
    invoice_id: uuid.UUID,
    body: ReviewIn,
    user: CurrentUserDep,
    session: SessionDep,
    storage: StorageDep,
    queue: QueueDep,
) -> InvoiceDetail:
    """Approve, edit (then approve) or reject a paused invoice; resumes its workflow."""
    # Row lock: two reviewers clicking at once cannot both resume the run.
    inv = await _load(session, user.tenant_id, invoice_id, lock=True)
    if inv.status != InvoiceStatus.needs_review:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Invoice is {inv.status.value}")
    if body.action == ReviewAction.edit:
        if not body.field_edits:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "edit needs field_edits")
        try:
            apply_field_edits(inv.extraction or {}, body.field_edits)
        except (ValueError, ValidationError) as e:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e)[:1000]) from e
    elif body.field_edits:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "field_edits only apply to action=edit"
        )

    session.add(
        ReviewDecision(
            tenant_id=user.tenant_id,
            invoice_id=inv.id,
            user_id=user.user_id,
            action=body.action,
            field_edits=body.field_edits or None,
            comment=body.comment,
        )
    )
    inv.status = InvoiceStatus.processing
    await session.commit()

    await queue.enqueue_resume(
        str(inv.id),
        {
            "action": body.action.value,
            "field_edits": body.field_edits,
            "comment": body.comment,
            "user_id": str(user.user_id),
            "user_email": user.email,
        },
    )
    return await _detail(session, await _load(session, user.tenant_id, invoice_id), storage)


@router.post(
    "/{invoice_id}/reprocess",
    response_model=InvoiceDetail,
    dependencies=[Depends(limited("invoice"))],  # re-runs the LLM, same budget as uploads
)
async def reprocess_invoice(
    invoice_id: uuid.UUID,
    user: CurrentUserDep,
    session: SessionDep,
    storage: StorageDep,
    queue: QueueDep,
) -> InvoiceDetail:
    """Run the workflow again from the start (fresh extraction)."""
    inv = await _load(session, user.tenant_id, invoice_id, lock=True)
    if inv.status in (InvoiceStatus.received, InvoiceStatus.processing):
        raise HTTPException(status.HTTP_409_CONFLICT, "Invoice is already being processed")
    inv.status = InvoiceStatus.received
    await session.commit()
    await queue.enqueue_invoice(str(inv.id), reprocess=True)
    return await _detail(session, await _load(session, user.tenant_id, invoice_id), storage)


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, default=str)}\n\n"


@router.get("/{invoice_id}/events")
async def invoice_events(
    invoice_id: uuid.UUID,
    request: Request,
    user: CurrentUserDep,
    session: SessionDep,
    redis: RedisDep,
) -> StreamingResponse:
    """Server-Sent Events: workflow events and status changes as they happen.

    Subscribes before reading the snapshot, so nothing published in between is lost
    (the client may see an event twice and should de-duplicate by timestamp)."""
    pubsub = redis.pubsub()
    await pubsub.subscribe(channel(str(invoice_id)))
    try:
        inv = await _load(session, user.tenant_id, invoice_id)
    except HTTPException:
        await pubsub.aclose()
        raise
    snapshot = {"type": "snapshot", "status": inv.status.value, "events": inv.events or []}
    await session.close()  # don't hold a DB connection for the life of the stream

    async def stream() -> AsyncIterator[str]:
        try:
            yield _sse(snapshot)
            idle = 0.0
            while not await request.is_disconnected():
                msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if msg is None:
                    idle += 1.0
                    if idle >= 15:
                        yield ": keep-alive\n\n"
                        idle = 0.0
                    continue
                idle = 0.0
                yield f"data: {msg['data'].decode()}\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            await pubsub.aclose()

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
