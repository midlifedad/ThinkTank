"""Async worker loop: polls for jobs, dispatches to handlers, manages shutdown.

The main runtime that drives all job processing. Ties together claim,
retry, rate limiting, backpressure, kill switch, and reclamation into
a single cohesive worker process.

Spec reference: Sections 6.1, 6.2, 6.3.

Usage:
    from thinktank.worker.loop import worker_loop
    await worker_loop(session_factory)
"""

from __future__ import annotations

import asyncio
import os
import signal
import socket
from datetime import datetime

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from thinktank.handlers.registry import get_handler
from thinktank.http_utils import RateLimitedError
from thinktank.llm.escalation import escalate_timed_out_reviews
from thinktank.llm.scheduled import run_daily_digest, run_health_check, run_weekly_audit
from thinktank.llm.time_utils import seconds_until_next_monday_utc, seconds_until_next_utc_hour
from thinktank.models.job import Job
from thinktank.queue.backpressure import get_effective_priority
from thinktank.queue.claim import claim_job, complete_job, fail_job
from thinktank.queue.errors import ErrorCategory, categorize_error
from thinktank.queue.kill_switch import is_workers_active
from thinktank.queue.reclaim import reclaim_stale_jobs
from thinktank.queue.retry import get_max_attempts
from thinktank.scaling.railway import manage_gpu_scaling
from thinktank.worker.config import WorkerSettings, get_worker_settings

logger = structlog.get_logger(__name__)


def generate_worker_id(service_type: str) -> str:
    """Generate a unique worker ID from service type, hostname, and PID.

    Format: {service_type}-{hostname}-{pid}
    Example: cpu-worker-abc123-42

    Args:
        service_type: The worker service type ("cpu" or "gpu").

    Returns:
        A unique worker identifier string.
    """
    return f"{service_type}-{socket.gethostname()}-{os.getpid()}"


async def worker_loop(
    session_factory: async_sessionmaker[AsyncSession],
    settings: WorkerSettings | None = None,
    shutdown_event: asyncio.Event | None = None,
) -> None:
    """Main async worker loop. Polls for jobs, dispatches to handlers.

    Structure:
    1. Initialize shutdown_event, semaphore, active_tasks, worker_id
    2. Register SIGTERM/SIGINT signal handlers (if not provided externally)
    3. Start reclamation scheduler as background task
    4. Main loop: check kill switch, claim job, backpressure check, dispatch
    5. Graceful shutdown: cancel reclamation, wait for in-flight tasks

    Args:
        session_factory: Async session factory for database connections.
        settings: Worker configuration. Uses get_worker_settings() if None.
        shutdown_event: External shutdown event (for testing). Creates one if None.
    """
    if settings is None:
        settings = get_worker_settings()

    if shutdown_event is None:
        shutdown_event = asyncio.Event()

        # Register signal handlers for graceful shutdown (only when not testing)
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, shutdown_event.set)

    semaphore = asyncio.Semaphore(settings.max_concurrency)
    active_tasks: set[asyncio.Task] = set()  # type: ignore[type-arg]
    worker_id = generate_worker_id(settings.service_type)
    idle_count = 0

    logger.info(
        "worker_starting",
        worker_id=worker_id,
        service_type=settings.service_type,
        max_concurrency=settings.max_concurrency,
        poll_interval=settings.poll_interval,
        job_types=settings.job_types,
    )

    # Start reclamation scheduler
    reclaim_task = asyncio.create_task(
        _reclamation_scheduler(session_factory, settings.reclaim_interval, shutdown_event)
    )

    # Start GPU scaling scheduler
    gpu_scaling_task = asyncio.create_task(
        _gpu_scaling_scheduler(session_factory, settings.reclaim_interval, shutdown_event)
    )

    # Start LLM governance schedulers
    escalation_task = asyncio.create_task(
        _llm_timeout_escalation_scheduler(session_factory, 900, shutdown_event)
    )
    health_check_task = asyncio.create_task(
        _llm_health_check_scheduler(session_factory, 21600, shutdown_event)
    )
    digest_task = asyncio.create_task(
        _llm_digest_scheduler(session_factory, shutdown_event)
    )
    audit_task = asyncio.create_task(
        _llm_audit_scheduler(session_factory, shutdown_event)
    )

    try:
        while not shutdown_event.is_set():
            # Step 4a: Check kill switch
            try:
                async with session_factory() as session:
                    if not await is_workers_active(session):
                        logger.info("worker_paused", reason="kill_switch_active")
                        await _interruptible_sleep(settings.poll_interval, shutdown_event)
                        continue
            except Exception:
                logger.exception("kill_switch_check_failed")
                await _interruptible_sleep(settings.poll_interval, shutdown_event)
                continue

            # Step 4b: Claim job
            job: Job | None = None
            try:
                async with session_factory() as session:
                    job = await claim_job(session, worker_id, settings.job_types)
            except Exception:
                logger.exception("claim_failed")
                await _interruptible_sleep(settings.poll_interval, shutdown_event)
                continue

            if job is None:
                # No work available, back off
                idle_count += 1
                wait = min(
                    settings.poll_interval * (settings.idle_backoff_multiplier ** min(idle_count, 50)),
                    settings.max_idle_backoff,
                )
                await _interruptible_sleep(wait, shutdown_event)
                continue

            # Step 4c: Reset idle backoff on successful claim
            idle_count = 0

            logger.info(
                "job_claimed",
                job_id=str(job.id),
                job_type=job.job_type,
                priority=job.priority,
                worker_id=worker_id,
            )

            # Step 4d: Backpressure check
            try:
                async with session_factory() as session:
                    effective_priority = await get_effective_priority(session, job)
                    if effective_priority != job.priority:
                        logger.info(
                            "backpressure_demotion",
                            job_id=str(job.id),
                            job_type=job.job_type,
                            original_priority=job.priority,
                            effective_priority=effective_priority,
                        )
                        job.priority = effective_priority
                        # Merge the detached job into this session to persist priority
                        merged = await session.merge(job)
                        merged.priority = effective_priority
                        await session.commit()
            except Exception:
                logger.exception(
                    "backpressure_check_failed",
                    job_id=str(job.id),
                )
                # Non-fatal: continue with original priority

            # Step 4e: Dispatch
            await semaphore.acquire()
            task = asyncio.create_task(
                _process_job(session_factory, job, semaphore, worker_id)
            )
            active_tasks.add(task)
            task.add_done_callback(active_tasks.discard)

    finally:
        # Step 5: Graceful shutdown
        logger.info("worker_shutting_down", active_tasks=len(active_tasks))

        # Cancel reclamation scheduler
        reclaim_task.cancel()
        try:
            await reclaim_task
        except asyncio.CancelledError:
            pass

        # Cancel GPU scaling scheduler
        gpu_scaling_task.cancel()
        try:
            await gpu_scaling_task
        except asyncio.CancelledError:
            pass

        # Cancel LLM governance schedulers
        for llm_task in (escalation_task, health_check_task, digest_task, audit_task):
            llm_task.cancel()
            try:
                await llm_task
            except asyncio.CancelledError:
                pass

        # Wait for in-flight tasks
        if active_tasks:
            logger.info("waiting_for_active_tasks", count=len(active_tasks))
            done, pending = await asyncio.wait(active_tasks, timeout=60)
            if pending:
                logger.warning(
                    "tasks_timed_out",
                    pending=len(pending),
                )
                for t in pending:
                    t.cancel()

        logger.info("worker_stopped", worker_id=worker_id)


