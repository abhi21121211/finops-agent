import pytest
from fastapi import HTTPException

from app.core.config import get_settings
from app.core.ratelimit import Limit, enforce
from app.core.redis import get_redis
from tests.conftest import FakeRedis


async def test_per_visitor_limit_with_retry_after():
    redis, limit = FakeRedis(), Limit("upload", per_ip_per_hour=3)
    for _ in range(3):
        await enforce(redis, limit, "1.2.3.4")
    with pytest.raises(HTTPException) as e:
        await enforce(redis, limit, "1.2.3.4")
    assert e.value.status_code == 429
    assert "3 per hour" in e.value.detail
    assert int(e.value.headers["Retry-After"]) > 0
    await enforce(redis, limit, "5.6.7.8")  # other visitors unaffected


async def test_daily_llm_budget_is_shared_by_all_visitors(monkeypatch):
    monkeypatch.setattr(get_settings(), "demo_daily_llm_budget", 2)
    redis, limit = FakeRedis(), Limit("invoice", 100, counts_toward_daily_llm_budget=True)
    await enforce(redis, limit, "1.1.1.1")
    await enforce(redis, limit, "2.2.2.2")
    with pytest.raises(HTTPException) as e:
        await enforce(redis, limit, "3.3.3.3")
    assert e.value.status_code == 429 and "daily quota" in e.value.detail


async def test_redis_outage_does_not_block_requests():
    await enforce(FakeRedis(fail=True), Limit("upload", 1), "1.2.3.4")


async def test_demo_login_limited_end_to_end(client, monkeypatch):
    from app.main import app

    fake = FakeRedis()
    app.dependency_overrides[get_redis] = lambda: fake
    monkeypatch.setattr(get_settings(), "rate_limits_enabled", True)
    limit = get_settings().demo_logins_per_hour
    codes = [(await client.post("/api/v1/auth/demo")).status_code for _ in range(limit + 1)]
    assert codes[:limit] == [200] * limit and codes[-1] == 429
