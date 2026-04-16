"""Database-backed secret retrieval from system_config.

API keys and secrets are stored in system_config with key prefix 'secret_'.
Values are stored as plain strings in JSONB. Reads from DB on every call
so changes via admin dashboard take effect without restart.

Falls back to environment variable if not set in DB, allowing
migration from env-based config.
"""

import os

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.thinktank.models.config_table import SystemConfig


async def get_secret(session: AsyncSession, name: str) -> str | None:
    """Read a secret from system_config, falling back to env var.

    Lookup order:
    1. system_config row with key = 'secret_{name}' (e.g. 'secret_anthropic_api_key')
    2. Environment variable with uppercase name (e.g. 'ANTHROPIC_API_KEY')

    Args:
        session: Active database session.
        name: Secret name in lowercase (e.g. 'anthropic_api_key').

    Returns:
        The secret value, or None if not found in either location.
    """
    db_key = f"secret_{name}"
    result = await session.execute(
        select(SystemConfig.value).where(SystemConfig.key == db_key)
    )
    row = result.scalar_one_or_none()
    if row is not None:
        # JSONB can hold either a plain string ("sk-ant-...") or a dict
        # wrapper ({"value": "sk-ant-..."}) depending on how the row was
        # written. str(row) on a dict produces a literal Python repr like
        # "{'value': 'sk-...'}" which is garbage when passed as an API key.
        if isinstance(row, dict):
            raw = row.get("value")
        elif isinstance(row, str):
            raw = row
        else:
            raw = None
        if raw:
            return raw

    # Fallback to environment variable. Also reached when the DB row
    # exists but holds an empty string / empty dict -- we don't want a
    # placeholder config row to shadow a real env var.
    return os.environ.get(name.upper()) or None