async def _process_job(
    session_factory: async_sessionmaker[AsyncSession],
    job: Job,
    semaphore: asyncio.Semaphore,
    worker_id: str,
) -> None:
    """Process a single job: look up handler, execute, complete or fail.

    Args:
        session_factory: Async session factory for database connections.
        job: The claimed job to process.
        semaphore: Concurrency semaphore to release when done.
        worker_id: The worker identifier for logging.
    """
    try:
        # Step 1: Look up handler
        handler = get_handler(job.job_type)

        if handler is None:
            # No handler registered for this job type
            logger.warning(
                "handler_not_found",
                job_id=str(job.id),
                job_type=job.job_type,
            )
            async with session_factory() as session:
                await fail_job(
                    session,
                    job.id,
                    f"No handler registered for job type: {job.job_type}",
                    ErrorCategory.HANDLER_NOT_FOUND,
                    max_attempts=1,  # Don't retry handler-not-found
                )
            return

        # Step 2: Execute handler with fresh session
        try:
            async with session_factory() as session:
                await handler(session, job)
        except Exception as exc:
            # Step 3: Handler failed
            error_category = categorize_error(exc)
            max_attempts = get_max_attempts(job.job_type)
            # Honor upstream Retry-After on 429 so the next retry doesn't
            # fire before the server asked us to (INTEGRATIONS-REVIEW M-02).
            retry_after_seconds = (
                exc.retry_after_seconds if isinstance(exc, RateLimitedError) else None
            )
            logger.warning(
                "job_failed",
                job_id=str(job.id),
                job_type=job.job_type,
                error=str(exc),
                error_category=error_category.value,
                worker_id=worker_id,
                retry_after_seconds=retry_after_seconds,
            )
            async with session_factory() as session:
                await fail_job(
                    session,
                    job.id,
                    str(exc),
                    error_category,
                    max_attempts=max_attempts,
                    retry_after_seconds=retry_after_seconds,
                )
            return

        # Step 4: Handler succeeded
        logger.info(
            "job_completed",
            job_id=str(job.id),
            job_type=job.job_type,
            worker_id=worker_id,
        )
        async with session_factory() as session:
            await complete_job(session, job.id)

    finally:
        # Always release semaphore
        semaphore.release()


