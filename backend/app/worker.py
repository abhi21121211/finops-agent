"""arq worker: runs the invoice workflow and persists its results.

Run with `arq app.worker.WorkerSettings`.
"""

import faulthandler
import signal
import time
import uuid
from datetime import date
from decimal import Decimal
from typing import Any

import openai
from arq import Retry
from sqlalchemy import delete, select
from sqlalchemy.orm import selectinload

from app.agents.graph import invoice_graph
from app.agents.state import InvoiceState, event
from app.core.config import get_settings
from app.core.logging import configure_logging, log
from app.core.queue import redis_settings
from app.db.models import Invoice, InvoiceFile, InvoiceLine, InvoiceStatus
from app.db.session import SessionLocal
from app.storage import get_storage

MAX_TRIES = 4
# Free tiers hit rate limits and "high demand" 503s in bursts; those are worth waiting out.
_TRANSIENT = (openai.RateLimitError, openai.APIConnectionError, openai.InternalServerError)


def _is_transient(e: Exception) -> bool:
    return isinstance(e, _TRANSIENT) or (
        isinstance(e, openai.APIStatusError) and e.status_code in (408, 429, 502, 503, 504)
    )


async def process_invoice(ctx: dict[str, Any], invoice_id: str, *, graph=None, config=None):
    graph = graph or invoice_graph
    iid = uuid.UUID(invoice_id)
    started = time.perf_counter()

    async with SessionLocal() as session:
        inv = await session.scalar(
            select(Invoice).where(Invoice.id == iid).options(selectinload(Invoice.files))
        )
        if inv is None:
            log.warning("invoice_missing", invoice_id=invoice_id)
            return
        originals = [f for f in inv.files if f.page_no == 0]
        inv.status = InvoiceStatus.processing
        inv.error_message = None
        job_try = ctx.get("job_try", 1)
        if job_try == 1:
            inv.events = [event("workflow", "started")]
        else:
            inv.events = [*inv.events, event("workflow", "started", f"attempt {job_try}")]
        await session.commit()

        state: InvoiceState = {
            "invoice_id": invoice_id,
            "tenant_id": str(inv.tenant_id),
            "file_keys": [f.s3_key for f in originals],
            "content_types": [f.content_type for f in originals],
            "source": inv.source.value,
            "extraction_attempts": 0,
            "cost_usd": 0.0,
            "list_price_usd": 0.0,
            "llm_latency_ms": 0,
            "events": [],
        }

        try:
            result: InvoiceState = await graph.ainvoke(state, config=config or {})
        except Exception as e:  # any node crash marks the invoice failed, never lost
            if _is_transient(e) and job_try < MAX_TRIES:
                delay = 30 * job_try
                log.warning("workflow_retry", invoice_id=invoice_id, try_=job_try, delay_s=delay)
                inv.events = [
                    *inv.events,
                    event("workflow", "info", f"LLM providers busy; retrying in {delay}s"),
                ]
                await session.commit()
                raise Retry(defer=delay) from e
            log.exception("workflow_failed", invoice_id=invoice_id)
            inv.status = InvoiceStatus.failed
            inv.error_message = f"{type(e).__name__}: {e}"[:2000]
            inv.events = [*inv.events, event("workflow", "failed", inv.error_message)]
            inv.latency_ms = int((time.perf_counter() - started) * 1000)
            await session.commit()
            return

        await _persist(session, inv, result, started)


async def _persist(session, inv: Invoice, result: InvoiceState, started: float) -> None:
    # Page images rendered by intake become invoice_files rows (page 1..n).
    await session.execute(
        delete(InvoiceFile).where(InvoiceFile.invoice_id == inv.id, InvoiceFile.page_no > 0)
    )
    await session.execute(delete(InvoiceLine).where(InvoiceLine.invoice_id == inv.id))
    pages = zip(result.get("page_keys", []), result.get("page_hashes", []), strict=True)
    for n, (key, sha) in enumerate(pages, start=1):
        session.add(
            InvoiceFile(
                invoice_id=inv.id, s3_key=key, content_type="image/jpeg", page_no=n, sha256=sha
            )
        )

    inv.model_used = result.get("model_used") or None
    inv.cost_usd = Decimal(str(result.get("cost_usd", 0)))
    inv.list_price_usd = Decimal(str(result.get("list_price_usd", 0)))
    inv.latency_ms = int((time.perf_counter() - started) * 1000)

    extraction = result.get("extraction")
    if extraction is None:
        inv.status = InvoiceStatus.failed
        inv.error_message = result.get("error") or "Extraction produced no result"
        inv.events = [*inv.events, *result.get("events", []), event("workflow", "failed")]
        await session.commit()
        return

    inv.extraction = extraction
    inv.field_confidence = result.get("field_confidence")
    inv.total = Decimal(str(extraction["total"]))
    inv.invoice_date = date.fromisoformat(extraction["invoice_date"])
    for pos, li in enumerate(extraction["line_items"]):
        session.add(
            InvoiceLine(
                invoice_id=inv.id,
                position=pos,
                description=li["description"],
                hsn_sac=li.get("hsn_sac"),
                quantity=Decimal(str(li["quantity"])),
                unit_price=Decimal(str(li["unit_price"])),
                tax_rate=Decimal(str(li["tax_rate"])),
                amount=Decimal(str(li["amount"])),
            )
        )
    # Until validation and routing exist (M2), nothing is auto-approved.
    inv.status = InvoiceStatus.needs_review
    inv.events = [*inv.events, *result.get("events", []), event("workflow", "completed")]
    await session.commit()
    log.info(
        "invoice_processed", invoice_id=str(inv.id), model=inv.model_used, latency_ms=inv.latency_ms
    )


async def startup(ctx: dict[str, Any]) -> None:
    # `kill -USR1 <pid>` dumps every thread's stack: the first tool for a stuck job.
    faulthandler.register(signal.SIGUSR1, all_threads=True)
    configure_logging(get_settings().log_level)
    await get_storage().ensure_bucket()


class WorkerSettings:
    functions = [process_invoice]
    on_startup = startup
    redis_settings = redis_settings()
    max_jobs = 4
    max_tries = MAX_TRIES
    # Results aren't needed (state lives in Postgres); keeping them would make re-enqueueing
    # the same invoice id a silent no-op for an hour.
    keep_result = 0
    job_timeout = 600
