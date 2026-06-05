from datetime import datetime, timezone

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import select

from app.db.engine import get_session
from app.db.models import ApiKey
from app.services.auth import generate_key, hash_key

keys_router = APIRouter()


class CreateKeyRequest(BaseModel):
    name: str


class KeyResponse(BaseModel):
    id: int
    name: str
    created_at: datetime
    is_active: bool


class CreateKeyResponse(KeyResponse):
    key: str  # plaintext — returned ONCE, never stored, never retrievable again


@keys_router.post("/keys", response_model=CreateKeyResponse, status_code=201)
async def create_key(body: CreateKeyRequest):
    token = generate_key()
    row = ApiKey(
        name=body.name,
        key_hash=hash_key(token),
        created_at=datetime.now(timezone.utc),
        is_active=True,
    )
    async with get_session() as session:
        session.add(row)
        await session.commit()
        await session.refresh(row)
    return CreateKeyResponse(
        id=row.id,
        name=row.name,
        created_at=row.created_at,
        is_active=row.is_active,
        key=token,
    )


@keys_router.get("/keys", response_model=list[KeyResponse])
async def list_keys():
    async with get_session() as session:
        result = await session.execute(
            select(ApiKey).order_by(ApiKey.created_at.desc())
        )
        rows = result.scalars().all()
    return [
        KeyResponse(id=r.id, name=r.name, created_at=r.created_at, is_active=r.is_active)
        for r in rows
    ]
