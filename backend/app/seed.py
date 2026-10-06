"""Idempotent seed: the demo tenant, its admin user and vendor master.
Run with `python -m app.seed`."""

import asyncio
import uuid

from sqlalchemy import select

from app.db.models import Tenant, User, UserRole, Vendor
from app.db.session import SessionLocal
from app.demo_data import BUYER_NAME, KNOWN_VENDORS

DEMO_TENANT_ID = uuid.UUID("00000000-0000-4000-8000-000000000001")
DEMO_USER_ID = uuid.UUID("00000000-0000-4000-8000-000000000002")
DEMO_EMAIL = "demo@finops.local"


async def seed() -> None:
    async with SessionLocal() as s:
        if await s.get(Tenant, DEMO_TENANT_ID) is None:
            s.add(Tenant(id=DEMO_TENANT_ID, name=BUYER_NAME))
            await s.flush()
        if await s.scalar(select(User).where(User.id == DEMO_USER_ID)) is None:
            s.add(
                User(
                    id=DEMO_USER_ID,
                    tenant_id=DEMO_TENANT_ID,
                    email=DEMO_EMAIL,
                    role=UserRole.admin,
                )
            )
        existing = set(
            await s.scalars(select(Vendor.gstin).where(Vendor.tenant_id == DEMO_TENANT_ID))
        )
        for v in KNOWN_VENDORS:
            if v.gstin not in existing:
                s.add(
                    Vendor(
                        tenant_id=DEMO_TENANT_ID,
                        name=v.name,
                        gstin=v.gstin,
                        email=v.email,
                        bank_account_last4=v.bank_last4,
                        bank_ifsc=v.bank_ifsc,
                    )
                )
        await s.commit()


if __name__ == "__main__":
    asyncio.run(seed())
