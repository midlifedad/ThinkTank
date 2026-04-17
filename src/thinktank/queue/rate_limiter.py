"""Sliding-window rate limiter via rate_limit_usage table.

Spec reference: Section 3.13 (rate_limit_usage), Section 5.8.
Coordinates rate limiting across concurrent workers using PostgreSQL
as the shared coordination point.
"""

from sqlalchemy import bindparam, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from thinktank.models.config_table import SystemConfig
from thinktank.models.rate_limit import RateLimitUsage


async def get_rate_limit_config(session: AsyncSession, api_name: str) -> int | None:
    """Read the configured rate limit for an API from system_config.

    Queries SystemConfig WHERE key == '{api_name}_calls_per_hour'.

    Args:
        session: Async database session.
        api_name: The API identifier (e.g., 'podcastindex', 'youtube').

    Returns:
        The integer limit, or None if no config exists (fail-open).
    """
    config_key = f"{api_name}_calls_per_hour"
    stmt = select(SystemConfig.value).where(SystemConfig.key == config_key)
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()

    if row is None:
        return None

    # Handle JSONB: could be {"value": 100} or raw int/float
    if isinstance(row, dict):
        return int(row.get("value", 0))
    return int(row)


async def check_and_acquire_rate_limit(
    session: AsyncSession,
    api_name: str,
    worker_id: str,
    window_minutes: int = 60,
) -> bool:
    """Check rate limit and acquire a slot if available.

    Uses a sliding window over rate_limit_usage to count recent API calls.
    If under the configured limit, inserts a new usage row and returns True.
    If at or over the limit, returns False (caller should back off).
    If no limit is configured, returns True (fail-open).

    Args:
        session: Async database session.
        api_name: The API identifier (e.g., 'podcastindex', 'youtube').
        worker_id: The worker acquiring the slot.
        window_minutes: Sliding window size in minutes (default 60).

    Returns:
        True if the call can proceed, False if rate-limited.
    """
    # Serialize concurrent rate-limit checks for the same API via a
    # transaction-scoped advisory lock. Without this, count+insert is a
    # TOCTOU race (INTEGRATIONS-REVIEW H-01): N concurrent callers all
    # see the same count, pass the limit check, and insert.
    # The lock releases automatically at commit/rollback.
    lock_key = hash(api_name) & 0x7FFFFFFF
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:k)"), {"k": lock_key}
    )

    # Use PG's LOCALTIMESTAMP for cutoff to match server-default NOW()
    # on the called_at column (both TIMESTAMP WITHOUT TIME ZONE).
    # This avoids timezone mismatches between Python UTC and PG local time.
    count_stmt = text(
        "SELECT COUNT(*) FROM rate_limit_usage "
        "WHERE api_name = :api_name "
        "AND called_at > LOCALTIMESTAMP - MAKE_INTERVAL(mins => :window_minutes)"
    )
    count_result = await session.execute(
        count_stmt, {"api_name": api_name, "window_minutes": window_minutes}
    )
    current_count = count_result.scalar_one()

    # Get configured limit
    limit = await get_rate_limit_config(session, api_name)

    if limit is None:
        # No config = no limit (fail-open)
        return True

    if current_count >= limit:
        return False

    # Record the call
    usage = RateLimitUsage(
        api_name=api_name,
        worker_id=worker_id,
    )
    session.add(usage)
    await session.flush()
    return True
