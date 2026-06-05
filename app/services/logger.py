import logging
from datetime import datetime, timezone

from app.db.engine import get_session
from app.db.models import RequestLog
from app.services.pricing import calculate_cost

_log = logging.getLogger(__name__)


async def write_log(
    *,
    model: str,
    provider: str,
    prompt_tokens: int,
    completion_tokens: int,
    latency_ms: int,
    status: str,
    api_key_id: int | None = None,
    fallback_used: bool = False,
) -> None:
    row = RequestLog(
        timestamp=datetime.now(timezone.utc),
        model=model,
        provider=provider,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cost_usd=calculate_cost(model, prompt_tokens, completion_tokens),
        latency_ms=latency_ms,
        status=status,
        api_key_id=api_key_id,
        fallback_used=fallback_used,
    )
    try:
        async with get_session() as session:
            session.add(row)
            await session.commit()
    except Exception:
        _log.exception("Failed to write request log")
