"""Keep the public demo tidy: reset the shared demo tenant at most once a day and preload
the sample invoices, so every visitor starts from the same, working state.

Render's free service sleeps when idle, so a cron job can't be relied on. Instead the
first demo login of the day claims a 24-hour Redis lock and enqueues the reset.

Only the demo tenant is touched; eval history (global) and other tenants never are.
"""

import uuid
from pathlib import Path

from redis.asyncio import Redis
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import log
from app.core.queue import Queue
from app.db.models import BankTransaction, Invoice, InvoiceFile, InvoiceSource
from app.ingest import create_invoice
from app.reconcile.bank_csv import parse_statement
from app.seed import DEMO_TENANT_ID, seed
from app.storage import Storage

RESET_LOCK = "demo:reset-lock"
SAMPLE_PATTERNS = ("sample-*.pdf", "sample-*.png", "sample-*.jpg")
STATEMENT_GLOB = "bank-statement-*.csv"


async def maybe_schedule_reset(redis: Redis, queue: Queue) -> bool:
    """Enqueue a reset if none ran in the last 24 hours. Never blocks the login."""
    s = get_settings()
    if not s.demo_daily_reset:
        return False
    try:
        if await redis.set(RESET_LOCK, "1", nx=True, ex=s.demo_reset_interval_s):
            await queue.enqueue_demo_reset()
            return True
    except Exception as e:
        log.warning("demo_reset_schedule_failed", error=type(e).__name__)
    return False


async def reset_demo(
    session: AsyncSession,
    storage: Storage,
    queue: Queue | None,
    checkpointer=None,
    samples_dir: Path | None = None,
) -> dict:
    """Wipe the demo tenant's invoices and payments, then load the samples."""
    tid = DEMO_TENANT_ID
    invoice_ids = list(await session.scalars(select(Invoice.id).where(Invoice.tenant_id == tid)))
    keys = list(
        await session.scalars(
            select(InvoiceFile.s3_key)
            .join(Invoice, Invoice.id == InvoiceFile.invoice_id)
            .where(Invoice.tenant_id == tid)
        )
    )

    for key in keys:
        try:
            await storage.delete(key)
        except Exception as e:  # a missing object must not stop the reset
            log.warning("demo_reset_delete_failed", error=type(e).__name__)
    if checkpointer is not None:
        for iid in invoice_ids:
            await checkpointer.adelete_thread(str(iid))

    # invoice_lines, invoice_files, matches and review_decisions cascade with invoices.
    await session.execute(delete(Invoice).where(Invoice.tenant_id == tid))
    await session.execute(delete(BankTransaction).where(BankTransaction.tenant_id == tid))
    await session.commit()
    await seed()  # vendors and POs are idempotent; restores anything edited

    loaded = 0
    directory = samples_dir or Path(get_settings().demo_samples_dir)
    if queue is not None and directory.is_dir():
        files = sorted(f for pattern in SAMPLE_PATTERNS for f in directory.glob(pattern))
        for f in files:
            await create_invoice(
                session, storage, queue, tid, f.read_bytes(), f.name, InvoiceSource.upload
            )
            loaded += 1
        for csv in sorted(directory.glob(STATEMENT_GLOB)):
            await _import_statement(session, tid, csv.read_bytes())
        await session.commit()

    result = {
        "invoices_removed": len(invoice_ids),
        "files_removed": len(keys),
        "samples_loaded": loaded,
    }
    log.info("demo_reset", **result)
    return result


async def _import_statement(session: AsyncSession, tid: uuid.UUID, data: bytes) -> None:
    rows = [
        {
            "id": uuid.uuid4(),
            "tenant_id": tid,
            "date": t.date,
            "amount": t.amount,
            "narration": t.narration,
            "reference": t.reference,
            "fingerprint": t.fingerprint,
        }
        for t in parse_statement(data)
    ]
    if rows:
        await session.execute(
            insert(BankTransaction)
            .values(rows)
            .on_conflict_do_nothing(index_elements=["tenant_id", "fingerprint"])
        )
