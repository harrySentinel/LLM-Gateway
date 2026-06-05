import httpx

_client: httpx.AsyncClient | None = None


def init() -> None:
    global _client
    _client = httpx.AsyncClient(
        timeout=httpx.Timeout(connect=5.0, read=60.0, write=10.0, pool=5.0),
        limits=httpx.Limits(
            max_connections=100,        # hard cap on open sockets across all hosts
            max_keepalive_connections=20,  # idle sockets kept warm (~10 per provider)
            keepalive_expiry=30.0,      # drop idle sockets after 30 s
        ),
    )


async def close() -> None:
    global _client
    if _client:
        await _client.aclose()


def get() -> httpx.AsyncClient:
    if _client is None:
        raise RuntimeError("HTTP client not initialized — was lifespan called?")
    return _client
