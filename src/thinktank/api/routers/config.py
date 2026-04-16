"""System config read/write endpoints."""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from thinktank.api.dependencies import get_session
from thinktank.api.schemas import ConfigResponse, ConfigUpdate

from src.thinktank.models.config_table import SystemConfig

router = APIRouter(prefix="/api/config", tags=["config"])


# Keys with this prefix store credentials (API keys, tokens) and must never
# be returned by this public endpoint. See ADMIN-REVIEW CR-02.
_SECRET_KEY_PREFIX = "secret_"


@router.get("", response_model=list[ConfigResponse])
async def list_config(
    session: AsyncSession = Depends(get_session),
) -> list[ConfigResponse]:
    """List all system config entries, excluding credential rows.

    Rows whose key starts with ``secret_`` are filtered out so API keys
    stored via the admin dashboard (Anthropic, Railway, PodcastIndex, ...)
    are not exfiltrated in plaintext.
    """
    result = await session.execute(select(SystemConfig))
    configs = result.scalars().all()
    return [
        ConfigResponse.model_validate(c)
        for c in configs
        if not c.key.startswith(_SECRET_KEY_PREFIX)
    ]


@router.get("/{key}", response_model=ConfigResponse)
async def get_config(
    key: str,
    session: AsyncSession = Depends(get_session),
) -> ConfigResponse:
    """Get a specific config entry by key.

    Rejects any key prefixed with ``secret_`` with 403, regardless of
    whether the row exists (prevents using 404 vs 403 as an oracle for
    which secrets are populated).
    """
    if key.startswith(_SECRET_KEY_PREFIX):
        raise HTTPException(
            status_code=403,
            detail="secret keys are not exposed via this endpoint",
        )
    result = await session.execute(select(SystemConfig).where(SystemConfig.key == key))
    config = result.scalars().first()
    if config is None:
        raise HTTPException(status_code=404, detail="Config key not found")
    return ConfigResponse.model_validate(config)


@router.put("/{key}", response_model=ConfigResponse)
async def upsert_config(
    key: str,
    body: ConfigUpdate,
    session: AsyncSession = Depends(get_session),
) -> ConfigResponse:
    """Create or update a config entry."""
    now = datetime.now()  # noqa: DTZ005 -- timezone-naive per project convention
    stmt = insert(SystemConfig).values(
        key=key,
        value=body.value,
        set_by=body.set_by,
        updated_at=now,
    ).on_conflict_do_update(
        index_elements=["key"],
        set_=dict(
            value=body.value,
            set_by=body.set_by,
            updated_at=now,
        ),
    )
    await session.execute(stmt)
    await session.commit()

    # Fetch the upserted row
    result = await session.execute(select(SystemConfig).where(SystemConfig.key == key))
    config = result.scalars().first()
    return ConfigResponse.model_validate(config)
