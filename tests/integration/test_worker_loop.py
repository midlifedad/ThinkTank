"""Integration tests for the async worker loop.

Tests verify the full lifecycle: claim, dispatch, complete/fail,
kill switch, and handler-not-found behavior against a real database.

Uses short poll_interval (0.1s) and brief shutdown delays (0.5s)
to keep tests fast while testing real async orchestration.
"""

import asyncio

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tests.factories import create_job, create_system_config
from thinktank.handlers.registry import JOB_HANDLERS, register_handler
from thinktank.models.job import Job
from thinktank.worker.loop import worker_loop


@pytest.fixture(autouse=True)
def _clean_handlers():
    """Save and restore JOB_HANDLERS state around each test."""
    saved = dict(JOB_HANDLERS)
    JOB_HANDLERS.clear()
    yield
    JOB_HANDLERS.clear()
    JOB_HANDLERS.update(saved)


class TestWorkerLoopLifecycle:
    """Test the basic claim-dispatch-complete lifecycle."""

    @pytest.mark.asyncio
    async def test_claims_and_completes_job(self, session_factory):
        """Worker claims a pending job, dispatches to handler, marks done."""
        handler_called = asyncio.Event()

        async def success_handler(session: AsyncSession, job: Job) -> None:
            handler_called.set()

        register_handler("test_job", success_handler)

        # Create a pending job
        async with session_factory() as session:
            job = await create_job(session, job_type="test_job", status="pending")
            job_id = job.id
            await session.commit()

        # Run worker loop briefly
        shutdown = asyncio.Event()

        async def stop_after_processing():
            # Wait for handler to be called or timeout
            try:
                await asyncio.wait_for(handler_called.wait(), timeout=3.0)
            except TimeoutError:
                pass
            # Give worker a moment to complete the job
            await asyncio.sleep(0.2)
            shutdown.set()

        from thinktank.worker.config import WorkerSettings

        settings = WorkerSettings(
            poll_interval=0.1,
            max_idle_backoff=0.5,
            max_concurrency=2,
            reclaim_interval=600.0,  # Don't trigger during test
        )

        loop_task = asyncio.create_task(worker_loop(session_factory, settings=settings, shutdown_event=shutdown))
        stop_task = asyncio.create_task(stop_after_processing())

        await asyncio.gather(loop_task, stop_task)

        # Verify job is done
        async with session_factory() as session:
            result_job = await session.get(Job, job_id)
            assert result_job is not None
            assert result_job.status == "done"

        assert handler_called.is_set(), "Handler was never called"

    @pytest.mark.asyncio
    async def test_handler_failure_marks_job_failed_or_retrying(self, session_factory):
        """When handler raises, job is marked failed or retrying with correct error_category."""
        handler_called = asyncio.Event()

        async def failing_handler(session: AsyncSession, job: Job) -> None:
            handler_called.set()
            raise RuntimeError("Something broke")

        register_handler("fail_job_type", failing_handler)

        # Create a pending job with max_attempts=1 so it goes to failed immediately
        async with session_factory() as session:
            job = await create_job(
                session,
                job_type="fail_job_type",
                status="pending",
                max_attempts=1,
            )
            job_id = job.id
            await session.commit()

        shutdown = asyncio.Event()

        async def stop_after_processing():
            try:
                await asyncio.wait_for(handler_called.wait(), timeout=3.0)
            except TimeoutError:
                pass
            await asyncio.sleep(0.2)
            shutdown.set()

        from thinktank.worker.config import WorkerSettings

        settings = WorkerSettings(
            poll_interval=0.1,
            max_idle_backoff=0.5,
            max_concurrency=2,
            reclaim_interval=600.0,
        )

        loop_task = asyncio.create_task(worker_loop(session_factory, settings=settings, shutdown_event=shutdown))
        stop_task = asyncio.create_task(stop_after_processing())

        await asyncio.gather(loop_task, stop_task)

        # Verify job has error info
        async with session_factory() as session:
            result_job = await session.get(Job, job_id)
            assert result_job is not None
            assert result_job.status in ("failed", "retrying")
            assert result_job.error is not None
            assert "Something broke" in result_job.error
            assert result_job.error_category is not None

    @pytest.mark.asyncio
    async def test_kill_switch_prevents_claiming(self, session_factory):
        """When workers_active=false, worker does not claim jobs."""

        async def should_not_run(session: AsyncSession, job: Job) -> None:
            raise AssertionError("Handler should not be called when kill switch is active")

        register_handler("kill_test_type", should_not_run)

        # Set kill switch
        async with session_factory() as session:
            await create_system_config(session, key="workers_active", value=False)
            job = await create_job(session, job_type="kill_test_type", status="pending")
            job_id = job.id
            await session.commit()

        shutdown = asyncio.Event()

        async def stop_after_delay():
            await asyncio.sleep(0.5)
            shutdown.set()

        from thinktank.worker.config import WorkerSettings

        settings = WorkerSettings(
            poll_interval=0.1,
            max_idle_backoff=0.5,
            max_concurrency=2,
            reclaim_interval=600.0,
        )

        loop_task = asyncio.create_task(worker_loop(session_factory, settings=settings, shutdown_event=shutdown))
        stop_task = asyncio.create_task(stop_after_delay())

        await asyncio.gather(loop_task, stop_task)

        # Job should still be pending
        async with session_factory() as session:
            result_job = await session.get(Job, job_id)
            assert result_job is not None
            assert result_job.status == "pending"

    @pytest.mark.asyncio
    async def test_handler_not_found_fails_job(self, session_factory):
        """Job with unregistered handler type is failed with handler_not_found."""
        # No handler registered for this type

        async with session_factory() as session:
            job = await create_job(
                session,
                job_type="nonexistent_handler_type",
                status="pending",
                max_attempts=1,
            )
            job_id = job.id
            await session.commit()

        shutdown = asyncio.Event()

        async def stop_after_delay():
            await asyncio.sleep(0.5)
            shutdown.set()

        from thinktank.worker.config import WorkerSettings

        settings = WorkerSettings(
            poll_interval=0.1,
            max_idle_backoff=0.5,
            max_concurrency=2,
            reclaim_interval=600.0,
        )

        loop_task = asyncio.create_task(worker_loop(session_factory, settings=settings, shutdown_event=shutdown))
        stop_task = asyncio.create_task(stop_after_delay())

        await asyncio.gather(loop_task, stop_task)

        # Job should be failed with handler_not_found
        async with session_factory() as session:
            result_job = await session.get(Job, job_id)
            assert result_job is not None
            assert result_job.status == "failed"
            assert result_job.error_category == "handler_not_found"
