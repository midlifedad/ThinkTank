"""Dashboard router with HTMX partial endpoints.

Provides the main admin dashboard page and auto-refreshing widget partials
for queue depth, error log, source health, GPU status, rate limits, and cost tracking.
"""

from fastapi import APIRouter, Depends, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from thinktank.admin.dependencies import get_session, get_templates

router = APIRouter(prefix="/admin", tags=["dashboard"])
templates = get_templates()


@router.get("/")
async def dashboard(request: Request):
    """Render the main admin dashboard page with HTMX widget placeholders."""
    return templates.TemplateResponse(request, "dashboard.html")


@router.get("/partials/queue-depth")
async def queue_depth_partial(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """HTML fragment: job counts grouped by job_type and status."""
    result = await session.execute(
        text("SELECT job_type, status, COUNT(*) AS cnt FROM jobs GROUP BY job_type, status ORDER BY job_type, status")
    )
    rows = result.fetchall()

    # Pivot into {job_type: {status: count}}
    pivot: dict[str, dict[str, int]] = {}
    for row in rows:
        job_type, status, cnt = row[0], row[1], row[2]
        if job_type not in pivot:
            pivot[job_type] = {}
        pivot[job_type][status] = cnt

    return templates.TemplateResponse(
        request, "partials/queue_depth.html", {"pivot": pivot},
    )


@router.get("/partials/error-log")
async def error_log_partial(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """HTML fragment: last 20 failed jobs with error details."""
    result = await session.execute(
        text(
            "SELECT job_type, error, error_category, created_at "
            "FROM jobs WHERE status = 'failed' "
            "ORDER BY created_at DESC LIMIT 20"
        )
    )
    rows = result.fetchall()
    errors = [
        {
            "job_type": r[0],
            "error": (r[1] or "")[:100],
            "error_category": r[2] or "unknown",
            "created_at": r[3],
        }
        for r in rows
    ]
    return templates.TemplateResponse(
        request, "partials/error_log.html", {"errors": errors},
    )


@router.get("/partials/source-health")
async def source_health_partial(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """HTML fragment: source counts by status."""
    total_result = await session.execute(text("SELECT COUNT(*) FROM sources"))
    total = total_result.scalar() or 0

    approved_result = await session.execute(
        text("SELECT COUNT(*) FROM sources WHERE approval_status = 'approved'")
    )
    approved = approved_result.scalar() or 0

    errored_result = await session.execute(
        text("SELECT COUNT(*) FROM sources WHERE error_count > 0")
    )
    errored = errored_result.scalar() or 0

    inactive_result = await session.execute(
        text("SELECT COUNT(*) FROM sources WHERE active = false")
    )
    inactive = inactive_result.scalar() or 0

    return templates.TemplateResponse(
        request, "partials/source_health.html",
        {"total": total, "approved": approved, "errored": errored, "inactive": inactive},
    )


@router.get("/partials/gpu-status")
async def gpu_status_partial(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """HTML fragment: GPU scaling status from system_config."""
    result = await session.execute(
        text("SELECT value FROM system_config WHERE key = 'gpu_service_status'")
    )
    row = result.fetchone()
    if row:
        gpu_data = row[0]
        status = gpu_data.get("status", "unknown") if isinstance(gpu_data, dict) else str(gpu_data)
        last_scale = gpu_data.get("last_scale_event") if isinstance(gpu_data, dict) else None
    else:
        status = "unknown"
        last_scale = None

    return templates.TemplateResponse(
        request, "partials/gpu_status.html",
        {"status": status, "last_scale": last_scale},
    )


@router.get("/partials/rate-limits")
async def rate_limits_partial(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """HTML fragment: rate limit gauges per API."""
    apis = {
        "youtube": 200,
        "podcastindex": 500,
        "anthropic": 50,
    }

    # Try to load custom limits from system_config
    config_result = await session.execute(
        text("SELECT value FROM system_config WHERE key = 'rate_limits'")
    )
    config_row = config_result.fetchone()
    if config_row and isinstance(config_row[0], dict):
        for api, limit in config_row[0].items():
            if api in apis and isinstance(limit, int):
                apis[api] = limit

    gauges = []
    for api_name, limit in apis.items():
        usage_result = await session.execute(
            text(
                "SELECT COUNT(*) FROM rate_limit_usage "
                "WHERE api_name = :api AND called_at > LOCALTIMESTAMP - INTERVAL '1 hour'"
            ),
            {"api": api_name},
        )
        current = usage_result.scalar() or 0
        pct = (current / limit * 100) if limit > 0 else 0
        if pct < 50:
            color = "green"
        elif pct < 80:
            color = "yellow"
        else:
            color = "red"
        gauges.append({
            "api_name": api_name,
            "current": current,
            "limit": limit,
            "pct": min(pct, 100),
            "color": color,
        })

    return templates.TemplateResponse(
        request, "partials/rate_limits.html", {"gauges": gauges},
    )


@router.get("/partials/cost-tracker")
async def cost_tracker_partial(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """HTML fragment: API costs in the last 24 hours."""
    result = await session.execute(
        text(
            "SELECT api_name, SUM(call_count), SUM(estimated_cost_usd) "
            "FROM api_usage "
            "WHERE period_start > LOCALTIMESTAMP - INTERVAL '24 hours' "
            "GROUP BY api_name"
        )
    )
    rows = result.fetchall()
    costs = [
        {
            "api_name": r[0],
            "call_count": r[1] or 0,
            "cost_usd": float(r[2]) if r[2] else 0.0,
        }
        for r in rows
    ]
    return templates.TemplateResponse(
        request, "partials/cost_tracker.html", {"costs": costs},
    )
