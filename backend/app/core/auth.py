"""JWT auth and tenant resolution.

Dev/demo tokens are HS256 JWTs signed with JWT_SECRET. Supabase Auth JWTs use the
same shape (HS256 with the project's JWT secret), so switching is a config change.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import get_settings

_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class CurrentUser:
    user_id: uuid.UUID
    tenant_id: uuid.UUID
    email: str
    role: str


def create_token(user_id: uuid.UUID, tenant_id: uuid.UUID, email: str, role: str) -> str:
    s = get_settings()
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "tenant_id": str(tenant_id),
        "email": email,
        "role": role,
        "iat": now,
        "exp": now + timedelta(minutes=s.jwt_ttl_minutes),
    }
    return jwt.encode(payload, s.jwt_secret, algorithm=s.jwt_algorithm)


def decode_token(token: str) -> CurrentUser:
    s = get_settings()
    try:
        claims = jwt.decode(token, s.jwt_secret, algorithms=[s.jwt_algorithm])
        return CurrentUser(
            user_id=uuid.UUID(claims["sub"]),
            tenant_id=uuid.UUID(claims["tenant_id"]),
            email=claims.get("email", ""),
            role=claims.get("role", "reviewer"),
        )
    except (jwt.PyJWTError, KeyError, ValueError) as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token") from e


def get_current_user(
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> CurrentUser:
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token")
    return decode_token(creds.credentials)


CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]
