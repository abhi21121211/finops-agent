"""Creating an invoice from a file: shared by the upload API and the demo loader."""

import hashlib
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.tools.documents import SUPPORTED_TYPES, sniff_content_type
from app.core.queue import Queue
from app.db.models import Invoice, InvoiceFile, InvoiceSource, InvoiceStatus
from app.storage import Storage, tenant_key

_EXT = {"application/pdf": "pdf", "image/png": "png", "image/jpeg": "jpg"}


class UnsupportedFile(ValueError):
    pass


async def create_invoice(
    session: AsyncSession,
    storage: Storage,
    queue: Queue,
    tenant_id: uuid.UUID,
    data: bytes,
    filename: str,
    source: InvoiceSource = InvoiceSource.upload,
) -> uuid.UUID:
    """Store the file, create the invoice row and queue the workflow."""
    # Trust the bytes, not the client's Content-Type header.
    ctype = sniff_content_type(data)
    if ctype not in SUPPORTED_TYPES:
        raise UnsupportedFile("Upload a PDF, PNG or JPG")
    invoice_id = uuid.uuid4()
    key = tenant_key(tenant_id, "invoices", invoice_id, f"original.{_EXT[ctype]}")
    await storage.put(key, data, ctype)
    inv = Invoice(
        id=invoice_id,
        tenant_id=tenant_id,
        status=InvoiceStatus.received,
        source=source,
        original_filename=filename[:500],
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
    return invoice_id
