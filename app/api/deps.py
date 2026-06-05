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
