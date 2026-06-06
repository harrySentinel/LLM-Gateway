import os

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool


class Base(DeclarativeBase):
    pass


_engine = None
_session_factory = None


def init() -> None:
    global _engine, _session_factory
    url = os.environ["DATABASE_URL"]

    # Supabase exposes two pooling modes:
    #
    #   Port 5432 — direct connection or session pooler.
    #     SQLAlchemy's own pool works correctly here; keep pool_size / max_overflow.
    #
    #   Port 6543 — transaction pooler (PgBouncer in transaction mode).
    #     Two problems arise with a conventional pool on top of PgBouncer:
    #
    #     1. Double-pooling: SQLAlchemy holds connections open in its own pool,
    #        but PgBouncer assigns a different physical connection on each
    #        transaction. The pool becomes useless and wastes resources.
    #        Fix: NullPool — SQLAlchemy opens/closes on each request and lets
    #        PgBouncer do the actual pooling.
    #
    #     2. Prepared statements: asyncpg caches prepared statements per
    #        connection. PgBouncer does NOT preserve this cache across the
    #        physical connections it hands out, so asyncpg tries to execute a
    #        prepared statement that doesn't exist on the new connection →
    #        "prepared statement ... does not exist" error.
    #        Fix: statement_cache_size=0 disables asyncpg's prepared-statement
    #        cache entirely. Safe for both pooler and direct connections.
    #
    using_pgbouncer = ":6543" in url

    connect_args = {
        "statement_cache_size": 0,  # safe for all modes; required for pgbouncer
    }

    if using_pgbouncer:
        _engine = create_async_engine(
            url,
            poolclass=NullPool,       # hand pooling responsibility to PgBouncer
            pool_pre_ping=False,      # meaningless with NullPool (no pool to ping)
            echo=False,
            connect_args=connect_args,
        )
    else:
        _engine = create_async_engine(
            url,
            pool_size=10,        # steady-state connections kept open
            max_overflow=20,     # extra connections allowed under burst load
            pool_pre_ping=True,  # discard stale connections before use
            echo=False,
            connect_args=connect_args,
        )

    _session_factory = async_sessionmaker(
        _engine, class_=AsyncSession, expire_on_commit=False
    )


async def create_tables() -> None:
    async with _engine.begin() as conn:
        # Creates any table that doesn't exist yet (api_keys on first run).
        # Does NOT alter existing tables — schema changes handled below.
        await conn.run_sync(Base.metadata.create_all)

        # Idempotent column migrations — IF NOT EXISTS is a no-op on repeat startups.
        await conn.execute(text(
            "ALTER TABLE request_logs "
            "ADD COLUMN IF NOT EXISTS api_key_id INTEGER "
            "REFERENCES api_keys(id) ON DELETE SET NULL"
        ))
        await conn.execute(text(
            "ALTER TABLE request_logs "
            "ADD COLUMN IF NOT EXISTS fallback_used BOOLEAN NOT NULL DEFAULT FALSE"
        ))
        await conn.execute(text(
            "ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS user_id TEXT"
        ))
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS provider_keys (
                id SERIAL PRIMARY KEY,
                user_id TEXT NOT NULL,
                provider VARCHAR(50) NOT NULL,
                encrypted_key TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL,
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                UNIQUE (user_id, provider)
            )
        """))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_provider_keys_user_id ON provider_keys (user_id)"
        ))

        # Indexes — IF NOT EXISTS makes these safe to run on every startup.
        for ddl in [
            "CREATE INDEX IF NOT EXISTS ix_request_logs_timestamp "
            "ON request_logs (timestamp)",

            "CREATE INDEX IF NOT EXISTS ix_request_logs_provider "
            "ON request_logs (provider)",

            "CREATE INDEX IF NOT EXISTS ix_request_logs_status "
            "ON request_logs (status)",

            "CREATE INDEX IF NOT EXISTS ix_request_logs_api_key_id "
            "ON request_logs (api_key_id)",

            "CREATE INDEX IF NOT EXISTS ix_request_logs_api_key_timestamp "
            "ON request_logs (api_key_id, timestamp)",

            "CREATE INDEX IF NOT EXISTS ix_api_keys_user_id ON api_keys (user_id)",
        ]:
            await conn.execute(text(ddl))


async def close() -> None:
    if _engine:
        await _engine.dispose()


def get_session() -> AsyncSession:
    if _session_factory is None:
        raise RuntimeError("DB not initialized — was lifespan called?")
    return _session_factory()
