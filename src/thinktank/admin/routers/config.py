"""System configuration router for admin dashboard.

Provides a unified config page with rate limits editor and system config editor.
Rate limits and system settings are stored in the system_config table and take
effect immediately without restart.
"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from thinktank.admin.dependencies import get_session, get_templates
from thinktank.models.config_table import SystemConfig

router = APIRouter(prefix="/admin/config", tags=["config"])
templates = get_templates()

# Default rate limits per API (requests per hour)
DEFAULT_RATE_LIMITS = {
    "youtube": 200,
    "podcastindex": 500,
    "anthropic": 50,
}

# System config keys with labels and defaults
SYSTEM_CONFIG_KEYS = [
    {"key": "llm_timeout_hours", "label": "LLM Timeout (hours)", "default": 2},
    {"key": "backpressure_threshold", "label": "Backpressure Threshold (queue depth)", "default": 100},
    {"key": "stale_job_minutes", "label": "Stale Job Timeout (minutes)", "default": 30},
    {"key": "max_candidates_per_day", "label": "Max Candidates Per Day", "default": 50},
]


def _coerce_to_int(value, default: int) -> int:
    """Coerce a JSONB value to int, handling raw ints, dicts with 'value' key, etc."""
    if value is None:
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, dict):
        v = value.get("value", default)
        if isinstance(v, (int, float)):
            return int(v)
        try:
            return int(v)
        except (ValueError, TypeError):
            return default
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


@router.get("/")
async def config_page(request: Request):
    """Render the config landing page with HTMX-loaded editors."""
    return templates.TemplateResponse(request, "config.html")


@router.get("/partials/rate-limits")
async def rate_limits_editor_partial(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """HTML fragment: editable rate limits form."""
    # Load current limits from system_config
    result = await session.execute(select(SystemConfig.value).where(SystemConfig.key == "rate_limits"))
    row = result.scalar_one_or_none()

    limits = dict(DEFAULT_RATE_LIMITS)
    if row and isinstance(row, dict):
        for api, limit in row.items():
            if api in limits and isinstance(limit, (int, float)):
                limits[api] = int(limit)

    return templates.TemplateResponse(
        request,
        "partials/rate_limits_editor.html",
        {"limits": limits},
    )


@router.post("/rate-limits/save")
async def save_rate_limits(
    request: Request,
    session: AsyncSession = Depends(get_session),
    limit_youtube: int = Form(...),
    limit_podcastindex: int = Form(...),
    limit_anthropic: int = Form(...),
):
    """Save rate limit settings to system_config."""
    new_limits = {
        "youtube": max(1, limit_youtube),
        "podcastindex": max(1, limit_podcastindex),
        "anthropic": max(1, limit_anthropic),
    }

    now = datetime.now(UTC)

    # Upsert rate_limits row
    result = await session.execute(select(SystemConfig).where(SystemConfig.key == "rate_limits"))
    existing = result.scalar_one_or_none()

    if existing:
        existing.value = new_limits
        existing.set_by = "admin"
        existing.updated_at = now
    else:
        session.add(
            SystemConfig(
                key="rate_limits",
                value=new_limits,
                set_by="admin",
                updated_at=now,
            )
        )

    await session.commit()

    return templates.TemplateResponse(
        request,
        "partials/rate_limits_editor.html",
        {"limits": new_limits, "success": "Rate limits saved successfully."},
    )


@router.get("/partials/system-settings")
async def system_settings_editor_partial(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """HTML fragment: editable system settings form."""
    settings = []
    for cfg in SYSTEM_CONFIG_KEYS:
        result = await session.execute(select(SystemConfig.value).where(SystemConfig.key == cfg["key"]))
        raw_value = result.scalar_one_or_none()
        current_value = _coerce_to_int(raw_value, cfg["default"])

        settings.append(
            {
                "key": cfg["key"],
                "label": cfg["label"],
                "value": current_value,
            }
        )

    return templates.TemplateResponse(
        request,
        "partials/system_config_editor.html",
        {"settings": settings},
    )


@router.post("/system/save")
async def save_system_settings(
    request: Request,
    session: AsyncSession = Depends(get_session),
    llm_timeout_hours: int = Form(...),
    backpressure_threshold: int = Form(...),
    stale_job_minutes: int = Form(...),
    max_candidates_per_day: int = Form(...),
):
    """Save system config settings to system_config."""
    values = {
        "llm_timeout_hours": max(1, llm_timeout_hours),
        "backpressure_threshold": max(1, backpressure_threshold),
        "stale_job_minutes": max(1, stale_job_minutes),
        "max_candidates_per_day": max(1, max_candidates_per_day),
    }

    now = datetime.now(UTC)

    for key, val in values.items():
        result = await session.execute(select(SystemConfig).where(SystemConfig.key == key))
        existing = result.scalar_one_or_none()

        if existing:
            existing.value = val
            existing.set_by = "admin"
            existing.updated_at = now
        else:
            session.add(
                SystemConfig(
                    key=key,
                    value=val,
                    set_by="admin",
                    updated_at=now,
                )
            )

    await session.commit()

    # Re-render with updated values
    settings = [
        {"key": key, "label": next(c["label"] for c in SYSTEM_CONFIG_KEYS if c["key"] == key), "value": val}
        for key, val in values.items()
    ]

    return templates.TemplateResponse(
        request,
        "partials/system_config_editor.html",
        {"settings": settings, "success": "System settings saved successfully."},
    )
