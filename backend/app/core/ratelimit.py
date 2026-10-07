"""Rate limits for the public demo: protect free LLM quota and storage from abuse.

Fixed-window counters in Redis (INCR + EXPIRE): two commands per check, well inside
Upstash's free tier. Two kinds of limit:

- per visitor (client IP) per hour, so one person or bot can't monopolise the demo;
- one global daily budget for everything that triggers LLM calls, so the free provider
  quota survives until the next day whatever happens.

If Redis is unreachable the limiter lets requests through (and logs it): uploads need
Redis for the job queue anyway, so failing closed would only add a second outage mode.
"""

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from redis.asyncio import Redis

from app.core.config import get_settings
from app.core.logging import log
from app.core.redis import get_redis


@dataclass(frozen=True)
class Limit:
    name: str
    per_ip_per_hour: int
    counts_toward_daily_llm_budget: bool = False


def client_ip(request: Request) -> str:
    # uvicorn runs with --proxy-headers behind Render, so this is the visitor's address.
    return request.client.host if request.client else "unknown"


async def _hit(redis: Redis, key: str, window_s: int) -> tuple[int, int]:
    count = await redis.incr(key)
    if count == 1:
        await redis.expire(key, window_s)
    ttl = await redis.ttl(key) if count > 1 else window_s
    return count, max(ttl, 1)


def _minutes(seconds: int) -> str:
    m = max(1, round(seconds / 60))
    return f"{m} minute{'s' if m != 1 else ''}"


async def enforce(redis: Redis, limit: Limit, ip: str) -> None:
    s = get_settings()
    hour = int(time.time() // 3600)
    try:
        count, ttl = await _hit(redis, f"rl:{limit.name}:{ip}:{hour}", 3600)
        if count > limit.per_ip_per_hour:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                f"Demo limit reached: {limit.per_ip_per_hour} per hour. "
                f"Try again in {_minutes(ttl)}.",
                headers={"Retry-After": str(ttl)},
            )
        if limit.counts_toward_daily_llm_budget:
            day = time.strftime("%Y-%m-%d", time.gmtime())
            used, ttl = await _hit(redis, f"rl:llm-budget:{day}", 86400)
            if used > s.demo_daily_llm_budget:
                raise HTTPException(
                    status.HTTP_429_TOO_MANY_REQUESTS,
                    "The demo has processed its daily quota of invoices (it runs on free "
                    f"AI tiers). Please come back in {_minutes(ttl)}.",
                    headers={"Retry-After": str(ttl)},
                )
    except HTTPException:
        raise
    except Exception as e:  # Redis down: don't turn a cache outage into a hard failure
        log.warning("ratelimit_unavailable", error=type(e).__name__)


def rate_limit(limit: Limit) -> Callable[..., Awaitable[None]]:
    async def dependency(request: Request, redis: Annotated[Redis, Depends(get_redis)]) -> None:
        if get_settings().rate_limits_enabled:
            await enforce(redis, limit, client_ip(request))

    return dependency


def _limits() -> dict[str, Limit]:
    s = get_settings()
    return {
        "invoice": Limit("invoice", s.demo_uploads_per_hour, counts_toward_daily_llm_budget=True),
        "statement": Limit("statement", s.demo_statements_per_hour),
        "login": Limit("login", s.demo_logins_per_hour),
    }


def limited(name: str):
    """Dependency for a named limit, e.g. `Depends(limited("invoice"))`."""
    return rate_limit(_limits()[name])
