"""Dashboard router with HTMX partial endpoints.

Provides the main admin dashboard page and auto-refreshing widget partials
for queue depth, error log, source health, GPU status, rate limits, cost tracking,
health summary, kill switch, activity feed, and pending approvals.
"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from thinktank.admin.auth import require_admin
from thinktank.admin.dependencies import get_session, get_templates
from thinktank.models.config_table import SystemConfig


def _utcnow() -> datetime:
    """Timezone-aware UTC now, matching TIMESTAMPTZ columns (migration 007)."""
    return datetime.now(UTC)


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
        request,
        "partials/queue_depth.html",
        {"pivot": pivot},
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
        request,
        "partials/error_log.html",
        {"errors": errors},
    )


@router.get("/partials/source-health")
async def source_health_partial(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """HTML fragment: source counts by status."""
    total_result = await session.execute(text("SELECT COUNT(*) FROM sources"))
    total = total_result.scalar() or 0

    approved_result = await session.execute(text("SELECT COUNT(*) FROM sources WHERE approval_status = 'approved'"))
    approved = approved_result.scalar() or 0

    errored_result = await session.execute(text("SELECT COUNT(*) FROM sources WHERE error_count > 0"))
    errored = errored_result.scalar() or 0

    inactive_result = await session.execute(text("SELECT COUNT(*) FROM sources WHERE active = false"))
    inactive = inactive_result.scalar() or 0

    return templates.TemplateResponse(
        request,
        "partials/source_health.html",
        {"total": total, "approved": approved, "errored": errored, "inactive": inactive},
    )


@router.get("/partials/gpu-status")
async def gpu_status_partial(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """HTML fragment: GPU scaling status from system_config."""
    result = await session.execute(text("SELECT value FROM system_config WHERE key = 'gpu_service_status'"))
    row = result.fetchone()
    if row:
        gpu_data = row[0]
        status = gpu_data.get("status", "unknown") if isinstance(gpu_data, dict) else str(gpu_data)
        last_scale = gpu_data.get("last_scale_event") if isinstance(gpu_data, dict) else None
    else:
        status = "unknown"
        last_scale = None

    return templates.TemplateResponse(
        request,
        "partials/gpu_status.html",
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
    config_result = await session.execute(text("SELECT value FROM system_config WHERE key = 'rate_limits'"))
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
        gauges.append(
            {
                "api_name": api_name,
                "current": current,
                "limit": limit,
                "pct": min(pct, 100),
                "color": color,
            }
        )

    return templates.TemplateResponse(
        request,
        "partials/rate_limits.html",
        {"gauges": gauges},
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
        request,
        "partials/cost_tracker.html",
        {"costs": costs},
    )


# ---------------------------------------------------------------------------
# Morning briefing endpoints (Phase 8, Plan 01)
# ---------------------------------------------------------------------------


@router.get("/partials/health-summary")
async def health_summary_partial(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """HTML fragment: system health indicators (workers, DB, error rate)."""
    # Worker status from system_config
    result = await session.execute(select(SystemConfig.value).where(SystemConfig.key == "workers_active"))
    row = result.scalar_one_or_none()
    workers_active = True  # default to active if not set
    if row is not None:
        workers_active = bool(row) if not isinstance(row, dict) else bool(row.get("value", True))

    # DB connection check
    db_ok = True
    try:
        await session.execute(text("SELECT 1"))
    except Exception:
        db_ok = False

    # Error rate: failed jobs in the last hour
    error_result = await session.execute(
        text("SELECT COUNT(*) FROM jobs WHERE status = 'failed' AND created_at > NOW() - INTERVAL '1 hour'")
    )
    error_count = error_result.scalar() or 0

    return templates.TemplateResponse(
        request,
        "partials/health_summary.html",
        {
            "workers_active": workers_active,
            "db_ok": db_ok,
            "error_count": error_count,
        },
    )


@router.get("/partials/kill-switch")
async def kill_switch_partial(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """HTML fragment: kill switch toggle showing current worker state."""
    result = await session.execute(select(SystemConfig.value).where(SystemConfig.key == "workers_active"))
    row = result.scalar_one_or_none()
    workers_active = True  # default to active if not set
    if row is not None:
        workers_active = bool(row) if not isinstance(row, dict) else bool(row.get("value", True))

    return templates.TemplateResponse(
        request,
        "partials/kill_switch.html",
        {"workers_active": workers_active},
    )


@router.post("/kill-switch/toggle")
async def kill_switch_toggle(
    request: Request,
    session: AsyncSession = Depends(get_session),
    principal: str = Depends(require_admin),
):
    """Toggle the global kill switch (workers_active config) and re-render partial."""
    result = await session.execute(select(SystemConfig).where(SystemConfig.key == "workers_active"))
    config = result.scalar_one_or_none()

    if config is not None:
        # Flip the boolean value
        current = config.value
        if isinstance(current, dict):
            new_val = not bool(current.get("value", True))
        else:
            new_val = not bool(current)
        config.value = new_val
        config.set_by = principal
        config.updated_at = _utcnow()
    else:
        # Create with value False (turning off)
        config = SystemConfig(
            key="workers_active",
            value=False,
            set_by=principal,
            updated_at=_utcnow(),
        )
        session.add(config)

    await session.commit()

    # Re-read to get the committed state
    result = await session.execute(select(SystemConfig.value).where(SystemConfig.key == "workers_active"))
    row = result.scalar_one_or_none()
    workers_active = bool(row) if row is not None else False

    return templates.TemplateResponse(
        request,
        "partials/kill_switch.html",
        {"workers_active": workers_active},
    )


@router.get("/partials/activity-feed")
async def activity_feed_partial(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """HTML fragment: last 50 recent jobs sorted by activity time."""
    from thinktank.models.job import Job

    result = await session.execute(select(Job).where(Job.status != "pending").order_by(Job.created_at.desc()).limit(50))
    jobs = result.scalars().all()

    return templates.TemplateResponse(
        request,
        "partials/activity_feed.html",
        {"jobs": jobs},
    )


@router.get("/partials/pending-approvals")
async def pending_approvals_partial(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """HTML fragment: count of pending LLM reviews awaiting decision."""
    result = await session.execute(text("SELECT COUNT(*) FROM llm_reviews WHERE decision IS NULL"))
    pending_count = result.scalar() or 0

    return templates.TemplateResponse(
        request,
        "partials/pending_approvals.html",
        {"pending_count": pending_count},
    )
