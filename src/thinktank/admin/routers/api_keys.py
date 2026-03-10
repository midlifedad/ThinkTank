"""API keys management router for admin dashboard.

Allows viewing which API keys are configured (masked) and setting
new values. Keys are stored in system_config with 'secret_' prefix.
"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.thinktank.models.config_table import SystemConfig
from thinktank.admin.dependencies import get_session, get_templates

router = APIRouter(prefix="/admin/api-keys", tags=["api-keys"])
templates = get_templates()

# Keys that can be managed via the admin interface
MANAGED_KEYS = [
    {"name": "anthropic_api_key", "label": "Anthropic API Key", "required": True},
    {"name": "podcastindex_api_key", "label": "Podcast Index API Key", "required": False},
    {"name": "podcastindex_api_secret", "label": "Podcast Index API Secret", "required": False},
    {"name": "youtube_api_key", "label": "YouTube API Key", "required": False},
    {"name": "railway_api_key", "label": "Railway API Key", "required": False},
    {"name": "railway_gpu_service_id", "label": "Railway GPU Service ID", "required": False},
    {"name": "railway_environment_id", "label": "Railway Environment ID", "required": False},
]


def _mask_value(value: str | None) -> str:
    """Mask a secret value for display, showing only last 4 chars."""
    if not value:
        return ""
    if len(value) <= 8:
        return "****"
    return f"****{value[-4:]}"


@router.get("/")
async def api_keys_page(request: Request):
    """Render the API keys management page."""
    return templates.TemplateResponse(request, "api_keys.html")


@router.get("/partials/list")
async def api_keys_list_partial(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """HTML fragment: list of API keys with masked values and status."""
    keys_status = []
    for key_def in MANAGED_KEYS:
        db_key = f"secret_{key_def['name']}"
        result = await session.execute(
            select(SystemConfig.value, SystemConfig.updated_at).where(
                SystemConfig.key == db_key
            )
        )
        row = result.one_or_none()

        keys_status.append({
            "name": key_def["name"],
            "label": key_def["label"],
            "required": key_def["required"],
            "is_set": row is not None and bool(row[0]),
            "masked": _mask_value(str(row[0]) if row and row[0] else None),
            "updated_at": row[1] if row else None,
        })

    return templates.TemplateResponse(
        request, "partials/api_keys_list.html", {"keys": keys_status},
    )


@router.post("/set")
async def set_api_key(
    request: Request,
    session: AsyncSession = Depends(get_session),
    key_name: str = Form(...),
    key_value: str = Form(...),
):
    """Set or update an API key in system_config."""
    # Validate key name is in managed list
    valid_names = {k["name"] for k in MANAGED_KEYS}
    if key_name not in valid_names:
        return templates.TemplateResponse(
            request, "partials/api_keys_list.html",
            {"keys": [], "error": f"Unknown key: {key_name}"},
            status_code=400,
        )

    db_key = f"secret_{key_name}"
    now = datetime.now(UTC).replace(tzinfo=None)

    # Check if key already exists
    result = await session.execute(
        select(SystemConfig).where(SystemConfig.key == db_key)
    )
    existing = result.scalar_one_or_none()

    if existing:
        existing.value = key_value
        existing.set_by = "admin"
        existing.updated_at = now
    else:
        session.add(SystemConfig(
            key=db_key, value=key_value, set_by="admin", updated_at=now,
        ))

    await session.commit()

    # Re-render the list
    return await api_keys_list_partial(request, session)


@router.post("/delete/{key_name}")
async def delete_api_key(
    key_name: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Remove an API key from system_config."""
    valid_names = {k["name"] for k in MANAGED_KEYS}
    if key_name not in valid_names:
        return templates.TemplateResponse(
            request, "partials/api_keys_list.html",
            {"keys": [], "error": f"Unknown key: {key_name}"},
            status_code=400,
        )

    db_key = f"secret_{key_name}"
    result = await session.execute(
        select(SystemConfig).where(SystemConfig.key == db_key)
    )
    existing = result.scalar_one_or_none()
    if existing:
        await session.delete(existing)
        await session.commit()

    return await api_keys_list_partial(request, session)
