from datetime import date, datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import and_, case, func, select, text as sa_text

from app.db.engine import get_session
from app.db.models import ApiKey, RequestLog

dashboard_router = APIRouter()


# ── Response models ──────────────────────────────────────────────────────────

class Totals(BaseModel):
    requests: int
    cost_usd: float
    avg_latency_ms: float
    success_rate: float          # 0.0 – 1.0


class ProviderStat(BaseModel):
    provider: str
    requests: int
    cost_usd: float
    avg_latency_ms: float
    success_rate: float


class ApiKeyStat(BaseModel):
    api_key_id: int | None
    api_key_name: str | None
    requests: int
    cost_usd: float
    avg_latency_ms: float


class StatsResponse(BaseModel):
    period: dict[str, str]
    totals: Totals
    by_provider: list[ProviderStat]
    by_api_key: list[ApiKeyStat]


class LogItem(BaseModel):
    id: int
    timestamp: datetime
    model: str
    provider: str
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float
    latency_ms: int
    status: str
    api_key_id: int | None
    api_key_name: str | None
    fallback_used: bool


class LogsResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[LogItem]


# ── Helpers ──────────────────────────────────────────────────────────────────

def _start(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)


def _end(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, 23, 59, 59, 999999, tzinfo=timezone.utc)


def _today() -> date:
    return datetime.now(timezone.utc).date()


def _thirty_days_ago() -> date:
    return (_today() - timedelta(days=30))


# AVG(CASE WHEN status='success' THEN 1.0 ELSE 0.0 END) = success / total as float.
# COALESCE handles the case where the result set is empty (AVG of nothing = NULL).
def _success_rate_expr():
    return func.coalesce(
        func.avg(case((RequestLog.status == "success", 1.0), else_=0.0)),
        0.0,
    )


# ── GET /stats ────────────────────────────────────────────────────────────────

@dashboard_router.get("/stats", response_model=StatsResponse)
async def get_stats(
    from_date: Optional[date] = Query(default=None, description="Start date YYYY-MM-DD"),
    to_date:   Optional[date] = Query(default=None, description="End date YYYY-MM-DD"),
):
    if from_date is None:
        from_date = _thirty_days_ago()
    if to_date is None:
        to_date = _today()

    from_dt = _start(from_date)
    to_dt   = _end(to_date)
    time_filter = and_(RequestLog.timestamp >= from_dt, RequestLog.timestamp <= to_dt)

    async with get_session() as session:

        # ── Overall totals (single query) ────────────────────────────────────
        totals_row = (await session.execute(
            select(
                func.count().label("requests"),
                func.coalesce(func.sum(RequestLog.cost_usd),    0.0).label("cost_usd"),
                func.coalesce(func.avg(RequestLog.latency_ms),  0.0).label("avg_latency_ms"),
                _success_rate_expr().label("success_rate"),
            ).where(time_filter)
        )).mappings().one()

        # ── Breakdown by provider ────────────────────────────────────────────
        # GROUP BY is on a low-cardinality indexed column — fast even on large tables.
        provider_rows = (await session.execute(
            select(
                RequestLog.provider,
                func.count().label("requests"),
                func.coalesce(func.sum(RequestLog.cost_usd),   0.0).label("cost_usd"),
                func.coalesce(func.avg(RequestLog.latency_ms), 0.0).label("avg_latency_ms"),
                _success_rate_expr().label("success_rate"),
            )
            .where(time_filter)
            .group_by(RequestLog.provider)
            .order_by(func.count().desc())
        )).mappings().all()

        # ── Breakdown by api_key (outer join for the human-readable name) ────
        api_key_rows = (await session.execute(
            select(
                RequestLog.api_key_id,
                ApiKey.name.label("api_key_name"),
                func.count().label("requests"),
                func.coalesce(func.sum(RequestLog.cost_usd),   0.0).label("cost_usd"),
                func.coalesce(func.avg(RequestLog.latency_ms), 0.0).label("avg_latency_ms"),
            )
            .outerjoin(ApiKey, RequestLog.api_key_id == ApiKey.id)
            .where(time_filter)
            .group_by(RequestLog.api_key_id, ApiKey.name)
            .order_by(func.count().desc())
        )).mappings().all()

    def _f(v) -> float:
        return round(float(v), 6)

    return StatsResponse(
        period={"from": from_date.isoformat(), "to": to_date.isoformat()},
        totals=Totals(
            requests=totals_row["requests"],
            cost_usd=_f(totals_row["cost_usd"]),
            avg_latency_ms=round(float(totals_row["avg_latency_ms"]), 1),
            success_rate=round(float(totals_row["success_rate"]), 4),
        ),
        by_provider=[
            ProviderStat(
                provider=r["provider"],
                requests=r["requests"],
                cost_usd=_f(r["cost_usd"]),
                avg_latency_ms=round(float(r["avg_latency_ms"]), 1),
                success_rate=round(float(r["success_rate"]), 4),
            )
            for r in provider_rows
        ],
        by_api_key=[
            ApiKeyStat(
                api_key_id=r["api_key_id"],
                api_key_name=r["api_key_name"],
                requests=r["requests"],
                cost_usd=_f(r["cost_usd"]),
                avg_latency_ms=round(float(r["avg_latency_ms"]), 1),
            )
            for r in api_key_rows
        ],
    )


