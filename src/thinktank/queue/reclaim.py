"""Stale job reclamation.

Spec reference: Section 6.3.
Reclaims jobs stuck in 'running' status beyond the configured timeout.
Runs periodically in the worker event loop (not as a jobs-table job).
"""

from __future__ import annotations

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from thinktank.models.config_table import SystemConfig

# Default stale timeout when system_config is missing
_DEFAULT_STALE_TIMEOUT_MINUTES = 30


async def _get_stale_timeout(session: AsyncSession) -> int:
    """Read stale_job_timeout_minutes from system_config.

    Returns:
        Timeout in minutes, or _DEFAULT_STALE_TIMEOUT_MINUTES if not configured.
    """
    stmt = select(SystemConfig.value).where(SystemConfig.key == "stale_job_timeout_minutes")
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()

    if row is None:
        return _DEFAULT_STALE_TIMEOUT_MINUTES

    # Handle JSONB: could be {"value": 30} or raw int
    if isinstance(row, dict):
        return int(row.get("value", _DEFAULT_STALE_TIMEOUT_MINUTES))
    return int(row)


async def reclaim_stale_jobs(session: AsyncSession) -> list[dict]:
    """Reclaim jobs stuck in 'running' state beyond the timeout.

    Uses a parameterized approach: first reads the config value,
    then uses it as a bind parameter in the bulk UPDATE.

    For each stale job:
    - If attempts + 1 >= max_attempts: set status='failed', completed_at=LOCALTIMESTAMP
    - Otherwise: set status='retrying', scheduled_at with exponential backoff

    All reclaimed jobs get:
    - worker_id = NULL (release worker claim)
    - attempts = attempts + 1
    - error = 'Reclaimed: exceeded stale_job_timeout_minutes'
    - error_category = 'worker_timeout'
    - last_error_at = LOCALTIMESTAMP

    Args:
        session: Async database session.

    Returns:
        List of dicts with info about reclaimed jobs
        (id, job_type, worker_id, attempts, max_attempts).
    """
    timeout_minutes = await _get_stale_timeout(session)

    # Use raw SQL for this bulk operation (per research recommendation).
    # Uses LOCALTIMESTAMP for timezone consistency with TIMESTAMP WITHOUT TIME ZONE columns.
    stmt = text("""
        UPDATE jobs
        SET status = CASE
                WHEN attempts + 1 >= max_attempts THEN 'failed'
                ELSE 'retrying'
            END,
            worker_id = NULL,
            attempts = attempts + 1,
            error = 'Reclaimed: exceeded stale_job_timeout_minutes',
            error_category = 'worker_timeout',
            last_error_at = LOCALTIMESTAMP,
            scheduled_at = CASE
                WHEN attempts + 1 >= max_attempts THEN NULL
                ELSE LOCALTIMESTAMP + (POWER(2, attempts + 1) * INTERVAL '1 minute')
            END,
            completed_at = CASE
                WHEN attempts + 1 >= max_attempts THEN LOCALTIMESTAMP
                ELSE NULL
            END
        WHERE status = 'running'
          AND started_at < LOCALTIMESTAMP - MAKE_INTERVAL(mins => :timeout_minutes)
        RETURNING id, job_type, worker_id, attempts, max_attempts
    """)

    result = await session.execute(stmt, {"timeout_minutes": timeout_minutes})
    reclaimed = [dict(row._mapping) for row in result.fetchall()]
    await session.flush()
    return reclaimed
