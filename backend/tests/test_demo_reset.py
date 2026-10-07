"""The daily demo reset: wipes only the demo tenant, then reloads the samples."""

from pathlib import Path

from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import func, select

from app.core.config import get_settings
from app.db.models import BankTransaction, Invoice
from app.db.session import SessionLocal
from app.demo import maybe_schedule_reset, reset_demo
from app.seed import DEMO_TENANT_ID
from tests.conftest import FakeRedis, make_pdf

SAMPLES = Path(__file__).resolve().parents[2] / "samples"


async def _count(model, *where):
    async with SessionLocal() as s:
        return await s.scalar(select(func.count()).select_from(model).where(*where))


async def test_reset_wipes_demo_data_and_loads_samples(client, auth_headers, storage, queue):
    for _ in range(2):
        await client.post(
            "/api/v1/invoices",
            headers=auth_headers,
            files={"file": ("x.pdf", make_pdf(), "application/pdf")},
        )
    assert await _count(Invoice, Invoice.tenant_id == DEMO_TENANT_ID) >= 2
    queue.enqueued.clear()

    async with SessionLocal() as s:
        result = await reset_demo(s, storage, queue, InMemorySaver(), samples_dir=SAMPLES)

    samples = len([p for p in SAMPLES.iterdir() if p.suffix in (".pdf", ".png", ".jpg")])
    assert result["invoices_removed"] >= 2 and result["files_removed"] >= 2
    assert result["samples_loaded"] == samples == len(queue.enqueued)
    assert await _count(Invoice, Invoice.tenant_id == DEMO_TENANT_ID) == samples
    assert await _count(BankTransaction, BankTransaction.tenant_id == DEMO_TENANT_ID) == 7
    # Only the samples' originals remain in storage.
    assert len([k for k in storage.objects if k.startswith(f"tenants/{DEMO_TENANT_ID}")]) == samples


async def test_reset_is_scheduled_at_most_once_per_interval(queue, monkeypatch):
    monkeypatch.setattr(get_settings(), "demo_daily_reset", True)
    redis = FakeRedis()
    assert await maybe_schedule_reset(redis, queue)
    assert not await maybe_schedule_reset(redis, queue)
    assert queue.demo_resets == 1


async def test_reset_disabled_by_default(queue):
    assert not await maybe_schedule_reset(FakeRedis(), queue)
