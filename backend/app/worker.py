"""arq worker: runs and resumes the invoice workflow and persists every step.

Run with `arq app.worker.WorkerSettings`.

The graph is checkpointed in Postgres under thread_id = invoice_id. The worker streams
node updates, writes each one to the invoice row and publishes it for the live timeline.
When a run stops at human_review, the job ends; POST /review enqueues resume_invoice,
which continues the same thread, possibly days later and on a different worker.
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
from langgraph.types import Command
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.agents.checkpointer import postgres_checkpointer
from app.agents.graph import build_graph
from app.agents.state import InvoiceState, WorkflowEvent, event
from app.core.config import get_settings
from app.core.events import publish
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


class Run:
    """One invocation of the graph for one invoice, with persistence and publishing."""

    def __init__(self, ctx: dict[str, Any], session: AsyncSession, inv: Invoice, config: dict):
        self.ctx, self.session, self.inv, self.config = ctx, session, inv, config
        self.graph = ctx["graph"]
        self.redis = ctx.get("redis")
        self.started = time.perf_counter()

    async def add_events(self, events: list[WorkflowEvent]) -> None:
        self.inv.events = [*(self.inv.events or []), *events]
        await self.session.commit()
        if self.redis is not None:
            for e in events:
                await publish(self.redis, str(self.inv.id), "event", e)

    async def set_status(self, status: InvoiceStatus) -> None:
        self.inv.status = status
        await self.session.commit()
        if self.redis is not None:
            await publish(self.redis, str(self.inv.id), "status", {"status": status.value})

    async def execute(self, graph_input: Any) -> None:
        try:
            async for chunk in self.graph.astream(graph_input, self.config, stream_mode="updates"):
                for node, update in chunk.items():
                    if node == "__interrupt__":
                        continue
                    values = (await self.graph.aget_state(self.config)).values
                    await self._persist(values)
                    await self.add_events((update or {}).get("events", []))
        except Exception as e:
            await self._handle_error(e)
            return
        await self._finish()

    async def _handle_error(self, e: Exception) -> None:
        job_try = self.ctx.get("job_try", 1)
        if _is_transient(e) and job_try < MAX_TRIES:
            delay = 30 * job_try
            log.warning("workflow_retry", invoice_id=str(self.inv.id), try_=job_try, delay_s=delay)
            await self.add_events(
                [event("workflow", "info", f"LLM providers busy; retrying in {delay}s")]
            )
            # The checkpoint keeps finished steps; the retry resumes at the failed node.
            raise Retry(defer=delay) from e
        log.exception("workflow_failed", invoice_id=str(self.inv.id))
        self.inv.error_message = f"{type(e).__name__}: {e}"[:2000]
        self._stamp_latency()
        await self.add_events([event("workflow", "failed", self.inv.error_message)])
        await self.set_status(InvoiceStatus.failed)

    async def _finish(self) -> None:
        snapshot = await self.graph.aget_state(self.config)
        values = snapshot.values
        self._stamp_latency()
        if "human_review" in snapshot.next:
            await self.add_events([event("workflow", "info", "waiting for a reviewer")])
            await self.set_status(InvoiceStatus.needs_review)
        elif values.get("outcome") in ("approved", "rejected"):
            await self.add_events([event("workflow", "completed", values["outcome"])])
            await self.set_status(InvoiceStatus(values["outcome"]))
        else:
            self.inv.error_message = values.get("error") or "Extraction produced no result"
            await self.add_events([event("workflow", "failed", self.inv.error_message)])
            await self.set_status(InvoiceStatus.failed)
        log.info(
            "invoice_run_done",
            invoice_id=str(self.inv.id),
            status=self.inv.status.value,
            model=self.inv.model_used,
            latency_ms=self.inv.latency_ms,
        )

    def _stamp_latency(self) -> None:
        # Machine time only: accumulates across runs, excludes time waiting for a human.
        self.inv.latency_ms = (self.inv.latency_ms or 0) + int(
            (time.perf_counter() - self.started) * 1000
        )
        self.started = time.perf_counter()

    async def _persist(self, values: InvoiceState) -> None:
        inv, s = self.inv, self.session
        if keys := values.get("page_keys"):
            have = {f.page_no for f in inv.files}
            pages = zip(keys, values["page_hashes"], strict=True)
            for n, (key, sha) in enumerate(pages, start=1):
                if n not in have:
                    s.add(
                        InvoiceFile(
                            invoice_id=inv.id,
                            s3_key=key,
                            content_type="image/jpeg",
                            page_no=n,
                            sha256=sha,
                        )
                    )
        inv.model_used = values.get("model_used") or inv.model_used
        inv.cost_usd = Decimal(str(values.get("cost_usd", 0)))
        inv.list_price_usd = Decimal(str(values.get("list_price_usd", 0)))
        inv.extraction_attempts = values.get("extraction_attempts", 0)
        inv.validation_issues = values.get("validation_issues")
        inv.route = values.get("route")
        inv.route_reasons = values.get("route_reasons")
        inv.field_confidence = values.get("field_confidence") or inv.field_confidence

        extraction = values.get("extraction")
        if extraction and extraction != inv.extraction:
            inv.extraction = extraction
            inv.total = Decimal(str(extraction["total"]))
            inv.invoice_date = date.fromisoformat(extraction["invoice_date"])
            await s.execute(delete(InvoiceLine).where(InvoiceLine.invoice_id == inv.id))
            for pos, li in enumerate(extraction["line_items"]):
                s.add(
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
        await s.commit()
        await s.refresh(inv, ["files"])


async def _load(session: AsyncSession, invoice_id: str) -> Invoice | None:
    return await session.scalar(
        select(Invoice)
        .where(Invoice.id == uuid.UUID(invoice_id))
        .options(selectinload(Invoice.files))
    )


def _thread(invoice_id: str, extra: dict | None = None) -> dict:
    return {"configurable": {"thread_id": invoice_id, **(extra or {})}}


async def process_invoice(
    ctx: dict[str, Any],
    invoice_id: str,
    *,
    reprocess: bool = False,
    configurable: dict | None = None,
) -> None:
    config = _thread(invoice_id, configurable)
    async with SessionLocal() as session:
        inv = await _load(session, invoice_id)
        if inv is None:
            log.warning("invoice_missing", invoice_id=invoice_id)
            return
        run = Run(ctx, session, inv, config)
        job_try = ctx.get("job_try", 1)

        if job_try > 1:
            # Transient failure earlier: continue from the last checkpoint.
            await run.add_events([event("workflow", "started", f"attempt {job_try}, resuming")])
            await run.set_status(InvoiceStatus.processing)
            await run.execute(None)
            return

        if reprocess:
            await run.graph.checkpointer.adelete_thread(invoice_id)
            await session.execute(
                delete(InvoiceFile).where(InvoiceFile.invoice_id == inv.id, InvoiceFile.page_no > 0)
            )
            inv.extraction = inv.field_confidence = inv.validation_issues = None
            inv.route = inv.route_reasons = inv.error_message = None
            inv.latency_ms = None
            await session.commit()
            await session.refresh(inv, ["files"])
        else:
            inv.events = []

        originals = [f for f in inv.files if f.page_no == 0]
        await run.add_events([event("workflow", "started", "reprocess" if reprocess else "")])
        await run.set_status(InvoiceStatus.processing)
        state: InvoiceState = {
            "invoice_id": invoice_id,
            "tenant_id": str(inv.tenant_id),
            "file_keys": [f.s3_key for f in originals],
            "content_types": [f.content_type for f in originals],
            "source": inv.source.value,
            "extraction_attempts": 0,
            "events": [],
        }
        await run.execute(state)


async def resume_invoice(
    ctx: dict[str, Any],
    invoice_id: str,
    decision: dict[str, Any],
    *,
    configurable: dict | None = None,
) -> None:
    config = _thread(invoice_id, configurable)
    async with SessionLocal() as session:
        inv = await _load(session, invoice_id)
        if inv is None:
            return
        run = Run(ctx, session, inv, config)
        snapshot = await run.graph.aget_state(config)
        if "human_review" not in snapshot.next:
            log.warning("resume_without_pause", invoice_id=invoice_id, next=snapshot.next)
            return
        await run.set_status(InvoiceStatus.processing)
        await run.execute(Command(resume=decision))


async def startup(ctx: dict[str, Any]) -> None:
    configure_logging(get_settings().log_level)
    # `kill -USR1 <pid>` dumps every thread's stack: the first tool for a stuck job.
    faulthandler.register(signal.SIGUSR1, all_threads=True)
    await get_storage().ensure_bucket()
    ctx["_checkpointer_cm"] = postgres_checkpointer()
    ctx["graph"] = build_graph(await ctx["_checkpointer_cm"].__aenter__())


async def shutdown(ctx: dict[str, Any]) -> None:
    await ctx["_checkpointer_cm"].__aexit__(None, None, None)


class WorkerSettings:
    functions = [process_invoice, resume_invoice]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = redis_settings()
    max_jobs = 4
    max_tries = MAX_TRIES
    job_timeout = 600
    # Results aren't needed (state lives in Postgres); keeping them would make re-enqueueing
    # the same invoice id a silent no-op for an hour.
    keep_result = 0
