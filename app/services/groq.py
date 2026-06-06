import json
import os

import httpx
from fastapi import HTTPException

from app.services import http_client

GROQ_BASE_URL = "https://api.groq.com/openai/v1"


def _api_key(user_key: str | None = None) -> str:
    key = user_key or os.environ.get("GROQ_API_KEY", "")
    if not key:
        raise HTTPException(
            status_code=400,
            detail="Groq API key not configured. Add it in Provider Keys.",
        )
    return key


async def chat_completion(model: str, messages: list[dict], user_key: str | None = None) -> dict:
    try:
        resp = await http_client.get().post(
            f"{GROQ_BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {_api_key(user_key)}"},
            json={"model": model, "messages": messages},
        )
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Groq API timed out")
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"Could not reach Groq API: {exc}")

    if resp.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"Groq error {resp.status_code}: {resp.text[:400]}",
        )
    return resp.json()


async def stream_chat(model: str, messages: list[dict], usage_out: dict, user_key: str | None = None):
    """
    Async generator — yields raw SSE bytes for StreamingResponse.
    Populates usage_out with prompt_tokens / completion_tokens from the
    final chunk (requires stream_options.include_usage=true).
    """
    payload = {
        "model": model,
        "messages": messages,
        "stream": True,
        "stream_options": {"include_usage": True},  # last chunk carries usage stats
    }
    try:
        async with http_client.get().stream(
            "POST",
            f"{GROQ_BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {_api_key(user_key)}"},
            json=payload,
        ) as resp:
            if resp.status_code != 200:
                body = await resp.aread()
                raise HTTPException(
                    status_code=502,
                    detail=f"Groq error {resp.status_code}: {body[:400].decode()}",
                )
            async for line in resp.aiter_lines():
                if not line:
                    continue
                if not line.startswith("data: "):
                    continue
                data_str = line[6:]
                if data_str.strip() == "[DONE]":
                    yield b"data: [DONE]\n\n"
                    return
                try:
                    chunk = json.loads(data_str)
                    if chunk.get("usage"):
                        usage_out["prompt_tokens"] = chunk["usage"].get("prompt_tokens", 0)
                        usage_out["completion_tokens"] = chunk["usage"].get("completion_tokens", 0)
                except json.JSONDecodeError:
                    pass
                yield f"{line}\n\n".encode()
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Groq API timed out")
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"Could not reach Groq API: {exc}")
