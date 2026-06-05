import json
import os
import time
import uuid

import httpx
from fastapi import HTTPException

from app.services import http_client

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"

_FINISH_REASON_MAP = {
    "STOP":       "stop",
    "MAX_TOKENS": "length",
    "SAFETY":     "content_filter",
}


def _api_key() -> str:
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise HTTPException(status_code=500, detail="GEMINI_API_KEY is not set")
    return key


def _to_gemini_payload(messages: list[dict]) -> dict:
    system_parts = []
    contents = []
    for msg in messages:
        role, content = msg["role"], msg["content"]
        if role == "system":
            system_parts.append({"text": content})
        else:
            gemini_role = "model" if role == "assistant" else "user"
            contents.append({"role": gemini_role, "parts": [{"text": content}]})
    payload: dict = {"contents": contents}
    if system_parts:
        payload["systemInstruction"] = {"parts": system_parts}
    return payload


def _to_openai_response(gemini_resp: dict, model: str) -> dict:
    candidate = gemini_resp["candidates"][0]
    text = candidate["content"]["parts"][0]["text"]
    finish_reason = _FINISH_REASON_MAP.get(candidate.get("finishReason", "STOP"), "stop")
    usage = gemini_resp.get("usageMetadata", {})
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": finish_reason,
            }
        ],
        "usage": {
            "prompt_tokens":     usage.get("promptTokenCount", 0),
            "completion_tokens": usage.get("candidatesTokenCount", 0),
            "total_tokens":      usage.get("totalTokenCount", 0),
        },
    }


async def chat_completion(model: str, messages: list[dict]) -> dict:
    url = f"{GEMINI_BASE_URL}/models/{model}:generateContent"
    payload = _to_gemini_payload(messages)
    try:
        resp = await http_client.get().post(url, params={"key": _api_key()}, json=payload)
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Gemini API timed out")
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"Could not reach Gemini API: {exc}")
    if resp.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"Gemini error {resp.status_code}: {resp.text[:400]}",
        )
    return _to_openai_response(resp.json(), model)


async def stream_chat(model: str, messages: list[dict], usage_out: dict):
    """
    Async generator — yields OpenAI-format SSE bytes.
    Gemini's streamGenerateContent sends chunks in Gemini format; we convert
    each one to an OpenAI chat.completion.chunk envelope on the fly.
    usage_out is updated from usageMetadata on each chunk; the last chunk
    holds the final cumulative totals.
    """
    url = f"{GEMINI_BASE_URL}/models/{model}:streamGenerateContent"
    payload = _to_gemini_payload(messages)
    completion_id = f"chatcmpl-{uuid.uuid4().hex}"
    created = int(time.time())

    try:
        async with http_client.get().stream(
            "POST", url, params={"key": _api_key(), "alt": "sse"}, json=payload
        ) as resp:
            if resp.status_code != 200:
                body = await resp.aread()
                raise HTTPException(
                    status_code=502,
                    detail=f"Gemini error {resp.status_code}: {body[:400].decode()}",
                )
            async for line in resp.aiter_lines():
                if not line or not line.startswith("data: "):
                    continue
                try:
                    chunk = json.loads(line[6:])
                except json.JSONDecodeError:
                    continue

                # usageMetadata accumulates across chunks; last value is final
                meta = chunk.get("usageMetadata", {})
                if meta:
                    usage_out["prompt_tokens"] = meta.get("promptTokenCount", 0)
                    usage_out["completion_tokens"] = meta.get("candidatesTokenCount", 0)

                candidates = chunk.get("candidates", [])
                if not candidates:
                    continue
                candidate = candidates[0]
                parts = candidate.get("content", {}).get("parts", [])
                text = parts[0].get("text", "") if parts else ""
                finish_reason = candidate.get("finishReason")

                openai_chunk = {
                    "id": completion_id,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": model,
                    "choices": [{
                        "index": 0,
                        "delta": {"content": text} if text else {},
                        "finish_reason": _FINISH_REASON_MAP.get(finish_reason) if finish_reason else None,
                    }],
                }
                yield f"data: {json.dumps(openai_chunk)}\n\n".encode()

        yield b"data: [DONE]\n\n"
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Gemini API timed out")
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"Could not reach Gemini API: {exc}")
