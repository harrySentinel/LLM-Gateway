"""
Redis-backed cache for Supabase auth token validation.

Without caching, every dashboard API call makes a round-trip to Supabase
/auth/v1/user to validate the JWT (~100ms). With caching, repeated calls
from the same session hit Redis instead (~2ms).

TTL is 5 minutes — short enough that revoked tokens expire quickly.
Falls back to no-cache if Redis is unavailable (REDIS_URL not set or
connection fails), so the app works without Redis in development.
"""

import hashlib
import os
from typing import Optional

import redis.asyncio as aioredis

_redis: Optional[aioredis.Redis] = None
_TTL = 300  # seconds


def init() -> None:
    global _redis
    url = os.environ.get("REDIS_URL", "")
    if url:
        try:
            _redis = aioredis.from_url(url, decode_responses=True)
        except Exception:
            _redis = None


async def close() -> None:
    global _redis
    if _redis:
        await _redis.aclose()
        _redis = None


def _key(token: str) -> str:
    # Hash the token so we never store plaintext JWTs in Redis
    digest = hashlib.sha256(token.encode()).hexdigest()[:32]
    return f"auth:uid:{digest}"


async def get_user_id(token: str) -> Optional[str]:
    if _redis is None:
        return None
    try:
        return await _redis.get(_key(token))
    except Exception:
        return None


async def set_user_id(token: str, user_id: str) -> None:
    if _redis is None:
        return
    try:
        await _redis.setex(_key(token), _TTL, user_id)
    except Exception:
        pass
