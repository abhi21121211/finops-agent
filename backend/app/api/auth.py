from fastapi import APIRouter
from pydantic import BaseModel

from app.core.auth import create_token
from app.db.models import UserRole
from app.seed import DEMO_EMAIL, DEMO_TENANT_ID, DEMO_USER_ID

router = APIRouter(prefix="/auth", tags=["auth"])


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


@router.post("/demo", response_model=TokenOut)
async def demo_login() -> TokenOut:
    """'Try the demo': a token for the seeded demo tenant. Real users sign in via Supabase."""
    token = create_token(DEMO_USER_ID, DEMO_TENANT_ID, DEMO_EMAIL, UserRole.admin.value)
    return TokenOut(access_token=token)
