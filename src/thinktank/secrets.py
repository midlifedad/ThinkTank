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
    if row is not None and row:
        # JSONB stores the value — could be a string directly
        return str(row) if not isinstance(row, str) else row

    # Fallback to environment variable
    return os.environ.get(name.upper()) or None
