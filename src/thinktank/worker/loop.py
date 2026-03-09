"""Async worker loop: polls for jobs, dispatches to handlers, manages shutdown.

The main runtime that drives all job processing. Ties together claim,
retry, rate limiting, backpressure, kill switch, and reclamation into
a single cohesive worker process.

Spec reference: Sections 6.1, 6.2, 6.3.

Usage:
    from src.thinktank.worker.loop import worker_loop
    await worker_loop(session_factory)
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import socket

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.thinktank.handlers.registry import get_handler
from src.thinktank.models.job import Job
from src.thinktank.queue.backpressure import get_effective_priority
from src.thinktank.queue.claim import claim_job, complete_job, fail_job
from src.thinktank.queue.errors import ErrorCategory, categorize_error
from src.thinktank.queue.kill_switch import is_workers_active
from src.thinktank.queue.reclaim import reclaim_stale_jobs
from src.thinktank.queue.retry import get_max_attempts
from src.thinktank.worker.config import WorkerSettings, get_worker_settings

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
                    settings.poll_interval * (settings.idle_backoff_multiplier ** idle_count),
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
            logger.warning(
                "job_failed",
                job_id=str(job.id),
                job_type=job.job_type,
                error=str(exc),
                error_category=error_category.value,
                worker_id=worker_id,
            )
            async with session_factory() as session:
                await fail_job(
                    session,
                    job.id,
                    str(exc),
                    error_category,
                    max_attempts=max_attempts,
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


async def _interruptible_sleep(duration: float, shutdown_event: asyncio.Event) -> None:
    """Sleep that can be interrupted by shutdown_event.

    Args:
        duration: Seconds to sleep.
        shutdown_event: Event that interrupts the sleep when set.
    """
    try:
        await asyncio.wait_for(shutdown_event.wait(), timeout=duration)
    except asyncio.TimeoutError:
        pass  # Normal: shutdown_event wasn't set during sleep
