"""Idempotent seed: the demo tenant and its admin user. Run with `python -m app.seed`."""

import asyncio
import uuid

from sqlalchemy import select

from app.db.models import Tenant, User, UserRole
from app.db.session import SessionLocal

DEMO_TENANT_ID = uuid.UUID("00000000-0000-4000-8000-000000000001")
DEMO_USER_ID = uuid.UUID("00000000-0000-4000-8000-000000000002")
DEMO_EMAIL = "demo@finops.local"


async def seed() -> None:
    async with SessionLocal() as s:
        if await s.get(Tenant, DEMO_TENANT_ID) is None:
            s.add(Tenant(id=DEMO_TENANT_ID, name="Demo Traders Pvt Ltd"))
            await s.flush()
        exists = await s.scalar(select(User).where(User.id == DEMO_USER_ID))
        if exists is None:
            s.add(
                User(
                    id=DEMO_USER_ID,
                    tenant_id=DEMO_TENANT_ID,
                    email=DEMO_EMAIL,
                    role=UserRole.admin,
                )
            )
        await s.commit()


if __name__ == "__main__":
    asyncio.run(seed())
