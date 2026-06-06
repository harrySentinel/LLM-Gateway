from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete, select

from app.api.deps import require_user
from app.db.engine import get_session
from app.db.models import ProviderKey
from app.services.encryption import decrypt, encrypt

provider_keys_router = APIRouter()

VALID_PROVIDERS = {"gemini", "groq"}


def _mask(key: str) -> str:
    if len(key) <= 8:
        return "••••••••"
    return key[:8] + "••••••••••••"


class AddKeyRequest(BaseModel):
    provider: str
    api_key: str


class ProviderKeyResponse(BaseModel):
    provider: str
    masked_key: str
    created_at: datetime


@provider_keys_router.get("/provider-keys", response_model=list[ProviderKeyResponse])
async def list_provider_keys(user_id: str = Depends(require_user)):
    async with get_session() as session:
        rows = (
            await session.execute(
                select(ProviderKey).where(
                    ProviderKey.user_id == user_id,
                    ProviderKey.is_active.is_(True),
                )
            )
        ).scalars().all()

    return [
        ProviderKeyResponse(
            provider=r.provider,
            masked_key=_mask(decrypt(r.encrypted_key)),
            created_at=r.created_at,
        )
        for r in rows
    ]


@provider_keys_router.post("/provider-keys", status_code=201)
async def save_provider_key(
    body: AddKeyRequest,
    user_id: str = Depends(require_user),
):
    if body.provider not in VALID_PROVIDERS:
        raise HTTPException(400, detail=f"provider must be one of {VALID_PROVIDERS}")
    if not body.api_key.strip():
        raise HTTPException(400, detail="api_key cannot be empty")

    encrypted = encrypt(body.api_key.strip())
    now = datetime.now(timezone.utc)

    async with get_session() as session:
        existing = (
            await session.execute(
                select(ProviderKey).where(
                    ProviderKey.user_id == user_id,
                    ProviderKey.provider == body.provider,
                )
            )
        ).scalar_one_or_none()

        if existing:
            existing.encrypted_key = encrypted
            existing.created_at = now
            existing.is_active = True
        else:
            session.add(
                ProviderKey(
                    user_id=user_id,
                    provider=body.provider,
                    encrypted_key=encrypted,
                    created_at=now,
                    is_active=True,
                )
            )
        await session.commit()

    return {"message": f"{body.provider} key saved successfully"}


@provider_keys_router.delete("/provider-keys/{provider}", status_code=204)
async def delete_provider_key(
    provider: str,
    user_id: str = Depends(require_user),
):
    if provider not in VALID_PROVIDERS:
        raise HTTPException(400, detail=f"provider must be one of {VALID_PROVIDERS}")

    async with get_session() as session:
        await session.execute(
            delete(ProviderKey).where(
                ProviderKey.user_id == user_id,
                ProviderKey.provider == provider,
            )
        )
        await session.commit()


async def get_user_provider_keys(user_id: str | None) -> dict[str, str]:
    """Returns {provider: plaintext_key} for a user. Used by the chat endpoint."""
    if not user_id:
        return {}
    async with get_session() as session:
        rows = (
            await session.execute(
                select(ProviderKey).where(
                    ProviderKey.user_id == user_id,
                    ProviderKey.is_active.is_(True),
                )
            )
        ).scalars().all()
    return {r.provider: decrypt(r.encrypted_key) for r in rows}
