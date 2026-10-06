"""Live workflow progress: the worker publishes to Redis, the SSE endpoint subscribes."""

import json
from typing import Any

from redis.asyncio import Redis


def channel(invoice_id: str) -> str:
    return f"invoice-events:{invoice_id}"


async def publish(redis: Redis, invoice_id: str, kind: str, payload: dict[str, Any]) -> None:
    await redis.publish(channel(invoice_id), json.dumps({"type": kind, **payload}, default=str))
