import logging

from fastapi import HTTPException
from tenacity import AsyncRetrying, retry_if_exception, stop_after_attempt, wait_exponential

from app.services import gemini, groq

_log = logging.getLogger(__name__)

# ── Primary routing ──────────────────────────────────────────────────────────
# Each entry: (name, predicate, provider_module). First match wins.
_ROUTES = [
    ("gemini", lambda m: m.startswith("gemini-"), gemini),
]
_DEFAULT = ("groq", groq)

# ── Fallback table ───────────────────────────────────────────────────────────
# When the primary fails, try this instead.
# Format: primary_name → (fallback_name, fallback_module, fallback_model)
# The fallback model is fixed because we're crossing provider boundaries —
# the client's original model name only makes sense on the original provider.
_FALLBACK_FOR: dict[str, tuple[str, object, str]] = {
    "groq":   ("gemini", gemini, "gemini-1.5-flash"),
    "gemini": ("groq",   groq,   "llama-3.3-70b-versatile"),
}

# ── Retry predicate ──────────────────────────────────────────────────────────
# Only retry on upstream failures. Do NOT retry on:
#   401/403 — invalid credentials (won't succeed on fallback)
#   400/422 — bad request (our fault, not the provider's)
#   500     — missing API key config (won't succeed anywhere)
def _is_retriable(exc: BaseException) -> bool:
    return isinstance(exc, HTTPException) and exc.status_code in (502, 504)


def resolve(model: str) -> tuple[str, object]:
    """Return (provider_name, provider_module) for a model string."""
    for name, predicate, provider in _ROUTES:
        if predicate(model):
            return name, provider
    return _DEFAULT


def _candidates(model: str) -> list[tuple[str, object, str]]:
    """Build ordered [(name, provider, model_to_use)] list."""
    primary_name, primary = resolve(model)
    ordered = [(primary_name, primary, model)]
    fb = _FALLBACK_FOR.get(primary_name)
    if fb:
        ordered.append(fb)
    return ordered


# ── Non-streaming failover ───────────────────────────────────────────────────

async def chat_completion(model: str, messages: list[dict], user_keys: dict | None = None) -> tuple[str, dict, bool]:
    """
    Returns (actual_provider_name, result_dict, fallback_used).
    Retries with exponential backoff on 502/504. Switches provider each attempt.
    """
    providers = _candidates(model)
    result: dict = {}
    final_idx = 0

    async for attempt in AsyncRetrying(
        stop=stop_after_attempt(len(providers)),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=4.0),
        retry=retry_if_exception(_is_retriable),
        reraise=True,
    ):
        with attempt:
            i = attempt.retry_state.attempt_number - 1
            name, provider, use_model = providers[i]
            if i > 0:
                _log.warning(
                    "Provider '%s' failed — failing over to '%s'",
                    providers[0][0], name,
                )
            result = await provider.chat_completion(use_model, messages, user_key=(user_keys or {}).get(name))
            final_idx = i

    return providers[final_idx][0], result, final_idx > 0


# ── Streaming failover ───────────────────────────────────────────────────────

async def _peek_stream(
    provider,
    model: str,
    messages: list[dict],
    usage_out: dict,
    user_key: str | None = None,
) -> tuple[bytes, object]:
    """
    Opens the provider stream and awaits exactly ONE chunk.

    This is the entire failover window for streaming. If the provider
    returns a non-200 or times out, the exception surfaces here — before
    any byte reaches the client — and tenacity can retry on the fallback.

    Once this function returns a chunk, we are committed. The chunk is
    already queued for the client's TCP buffer. Mid-stream failover is
    not attempted: there is no clean way to tell the client "ignore those
    bytes, start over from a different provider."
    """
    gen = provider.stream_chat(model, messages, usage_out, user_key=user_key)
    first_chunk = await gen.__anext__()
    return first_chunk, gen


async def stream_chat(
    model: str,
    messages: list[dict],
    usage_out: dict,
    meta_out: dict,
    user_keys: dict | None = None,
):
    """
    Async generator — yields SSE bytes with pre-stream failover.

    meta_out is populated with {"provider": str, "fallback_used": bool}
    BEFORE the first yield so the caller's finally block always has
    accurate values regardless of when it reads meta_out.
    """
    providers = _candidates(model)
    first_chunk: bytes = b""
    gen = None
    final_idx = 0

    async for attempt in AsyncRetrying(
        stop=stop_after_attempt(len(providers)),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=4.0),
        retry=retry_if_exception(_is_retriable),
        reraise=True,
    ):
        with attempt:
            i = attempt.retry_state.attempt_number - 1
            name, provider, use_model = providers[i]
            if i > 0:
                _log.warning(
                    "Provider '%s' failed before first token — failing over to '%s'",
                    providers[0][0], name,
                )
            first_chunk, gen = await _peek_stream(provider, use_model, messages, usage_out, (user_keys or {}).get(name))
            final_idx = i

    meta_out["provider"] = providers[final_idx][0]
    meta_out["fallback_used"] = final_idx > 0

    yield first_chunk
    async for chunk in gen:
        yield chunk
