"""Core job queue operations: claim, complete, fail.

Uses SELECT FOR UPDATE SKIP LOCKED for atomic job claiming.
Two-transaction pattern: claim is a fast lock-and-update, processing
happens in a separate transaction.

Spec reference: Sections 3.10, 6.1, 6.2.
"""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from thinktank.models.job import Job
from thinktank.queue.errors import ErrorCategory
from thinktank.queue.retry import calculate_backoff, get_max_attempts


def _now() -> datetime:
    """Return current UTC time as timezone-aware datetime.

    DATA-REVIEW H4 / migration 007: timestamp columns are TIMESTAMPTZ.
    """
    return datetime.now(UTC)


async def claim_job(
    session: AsyncSession,
    worker_id: str,
    job_types: list[str] | None = None,
) -> Job | None:
    """Claim the highest-priority eligible job atomically.

    Uses SELECT FOR UPDATE SKIP LOCKED to prevent two workers
    from ever claiming the same job. Returns None if no work available.

    Priority ordering: lowest number = highest priority.
    scheduled_at ordering: NULL first (immediately eligible), then ascending.
    """
    now = _now()

    stmt = (
        select(Job)
        .where(
            Job.status.in_(["pending", "retrying"]),
            or_(Job.scheduled_at.is_(None), Job.scheduled_at <= now),
        )
        .order_by(Job.priority.asc(), Job.scheduled_at.asc().nulls_first())
        .with_for_update(skip_locked=True)
        .limit(1)
    )

    if job_types is not None:
        stmt = stmt.where(Job.job_type.in_(job_types))

    result = await session.execute(stmt)
    job = result.scalar_one_or_none()

    if job is None:
        return None

    # Update to running in the same transaction
    job.status = "running"
    job.worker_id = worker_id
    job.started_at = _now()
    job.attempts += 1
    await session.commit()  # Release the FOR UPDATE lock immediately

    return job


async def complete_job(session: AsyncSession, job_id: uuid.UUID) -> None:
    """Mark a job as successfully completed.

    Clears error fields and sets completed_at timestamp.
    """
    stmt = (
        update(Job)
        .where(Job.id == job_id)
        .values(
            status="done",
            completed_at=_now(),
            error=None,
            error_category=None,
        )
    )
    await session.execute(stmt)
    await session.commit()


async def fail_job(
    session: AsyncSession,
    job_id: uuid.UUID,
    error_msg: str,
    error_category: ErrorCategory,
    max_attempts: int | None = None,
    retry_after_seconds: int | None = None,
) -> None:
    """Mark a job as failed. Retry with backoff if under max_attempts.

    If max_attempts not provided, uses per-type limit from retry.py.
    Retryable: status='retrying', scheduled_at set to now + backoff.
    Terminal: status='failed', completed_at set.

    If retry_after_seconds is provided (e.g. from a 429 Retry-After header),
    it takes precedence over our exponential backoff when scheduling the
    retry, so we honor the upstream's requested delay.
    """
    job = await session.get(Job, job_id)
    if job is None:
        return

    if max_attempts is None:
        max_attempts = get_max_attempts(job.job_type)

    now = _now()

    if job.attempts < max_attempts:
        # Retry with exponential backoff, unless upstream told us otherwise.
        job.status = "retrying"
        if retry_after_seconds is not None:
            job.scheduled_at = now + timedelta(seconds=retry_after_seconds)
        else:
            job.scheduled_at = now + calculate_backoff(job.attempts)
        job.worker_id = None
    else:
        # Terminal failure
        job.status = "failed"
        job.completed_at = now

    job.error = error_msg
    job.error_category = error_category.value
    job.last_error_at = now

    await session.commit()
