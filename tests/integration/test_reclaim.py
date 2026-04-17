"""Integration tests for stale job reclamation against real PostgreSQL.

Tests reclaim_stale_jobs() with real timestamps and retry/fail logic.
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.factories import create_job, create_system_config


class TestReclaimStaleJobs:
    """Test reclaim_stale_jobs against real DB."""

    async def _create_running_job_started_minutes_ago(self, session: AsyncSession, minutes_ago: int, **overrides):
        """Helper: create a running job with started_at set via PG time.

        Uses LOCALTIMESTAMP to avoid timezone mismatch between Python UTC
        and PG local time in TIMESTAMP WITHOUT TIME ZONE columns.
        """
        job = await create_job(
            session,
            status="running",
            worker_id="stale-worker-1",
            **overrides,
        )
        # Set started_at using PG's own clock for consistency with reclaim query
        await session.execute(
            text("UPDATE jobs SET started_at = LOCALTIMESTAMP - MAKE_INTERVAL(mins => :mins) WHERE id = :job_id"),
            {"mins": minutes_ago, "job_id": str(job.id)},
        )
        await session.flush()
        return job

    async def test_reclaims_stale_job(self, session: AsyncSession):
        """A running job past the timeout should be reclaimed."""
        from thinktank.queue.reclaim import reclaim_stale_jobs

        # Set timeout to 30 minutes
        await create_system_config(
            session,
            key="stale_job_timeout_minutes",
            value={"value": 30},
        )

        # Create a job that started 35 minutes ago (past 30-min timeout)
        stale_job = await self._create_running_job_started_minutes_ago(session, 35, attempts=0, max_attempts=3)

        reclaimed = await reclaim_stale_jobs(session)

        assert len(reclaimed) == 1
        assert reclaimed[0]["id"] == stale_job.id

    async def test_does_not_reclaim_fresh_job(self, session: AsyncSession):
        """A running job within the timeout should NOT be reclaimed."""
        from thinktank.queue.reclaim import reclaim_stale_jobs

        await create_system_config(
            session,
            key="stale_job_timeout_minutes",
            value={"value": 30},
        )

        # Create a job that started 25 minutes ago (within 30-min timeout)
        await self._create_running_job_started_minutes_ago(session, 25, attempts=0, max_attempts=3)

        reclaimed = await reclaim_stale_jobs(session)
        assert len(reclaimed) == 0

    async def test_reclaimed_job_gets_retrying_status(self, session: AsyncSession):
        """Reclaimed job with attempts < max_attempts should get status='retrying'."""
        from thinktank.queue.reclaim import reclaim_stale_jobs

        await create_system_config(
            session,
            key="stale_job_timeout_minutes",
            value={"value": 30},
        )

        stale_job = await self._create_running_job_started_minutes_ago(session, 35, attempts=0, max_attempts=3)

        await reclaim_stale_jobs(session)

        # Re-read the job to check updated fields
        result = await session.execute(
            text("SELECT status, worker_id, error_category, error, attempts FROM jobs WHERE id = :id"),
            {"id": str(stale_job.id)},
        )
        row = result.fetchone()
        assert row[0] == "retrying"  # status
        assert row[1] is None  # worker_id cleared
        assert row[2] == "worker_timeout"  # error_category
        assert "stale_job_timeout_minutes" in row[3]  # error message
        assert row[4] == 1  # attempts incremented from 0 to 1

    async def test_reclaimed_job_at_max_attempts_gets_failed(self, session: AsyncSession):
        """Reclaimed job at max_attempts should get status='failed' and completed_at."""
        from thinktank.queue.reclaim import reclaim_stale_jobs

        await create_system_config(
            session,
            key="stale_job_timeout_minutes",
            value={"value": 30},
        )

        # Job with attempts=2, max_attempts=3: next attempt (3) >= max_attempts
        stale_job = await self._create_running_job_started_minutes_ago(session, 35, attempts=2, max_attempts=3)

        await reclaim_stale_jobs(session)

        result = await session.execute(
            text("SELECT status, completed_at, attempts FROM jobs WHERE id = :id"),
            {"id": str(stale_job.id)},
        )
        row = result.fetchone()
        assert row[0] == "failed"  # status
        assert row[1] is not None  # completed_at set
        assert row[2] == 3  # attempts incremented from 2 to 3

    async def test_returns_empty_when_no_stale_jobs(self, session: AsyncSession):
        """Should return empty list when no stale jobs exist."""
        from thinktank.queue.reclaim import reclaim_stale_jobs

        await create_system_config(
            session,
            key="stale_job_timeout_minutes",
            value={"value": 30},
        )

        reclaimed = await reclaim_stale_jobs(session)
        assert reclaimed == []

    async def test_non_running_jobs_never_reclaimed(self, session: AsyncSession):
        """Jobs in pending, done, or failed status should never be reclaimed."""
        from thinktank.queue.reclaim import reclaim_stale_jobs

        await create_system_config(
            session,
            key="stale_job_timeout_minutes",
            value={"value": 30},
        )

        # Create non-running jobs with old started_at
        for status in ("pending", "done", "failed"):
            job = await create_job(session, status=status, attempts=0, max_attempts=3)
            await session.execute(
                text("UPDATE jobs SET started_at = LOCALTIMESTAMP - INTERVAL '2 hours' WHERE id = :id"),
                {"id": str(job.id)},
            )

        await session.flush()

        reclaimed = await reclaim_stale_jobs(session)
        assert reclaimed == []

    async def test_mixed_stale_and_fresh_jobs(self, session: AsyncSession):
        """Only stale running jobs should be reclaimed, not fresh ones."""
        from thinktank.queue.reclaim import reclaim_stale_jobs

        await create_system_config(
            session,
            key="stale_job_timeout_minutes",
            value={"value": 30},
        )

        # Stale job (35 min ago)
        stale = await self._create_running_job_started_minutes_ago(session, 35, attempts=0, max_attempts=3)
        # Fresh job (10 min ago)
        await self._create_running_job_started_minutes_ago(session, 10, attempts=0, max_attempts=3)

        reclaimed = await reclaim_stale_jobs(session)

        assert len(reclaimed) == 1
        assert reclaimed[0]["id"] == stale.id

    async def test_uses_default_timeout_when_no_config(self, session: AsyncSession):
        """When stale_job_timeout_minutes not in config, defaults to 30."""
        from thinktank.queue.reclaim import reclaim_stale_jobs

        # No stale_job_timeout_minutes config -- should default to 30

        # Stale job (35 min ago, past default 30)
        stale = await self._create_running_job_started_minutes_ago(session, 35, attempts=0, max_attempts=3)

        reclaimed = await reclaim_stale_jobs(session)
        assert len(reclaimed) == 1
        assert reclaimed[0]["id"] == stale.id

    async def test_reclaim_backoff_is_capped_at_sixty_minutes(self, session: AsyncSession):
        """HANDLERS-REVIEW HI-06 (T6.3): backoff must match retry.calculate_backoff
        (single source of truth), which caps at 60 minutes.

        Previously reclaim.py used raw SQL ``POWER(2, attempts + 1)`` with no
        cap, so a job with attempts=10 would be scheduled 2^11 = 2048 minutes
        (~34 hours) in the future -- diverging from worker retry semantics.
        """
        from datetime import datetime, timedelta

        from thinktank.queue.reclaim import reclaim_stale_jobs

        await create_system_config(
            session,
            key="stale_job_timeout_minutes",
            value={"value": 30},
        )

        # max_attempts is high so the job is still retryable; attempts=10
        # triggers the uncapped SQL path (2^11 = 2048 minutes).
        stale = await self._create_running_job_started_minutes_ago(session, 35, attempts=10, max_attempts=20)

        await reclaim_stale_jobs(session)

        result = await session.execute(
            text("SELECT status, scheduled_at, NOW() AS now_ts FROM jobs WHERE id = :id"),
            {"id": str(stale.id)},
        )
        row = result.fetchone()
        assert row[0] == "retrying"
        assert row[1] is not None

        scheduled_at: datetime = row[1]
        now_ts: datetime = row[2]
        delta = scheduled_at - now_ts
        # Expected: calculate_backoff(11) = min(2**11, 60) = 60 minutes.
        # Allow small slop for clock drift between LOCALTIMESTAMP and the
        # per-row UPDATE execution.
        assert delta <= timedelta(minutes=60, seconds=5), f"Reclaim backoff should be capped at 60 minutes, got {delta}"
        assert delta >= timedelta(minutes=59), f"Expected full 60-minute cap, got {delta}"
