"""Job queue (arq on Redis). Workflow runs never block API requests."""

from typing import Any, Protocol

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from app.core.config import get_settings


class Queue(Protocol):
    async def enqueue_invoice(self, invoice_id: str, *, reprocess: bool = False) -> None: ...
    async def enqueue_resume(self, invoice_id: str, decision: dict[str, Any]) -> None: ...


def redis_settings() -> RedisSettings:
    return RedisSettings.from_dsn(get_settings().redis_url)


class ArqQueue:
    _pool: ArqRedis | None = None

    async def _get(self) -> ArqRedis:
        if ArqQueue._pool is None:
            ArqQueue._pool = await create_pool(redis_settings())
        return ArqQueue._pool

    async def enqueue_invoice(self, invoice_id: str, *, reprocess: bool = False) -> None:
        # One job id per invoice: a double-submit is a no-op while a run is queued/running.
        await (await self._get()).enqueue_job(
            "process_invoice", invoice_id, reprocess=reprocess, _job_id=f"run:{invoice_id}"
        )

    async def enqueue_resume(self, invoice_id: str, decision: dict[str, Any]) -> None:
        await (await self._get()).enqueue_job(
            "resume_invoice", invoice_id, decision, _job_id=f"run:{invoice_id}"
        )


def get_queue() -> Queue:
    return ArqQueue()
