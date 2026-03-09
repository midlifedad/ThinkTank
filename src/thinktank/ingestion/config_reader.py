"""Config reader for ingestion settings from system_config table.

Provides helpers to read system_config values and compute
effective per-source filter configuration with overrides.

Pure logic for get_source_filter_config (no I/O).
Async for get_config_value (DB read).
"""

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.thinktank.models.config_table import SystemConfig


async def get_config_value(session: AsyncSession, key: str, default: Any) -> Any:
    """Read a system_config value, falling back to default.

    Workers read config on each job execution so changes take
    effect without restart.

    Args:
        session: Active database session.
        key: Config key to look up (e.g., 'min_duration_seconds').
        default: Value to return if key not found.

    Returns:
        The JSONB value stored for the key, or default if not found.
    """
    result = await session.execute(
        select(SystemConfig.value).where(SystemConfig.key == key)
    )
    row = result.scalar_one_or_none()
    return row if row is not None else default


def get_source_filter_config(
    source_config: dict,
    global_min_duration: int,
    global_skip_patterns: list[str],
) -> tuple[int, list[str]]:
    """Compute effective filter config from source overrides and global defaults.

    Per-source JSONB config can override global min_duration and skip_title_patterns.

    Override keys:
        min_duration_override: int - replaces global min_duration
        skip_title_patterns_override: list[str] - replaces global patterns entirely
        additional_skip_patterns: list[str] - appended to global patterns

    Args:
        source_config: The source.config JSONB dict.
        global_min_duration: Global min_duration_seconds from system_config.
        global_skip_patterns: Global skip_title_patterns from system_config.

    Returns:
        Tuple of (effective_min_duration, effective_skip_patterns).
    """
    # Duration override
    if "min_duration_override" in source_config:
        effective_min_duration = source_config["min_duration_override"]
    else:
        effective_min_duration = global_min_duration

    # Skip patterns override
    if "skip_title_patterns_override" in source_config:
        effective_skip_patterns = source_config["skip_title_patterns_override"]
    else:
        effective_skip_patterns = list(global_skip_patterns)
        additional = source_config.get("additional_skip_patterns", [])
        if additional:
            effective_skip_patterns = effective_skip_patterns + additional

    return effective_min_duration, effective_skip_patterns
