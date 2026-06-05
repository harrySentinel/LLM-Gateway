import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

load_dotenv()  # must run before any module reads os.environ

from app.api.routes.chat import api_router            # noqa: E402
from app.api.routes.dashboard import dashboard_router  # noqa: E402
from app.api.routes.keys import keys_router            # noqa: E402
from app.db import engine as db_engine                 # noqa: E402
from app.db import models as _models                   # noqa: F401,E402  registers tables with Base
from app.services import http_client                   # noqa: E402


@asynccontextmanager
async def lifespan(app: FastAPI):
    http_client.init()
    db_engine.init()
    await db_engine.create_tables()
    yield
    await http_client.close()
    await db_engine.close()


app = FastAPI(title="LLM Gateway", version="0.1.0", lifespan=lifespan)

# CORS — configure via ALLOWED_ORIGINS env var (comma-separated).
# CORS_ORIGINS is accepted as a legacy alias so existing .env files keep working.
# Render / production: set ALLOWED_ORIGINS=https://your-dashboard.vercel.app
_raw_origins = (
    os.environ.get("ALLOWED_ORIGINS")
    or os.environ.get("CORS_ORIGINS")
    or "http://localhost:3000"
)
_origins = [o.strip() for o in _raw_origins.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=True,  # required — frontend sends Authorization header
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router,       prefix="/v1")
app.include_router(keys_router,      prefix="/v1")
app.include_router(dashboard_router, prefix="/v1")


@app.get("/health")
async def health():
    return {"status": "ok"}
