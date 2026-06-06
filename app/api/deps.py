import os

import jwt
from fastapi import Header, HTTPException
from sqlalchemy import select

from app.db.engine import get_session
from app.db.models import ApiKey
from app.services.auth import hash_key


async def require_api_key(authorization: str = Header(...)) -> ApiKey:
    """
    FastAPI dependency — inject with `Depends(require_api_key)`.
    Extracts the Bearer token, hashes it, and looks it up in the DB.
    Returns the ApiKey row on success; raises 401 on any failure.
    """
    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail="Authorization header must be: Bearer gw_...",
        )
    token = authorization[7:]
    if not token.startswith("gw_"):
        raise HTTPException(status_code=401, detail="Invalid API key format")

    key_hash = hash_key(token)
    async with get_session() as session:
        result = await session.execute(
            select(ApiKey).where(
                ApiKey.key_hash == key_hash,
                ApiKey.is_active.is_(True),
            )
        )
        api_key = result.scalar_one_or_none()

    if api_key is None:
        raise HTTPException(status_code=401, detail="Invalid or revoked API key")

    return api_key


def require_user(authorization: str = Header(...)) -> str:
    """
    FastAPI dependency for dashboard routes — inject with `Depends(require_user)`.
    Validates the Supabase JWT locally using PyJWT (no network call).
    Returns the user's UUID string on success; raises 401 on any failure.
    """
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authorization header must be: Bearer <supabase_token>")

    token = authorization[7:]
    secret = os.environ.get("SUPABASE_JWT_SECRET", "")
    if not secret:
        raise HTTPException(status_code=500, detail="SUPABASE_JWT_SECRET not configured")

    try:
        payload = jwt.decode(
            token,
            secret,
            algorithms=["HS256"],
            options={"verify_aud": False},
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Session expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")

    if payload.get("role") != "authenticated":
        raise HTTPException(status_code=401, detail="Not an authenticated user token")

    user_id: str | None = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Token missing user ID")

    return user_id
