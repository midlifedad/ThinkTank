"""Stale job reclamation.

Spec reference: Section 6.3.
Reclaims jobs stuck in 'running' status beyond the configured timeout.
Runs periodically in the worker event loop (not as a jobs-table job).
"""

from __future__ import annotations

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from thinktank.models.config_table import SystemConfig
from thinktank.queue.retry import calculate_backoff

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

    For each stale job:
    - If attempts + 1 >= max_attempts: set status='failed', completed_at=LOCALTIMESTAMP
    - Otherwise: set status='retrying', scheduled_at with exponential backoff
      from ``calculate_backoff`` (single source of truth with worker retry
      semantics, capped at 60 minutes).

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

    # Select stale candidates first so we can apply the Python-side backoff
    # formula per row (single source of truth with ``calculate_backoff``).
    # HANDLERS-REVIEW HI-06 (T6.3): previously used raw SQL POWER(2, attempts+1)
    # with no cap, scheduling some jobs many hours into the future.
    select_stmt = text("""
        SELECT id, job_type, worker_id, attempts, max_attempts
        FROM jobs
        WHERE status = 'running'
          AND started_at < LOCALTIMESTAMP - MAKE_INTERVAL(mins => :timeout_minutes)
        FOR UPDATE SKIP LOCKED
    """)
    candidates = (await session.execute(select_stmt, {"timeout_minutes": timeout_minutes})).fetchall()

    reclaimed: list[dict] = []
    update_retrying = text("""
        UPDATE jobs
        SET status = 'retrying',
            worker_id = NULL,
            attempts = :new_attempts,
            error = 'Reclaimed: exceeded stale_job_timeout_minutes',
            error_category = 'worker_timeout',
            last_error_at = LOCALTIMESTAMP,
            scheduled_at = LOCALTIMESTAMP + MAKE_INTERVAL(mins => :backoff_minutes),
            completed_at = NULL
        WHERE id = :id
    """)
    update_failed = text("""
        UPDATE jobs
        SET status = 'failed',
            worker_id = NULL,
            attempts = :new_attempts,
            error = 'Reclaimed: exceeded stale_job_timeout_minutes',
            error_category = 'worker_timeout',
            last_error_at = LOCALTIMESTAMP,
            scheduled_at = NULL,
            completed_at = LOCALTIMESTAMP
        WHERE id = :id
    """)

    for row in candidates:
        new_attempts = row.attempts + 1
        if new_attempts >= row.max_attempts:
            await session.execute(
                update_failed,
                {"id": row.id, "new_attempts": new_attempts},
            )
        else:
            backoff_minutes = int(calculate_backoff(new_attempts).total_seconds() // 60)
            await session.execute(
                update_retrying,
                {
                    "id": row.id,
                    "new_attempts": new_attempts,
                    "backoff_minutes": backoff_minutes,
                },
            )
        reclaimed.append(
            {
                "id": row.id,
                "job_type": row.job_type,
                "worker_id": row.worker_id,
                "attempts": row.attempts,
                "max_attempts": row.max_attempts,
            }
        )

    await session.flush()
    return reclaimed
