"""Job queue (arq on Redis). Workflow runs never block API requests."""

from typing import Protocol

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from app.core.config import get_settings


class Queue(Protocol):
    async def enqueue_invoice(self, invoice_id: str) -> None: ...


def redis_settings() -> RedisSettings:
    return RedisSettings.from_dsn(get_settings().redis_url)


class ArqQueue:
    _pool: ArqRedis | None = None

    async def enqueue_invoice(self, invoice_id: str) -> None:
        if ArqQueue._pool is None:
            ArqQueue._pool = await create_pool(redis_settings())
        # _job_id makes a double-submit of the same invoice a no-op while queued.
        await ArqQueue._pool.enqueue_job("process_invoice", invoice_id, _job_id=invoice_id)


def get_queue() -> Queue:
    return ArqQueue()
