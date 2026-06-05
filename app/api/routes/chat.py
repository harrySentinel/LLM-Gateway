import asyncio
import time

from fastapi import APIRouter, BackgroundTasks, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.api.deps import require_api_key
from app.db.models import ApiKey
from app.services import logger, router

api_router = APIRouter()


class Message(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    model: str
    messages: list[Message]
    stream: bool = False


async def _stream_and_log(
    model: str,
    messages: list[dict],
    api_key_id: int | None,
):
    """
    Wraps router.stream_chat (which handles provider selection + failover).
    meta_out is populated by the router before the first chunk is yielded,
    so the finally block always reads the actual provider and fallback status.
    """
    usage_out: dict = {}
    meta_out: dict = {}
    t0 = time.monotonic()
    status = "error"
    try:
        async for chunk in router.stream_chat(model, messages, usage_out, meta_out):
            yield chunk
        status = "success"
    finally:
        latency_ms = int((time.monotonic() - t0) * 1000)
        asyncio.create_task(
            logger.write_log(
                model=model,
                provider=meta_out.get("provider", "unknown"),
                prompt_tokens=usage_out.get("prompt_tokens", 0),
                completion_tokens=usage_out.get("completion_tokens", 0),
                latency_ms=latency_ms,
                status=status,
                api_key_id=api_key_id,
                fallback_used=meta_out.get("fallback_used", False),
            )
        )


@api_router.post("/chat")
async def chat(
    request: ChatRequest,
    background_tasks: BackgroundTasks,
    api_key: ApiKey = Depends(require_api_key),
):
    model = request.model
    messages = [m.model_dump() for m in request.messages]

    # ── Streaming path ──────────────────────────────────────────────────────
    if request.stream:
        return StreamingResponse(
            _stream_and_log(model, messages, api_key.id),
            media_type="text/event-stream",
        )

    # ── Non-streaming path ──────────────────────────────────────────────────
    provider_name = "unknown"
    fallback_used = False
    result: dict | None = None
    status = "error"
    t0 = time.monotonic()
    try:
        provider_name, result, fallback_used = await router.chat_completion(model, messages)
        status = "success"
    finally:
        latency_ms = int((time.monotonic() - t0) * 1000)
        usage = result.get("usage", {}) if result else {}
        background_tasks.add_task(
            logger.write_log,
            model=model,
            provider=provider_name,
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            latency_ms=latency_ms,
            status=status,
            api_key_id=api_key.id,
            fallback_used=fallback_used,
        )

    return result