async def _reclamation_scheduler(
    session_factory: async_sessionmaker[AsyncSession],
    interval: float,
    shutdown_event: asyncio.Event,
) -> None:
    """Run stale job reclamation on schedule.

    Runs reclaim_stale_jobs() every `interval` seconds until
    shutdown_event is set. Logs results and continues on error.

    Args:
        session_factory: Async session factory for database connections.
        interval: Seconds between reclamation runs.
        shutdown_event: Event to signal shutdown.
    """
    while not shutdown_event.is_set():
        try:
            await _interruptible_sleep(interval, shutdown_event)
            if shutdown_event.is_set():
                break
            async with session_factory() as session:
                reclaimed = await reclaim_stale_jobs(session)
                await session.commit()
                if reclaimed:
                    logger.info(
                        "stale_jobs_reclaimed",
                        count=len(reclaimed),
                        jobs=reclaimed,
                    )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("reclamation_failed")


async def _gpu_scaling_scheduler(
    session_factory: async_sessionmaker[AsyncSession],
    interval: float,
    shutdown_event: asyncio.Event,
) -> None:
    """Run GPU scaling management on schedule.

    Checks process_content queue depth and scales the GPU service
    up or down via Railway API. Tracks idle time across iterations.

    Args:
        session_factory: Async session factory for database connections.
        interval: Seconds between scaling checks.
        shutdown_event: Event to signal shutdown.
    """
    gpu_idle_since: datetime | None = None
    while not shutdown_event.is_set():
        try:
            await _interruptible_sleep(interval, shutdown_event)
            if shutdown_event.is_set():
                break
            async with session_factory() as session:
                scaled, gpu_idle_since = await manage_gpu_scaling(session, gpu_idle_since)
                if scaled:
                    logger.info("gpu_scaling_action", idle_since=str(gpu_idle_since))
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("gpu_scaling_failed")


async def _llm_timeout_escalation_scheduler(
    session_factory: async_sessionmaker[AsyncSession],
    interval: float,
    shutdown_event: asyncio.Event,
) -> None:
    """Run LLM timeout escalation on schedule (every 15 minutes).

    Escalates awaiting_llm jobs past llm_timeout_hours to human review.

    Args:
        session_factory: Async session factory for database connections.
        interval: Seconds between escalation runs (default 900 = 15min).
        shutdown_event: Event to signal shutdown.
    """
    while not shutdown_event.is_set():
        try:
            await _interruptible_sleep(interval, shutdown_event)
            if shutdown_event.is_set():
                break
            async with session_factory() as session:
                count = await escalate_timed_out_reviews(session)
                await session.commit()
                if count:
                    logger.info("llm_escalation_complete", escalated=count)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("llm_escalation_failed")


async def _llm_health_check_scheduler(
    session_factory: async_sessionmaker[AsyncSession],
    interval: float,
    shutdown_event: asyncio.Event,
) -> None:
    """Run LLM health check on schedule (every 6 hours).

    Args:
        session_factory: Async session factory for database connections.
        interval: Seconds between health checks (default 21600 = 6h).
        shutdown_event: Event to signal shutdown.
    """
    while not shutdown_event.is_set():
        try:
            await _interruptible_sleep(interval, shutdown_event)
            if shutdown_event.is_set():
                break
            async with session_factory() as session:
                review = await run_health_check(session)
                await session.commit()
                if review:
                    logger.info("llm_health_check_complete", decision=review.decision)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("llm_health_check_scheduler_failed")


async def _llm_digest_scheduler(
    session_factory: async_sessionmaker[AsyncSession],
    shutdown_event: asyncio.Event,
) -> None:
    """Run LLM daily digest at 07:00 UTC.

    Recomputes wait time on each iteration to avoid clock drift.

    Args:
        session_factory: Async session factory for database connections.
        shutdown_event: Event to signal shutdown.
    """
    while not shutdown_event.is_set():
        try:
            wait = seconds_until_next_utc_hour(7)
            await _interruptible_sleep(wait, shutdown_event)
            if shutdown_event.is_set():
                break
            async with session_factory() as session:
                review = await run_daily_digest(session)
                await session.commit()
                if review:
                    logger.info("llm_daily_digest_complete")
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("llm_digest_scheduler_failed")


async def _llm_audit_scheduler(
    session_factory: async_sessionmaker[AsyncSession],
    shutdown_event: asyncio.Event,
) -> None:
    """Run LLM weekly audit on Mondays at 07:00 UTC.

    Recomputes wait time on each iteration to avoid clock drift.

    Args:
        session_factory: Async session factory for database connections.
        shutdown_event: Event to signal shutdown.
    """
    while not shutdown_event.is_set():
        try:
            wait = seconds_until_next_monday_utc(7)
            await _interruptible_sleep(wait, shutdown_event)
            if shutdown_event.is_set():
                break
            async with session_factory() as session:
                review = await run_weekly_audit(session)
                await session.commit()
                if review:
                    logger.info("llm_weekly_audit_complete")
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("llm_audit_scheduler_failed")


async def _interruptible_sleep(duration: float, shutdown_event: asyncio.Event) -> None:
    """Sleep that can be interrupted by shutdown_event.

    Args:
        duration: Seconds to sleep.
        shutdown_event: Event that interrupts the sleep when set.
    """
    try:
        await asyncio.wait_for(shutdown_event.wait(), timeout=duration)
    except TimeoutError:
        pass  # Normal: shutdown_event wasn't set during sleep
