import os

from fastapi import Header, HTTPException
from sqlalchemy import select

from app.db.engine import get_session
from app.db.models import ApiKey
from app.services.auth import hash_key
from app.services.http_client import get


async def require_api_key(authorization: str = Header(...)) -> ApiKey:
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


async def require_user(authorization: str = Header(...)) -> str:
    """
    Validates the Supabase access token by calling /auth/v1/user.
    Avoids local JWT verification — no secret needed, always authoritative.
    """
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authorization header must be: Bearer <token>")

    token = authorization[7:]
    supabase_url = os.environ.get("SUPABASE_URL", "").strip()
    supabase_key = os.environ.get("SUPABASE_ANON_KEY", "").replace(" ", "").replace("\n", "")

    if not supabase_url or not supabase_key:
        raise HTTPException(status_code=500, detail="Supabase not configured on the server")

    try:
        resp = await get().get(
            f"{supabase_url}/auth/v1/user",
            headers={
                "apikey": supabase_key,
                "Authorization": f"Bearer {token}",
            },
        )
    except Exception:
        raise HTTPException(status_code=503, detail="Auth service unreachable")

    if resp.status_code != 200:
        raise HTTPException(status_code=401, detail="Invalid or expired session")

    user_id: str | None = resp.json().get("id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Token missing user ID")

    return user_id
