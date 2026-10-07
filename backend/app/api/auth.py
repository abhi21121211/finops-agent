from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from redis.asyncio import Redis

from app.core.auth import create_token
from app.core.queue import Queue, get_queue
from app.core.ratelimit import limited
from app.core.redis import get_redis
from app.db.models import UserRole
from app.demo import maybe_schedule_reset
from app.seed import DEMO_EMAIL, DEMO_TENANT_ID, DEMO_USER_ID

router = APIRouter(prefix="/auth", tags=["auth"])


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


@router.post("/demo", response_model=TokenOut, dependencies=[Depends(limited("login"))])
async def demo_login(
    redis: Annotated[Redis, Depends(get_redis)], queue: Annotated[Queue, Depends(get_queue)]
) -> TokenOut:
    """'Try the demo': a token for the seeded demo tenant. Real users sign in via Supabase.
    The first demo login of the day also resets the demo data (app/demo.py)."""
    await maybe_schedule_reset(redis, queue)
    token = create_token(DEMO_USER_ID, DEMO_TENANT_ID, DEMO_EMAIL, UserRole.admin.value)
    return TokenOut(access_token=token)
