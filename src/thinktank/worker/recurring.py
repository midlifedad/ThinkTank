"""Recurring-task executor: makes the admin scheduler actually run.

Source: ARCH-REVIEW 2026-05-28 (A1). Phase 11 shipped a scheduler editor
(frequency + enable toggle persisted to ``system_config`` under
``scheduler_<key>``) but nothing ever consumed those configs -- recurring
tasks only ran when an operator clicked "Run Now". This module is the
missing executor: a worker background task that periodically checks each
job-typed entry in ``SCHEDULED_TASKS`` and enqueues a job when it is due.

Semantics (matching what the admin UI writes):
    - Missing config row  -> enabled at ``default_hours``, due immediately.
    - ``enabled: false``  -> never enqueued.
    - ``next_run_at``     -> due when now >= next_run_at; a missing/invalid
      value means due immediately.
    - After enqueueing, ``last_run_at``/``next_run_at`` are advanced in the
      same config row the UI displays.

Dedup: if a job of the same type is already pending/retrying, the tick is
skipped WITHOUT advancing the schedule -- the executor retries next tick
rather than stacking duplicate jobs behind a stuck queue.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from thinktank.models.config_table import SystemConfig
from thinktank.models.job import Job
from thinktank.queue.backpressure import get_queue_depth
from thinktank.queue.leader import LOCK_RECURRING_TASKS, try_advisory_xact_lock
from thinktank.queue.retry import get_max_attempts
from thinktank.queue.scheduled_tasks import SCHEDULED_TASKS

logger = structlog.get_logger(__name__)

# How often the executor wakes to check schedules (seconds).
CHECK_INTERVAL_SECONDS = 60


def _utcnow() -> datetime:
    """Timezone-aware UTC now, matching TIMESTAMPTZ columns."""
    return datetime.now(UTC)


def _parse_iso(value: object) -> datetime | None:
    """Parse an ISO datetime string from JSONB; None on missing/invalid."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    # Config rows written before TZ-awareness may lack tzinfo; assume UTC.
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _is_due(raw_config: object, now: datetime) -> bool:
    """Decide whether a scheduled task is due to run.

    Args:
        raw_config: The scheduler_<key> JSONB value (dict or None).
        now: Current time.

    Returns:
        True when the task should be enqueued this tick.
    """
    if not isinstance(raw_config, dict):
        # No config row yet: default-enabled, due immediately.
        return True
    if not raw_config.get("enabled", True):
        return False
    next_run_at = _parse_iso(raw_config.get("next_run_at"))
    if next_run_at is None:
        return True
    return now >= next_run_at


async def run_due_scheduled_tasks(session: AsyncSession) -> int:
    """Enqueue a job for every due, enabled, job-typed scheduled task.

    Returns:
        Number of jobs enqueued this tick.
    """
    now = _utcnow()
    enqueued = 0

    # A4: singleton tick. Two replicas ticking together would both read
    # next_run_at <= now and double-enqueue before either advances the
    # schedule. Loser skips this tick; its next tick sees the advanced
    # schedule and correctly no-ops.
    if not await try_advisory_xact_lock(session, LOCK_RECURRING_TASKS):
        return 0

    for task_def in SCHEDULED_TASKS:
        job_type = task_def["job_type"]
        if job_type is None:
            continue  # LLM tasks run via dedicated schedulers

        config_key = f"scheduler_{task_def['key']}"
        result = await session.execute(select(SystemConfig).where(SystemConfig.key == config_key))
        config_row = result.scalar_one_or_none()
        raw_config = config_row.value if config_row is not None else None

        if not _is_due(raw_config, now):
            continue

        # Dedup: don't stack a new job behind one that hasn't run yet.
        # Schedule is NOT advanced, so the next tick retries.
        if await get_queue_depth(session, job_type) > 0:
            logger.info("recurring_task_skipped_inflight", task=task_def["key"], job_type=job_type)
            continue

        session.add(
            Job(
                id=uuid.uuid4(),
                job_type=job_type,
                payload={"triggered_by": "recurring_scheduler"},
                priority=5,
                status="pending",
                attempts=0,
                max_attempts=get_max_attempts(job_type),
                created_at=now,
            )
        )

        frequency_hours = task_def["default_hours"]
        if isinstance(raw_config, dict):
            frequency_hours = raw_config.get("frequency_hours", frequency_hours)
        new_value = dict(raw_config) if isinstance(raw_config, dict) else {"enabled": True}
        new_value["frequency_hours"] = frequency_hours
        new_value["last_run_at"] = now.isoformat()
        new_value["next_run_at"] = (now + timedelta(hours=frequency_hours)).isoformat()

        if config_row is not None:
            config_row.value = new_value
            config_row.updated_at = now
        else:
            session.add(
                SystemConfig(
                    key=config_key,
                    value=new_value,
                    set_by="recurring_scheduler",
                    updated_at=now,
                )
            )

        enqueued += 1
        logger.info(
            "recurring_task_enqueued",
            task=task_def["key"],
            job_type=job_type,
            next_run_at=new_value["next_run_at"],
        )

    await session.commit()
    return enqueued


async def recurring_task_scheduler(
    session_factory,
    shutdown_event: asyncio.Event,
    check_interval: float = CHECK_INTERVAL_SECONDS,
) -> None:
    """Background task: periodically enqueue due scheduled tasks.

    Mirrors the other worker schedulers (reclaim, GPU scaling): runs until
    shutdown_event is set, logging and swallowing per-tick errors so one
    bad tick never kills the scheduler.
    """
    logger.info("recurring_task_scheduler_started", check_interval=check_interval)
    while not shutdown_event.is_set():
        try:
            async with session_factory() as session:
                await run_due_scheduled_tasks(session)
        except Exception:
            logger.exception("recurring_task_scheduler_tick_failed")

        try:
            await asyncio.wait_for(shutdown_event.wait(), timeout=check_interval)
        except TimeoutError:
            pass