# ── GET /logs ─────────────────────────────────────────────────────────────────

@dashboard_router.get("/logs", response_model=LogsResponse)
async def get_logs(
    from_date:  Optional[date] = Query(default=None),
    to_date:    Optional[date] = Query(default=None),
    provider:   Optional[str] = None,
    status:     Optional[str] = None,
    api_key_id: Optional[int] = None,
    page:       int = Query(default=1,  ge=1),
    page_size:  int = Query(default=50, ge=1, le=500),
):
    if from_date is None:
        from_date = _thirty_days_ago()
    if to_date is None:
        to_date = _today()

    conditions = [
        RequestLog.timestamp >= _start(from_date),
        RequestLog.timestamp <= _end(to_date),
    ]
    if provider:
        conditions.append(RequestLog.provider == provider)
    if status:
        conditions.append(RequestLog.status == status)
    if api_key_id is not None:
        conditions.append(RequestLog.api_key_id == api_key_id)

    where_clause = and_(*conditions)
    offset = (page - 1) * page_size

    async with get_session() as session:

        # ── Count total matching rows (same filters, no LIMIT) ───────────────
        # Runs against the timestamp + filter indexes — does not full-scan.
        total = (await session.scalar(
            select(func.count()).select_from(RequestLog).where(where_clause)
        )) or 0

        # ── Paginated data with api_key name via outer join ──────────────────
        rows = (await session.execute(
            select(
                RequestLog.id,
                RequestLog.timestamp,
                RequestLog.model,
                RequestLog.provider,
                RequestLog.prompt_tokens,
                RequestLog.completion_tokens,
                RequestLog.cost_usd,
                RequestLog.latency_ms,
                RequestLog.status,
                RequestLog.api_key_id,
                RequestLog.fallback_used,
                ApiKey.name.label("api_key_name"),
            )
            .outerjoin(ApiKey, RequestLog.api_key_id == ApiKey.id)
            .where(where_clause)
            .order_by(RequestLog.timestamp.desc())
            .offset(offset)
            .limit(page_size)
        )).mappings().all()

    return LogsResponse(
        total=total,
        page=page,
        page_size=page_size,
        items=[
            LogItem(
                id=r["id"],
                timestamp=r["timestamp"],
                model=r["model"],
                provider=r["provider"],
                prompt_tokens=r["prompt_tokens"],
                completion_tokens=r["completion_tokens"],
                cost_usd=r["cost_usd"],
                latency_ms=r["latency_ms"],
                status=r["status"],
                api_key_id=r["api_key_id"],
                api_key_name=r["api_key_name"],
                fallback_used=r["fallback_used"],
            )
            for r in rows
        ],
    )


# ── GET /stats/daily ──────────────────────────────────────────────────────────

class DailyStat(BaseModel):
    date: str        # "YYYY-MM-DD"
    requests: int
    cost_usd: float
    avg_latency_ms: float


@dashboard_router.get("/stats/daily", response_model=list[DailyStat])
async def get_daily_stats(
    from_date: Optional[date] = Query(default=None),
    to_date:   Optional[date] = Query(default=None),
):
    """
    Daily aggregates for time-series charts.
    Uses date_trunc('day', ...) so each row represents one calendar day.
    The timestamp index makes this fast even on large tables.
    """
    if from_date is None:
        from_date = _thirty_days_ago()
    if to_date is None:
        to_date = _today()

    from_dt = _start(from_date)
    to_dt   = _end(to_date)
    day_col = func.date_trunc("day", RequestLog.timestamp)

    async with get_session() as session:
        rows = (await session.execute(
            select(
                day_col.label("day"),
                func.count().label("requests"),
                func.coalesce(func.sum(RequestLog.cost_usd),   0.0).label("cost_usd"),
                func.coalesce(func.avg(RequestLog.latency_ms), 0.0).label("avg_latency_ms"),
            )
            .where(and_(RequestLog.timestamp >= from_dt, RequestLog.timestamp <= to_dt))
            .group_by(day_col)
            .order_by(day_col)
        )).mappings().all()

    return [
        DailyStat(
            date=row["day"].strftime("%Y-%m-%d"),
            requests=row["requests"],
            cost_usd=round(float(row["cost_usd"]), 6),
            avg_latency_ms=round(float(row["avg_latency_ms"]), 1),
        )
        for row in rows
    ]
