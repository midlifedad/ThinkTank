"""Integration tests for claim_job, complete_job, and fail_job.

Tests run against real PostgreSQL to verify SELECT FOR UPDATE SKIP LOCKED
behavior and concurrent claim safety.
"""

import asyncio
import uuid
from datetime import timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from thinktank.queue.claim import claim_job, complete_job, fail_job
from thinktank.queue.errors import ErrorCategory
from tests.factories import _now, create_job


class TestClaimJob:
    """claim_job atomically claims the highest-priority eligible job."""

    async def test_claims_highest_priority_job(self, session: AsyncSession):
        """Lower priority number = higher priority. Should claim priority=1 first."""
        await create_job(session, priority=5, status="pending")
        high = await create_job(session, priority=1, status="pending")
        await create_job(session, priority=3, status="pending")
        await session.commit()

        claimed = await claim_job(session, "worker-1")

        assert claimed is not None
        assert claimed.id == high.id
        assert claimed.status == "running"
        assert claimed.worker_id == "worker-1"

    async def test_sets_started_at_and_increments_attempts(self, session: AsyncSession):
        await create_job(session, status="pending", attempts=0)
        await session.commit()

        claimed = await claim_job(session, "worker-1")

        assert claimed is not None
        assert claimed.started_at is not None
        assert claimed.attempts == 1

    async def test_returns_none_when_no_eligible_jobs(self, session: AsyncSession):
        """No pending/retrying jobs -> returns None."""
        await create_job(session, status="done")
        await create_job(session, status="failed")
        await session.commit()

        result = await claim_job(session, "worker-1")
        assert result is None

    async def test_returns_none_on_empty_table(self, session: AsyncSession):
        result = await claim_job(session, "worker-1")
        assert result is None

    async def test_skips_future_scheduled_jobs(self, session: AsyncSession):
        """Jobs with scheduled_at in the future should not be claimed."""
        future_time = _now() + timedelta(hours=1)
        await create_job(session, status="pending", scheduled_at=future_time)
        await session.commit()

        result = await claim_job(session, "worker-1")
        assert result is None

    async def test_claims_null_scheduled_at_as_immediately_eligible(self, session: AsyncSession):
        """scheduled_at=NULL means immediately eligible."""
        job = await create_job(session, status="pending", scheduled_at=None)
        await session.commit()

        claimed = await claim_job(session, "worker-1")
        assert claimed is not None
        assert claimed.id == job.id

    async def test_claims_past_scheduled_at(self, session: AsyncSession):
        """Jobs with scheduled_at in the past should be claimed."""
        past_time = _now() - timedelta(hours=1)
        job = await create_job(session, status="retrying", scheduled_at=past_time)
        await session.commit()

        claimed = await claim_job(session, "worker-1")
        assert claimed is not None
        assert claimed.id == job.id

    async def test_claims_retrying_jobs(self, session: AsyncSession):
        """Retrying jobs with past scheduled_at should be claimable."""
        past_time = _now() - timedelta(minutes=5)
        job = await create_job(session, status="retrying", scheduled_at=past_time, attempts=1)
        await session.commit()

        claimed = await claim_job(session, "worker-1")
        assert claimed is not None
        assert claimed.id == job.id
        assert claimed.status == "running"

    async def test_job_types_filter(self, session: AsyncSession):
        """When job_types provided, only those types are claimed."""
        await create_job(session, job_type="discover_thinker", status="pending")
        fetch_job = await create_job(session, job_type="fetch_podcast_feed", status="pending", priority=1)
        await session.commit()

        claimed = await claim_job(session, "worker-1", job_types=["fetch_podcast_feed"])
        assert claimed is not None
        assert claimed.id == fetch_job.id
        assert claimed.job_type == "fetch_podcast_feed"

    async def test_job_types_filter_no_match(self, session: AsyncSession):
        """When no jobs match the type filter, returns None."""
        await create_job(session, job_type="discover_thinker", status="pending")
        await session.commit()

        result = await claim_job(session, "worker-1", job_types=["process_content"])
        assert result is None

    async def test_concurrent_claims_mutual_exclusion(
        self, session_factory,
    ):
        """Two workers claiming the same single job: exactly one wins."""
        # Create one job using a dedicated session
        async with session_factory() as setup_session:
            job = await create_job(setup_session, status="pending", priority=1)
            job_id = job.id
            await setup_session.commit()

        # Two concurrent claims with separate sessions
        async def do_claim(worker_id: str):
            async with session_factory() as sess:
                return await claim_job(sess, worker_id)

        results = await asyncio.gather(
            do_claim("worker-a"),
            do_claim("worker-b"),
        )

        # Exactly one should succeed
        claimed = [r for r in results if r is not None]
        nones = [r for r in results if r is None]
        assert len(claimed) == 1, f"Expected exactly one claim, got {len(claimed)}"
        assert len(nones) == 1
        assert claimed[0].id == job_id

    async def test_null_scheduled_at_ordered_before_future(self, session: AsyncSession):
        """NULL scheduled_at jobs should be claimed before future scheduled_at jobs."""
        future_time = _now() + timedelta(hours=1)
        await create_job(session, status="pending", scheduled_at=future_time, priority=1)
        null_job = await create_job(session, status="pending", scheduled_at=None, priority=5)
        await session.commit()

        claimed = await claim_job(session, "worker-1")
        assert claimed is not None
        assert claimed.id == null_job.id


class TestCompleteJob:
    """complete_job marks a job as done."""

    async def test_sets_done_status(self, session: AsyncSession):
        job = await create_job(session, status="running", worker_id="worker-1")
        await session.commit()

        await complete_job(session, job.id)

        await session.refresh(job)
        assert job.status == "done"
        assert job.completed_at is not None

    async def test_clears_error_fields(self, session: AsyncSession):
        job = await create_job(
            session,
            status="running",
            error="previous error",
            error_category="http_error",
        )
        await session.commit()

        await complete_job(session, job.id)

        await session.refresh(job)
        assert job.error is None
        assert job.error_category is None


class TestFailJob:
    """fail_job handles retries and terminal failures."""

    async def test_retries_when_under_max_attempts(self, session: AsyncSession):
        """With attempts < max_attempts, set status='retrying' with backoff."""
        job = await create_job(
            session,
            status="running",
            attempts=1,
            max_attempts=3,
            job_type="discover_thinker",
        )
        await session.commit()

        await fail_job(
            session,
            job.id,
            error_msg="Connection refused",
            error_category=ErrorCategory.HTTP_ERROR,
        )

        await session.refresh(job)
        assert job.status == "retrying"
        assert job.scheduled_at is not None
        assert job.worker_id is None
        assert job.error == "Connection refused"
        assert job.error_category == "http_error"
        assert job.last_error_at is not None
        assert job.completed_at is None

    async def test_fails_at_max_attempts(self, session: AsyncSession):
        """With attempts >= max_attempts, set status='failed' and completed_at."""
        job = await create_job(
            session,
            status="running",
            attempts=3,
            max_attempts=3,
            job_type="discover_thinker",
        )
        await session.commit()

        await fail_job(
            session,
            job.id,
            error_msg="Max retries exceeded",
            error_category=ErrorCategory.HTTP_TIMEOUT,
        )

        await session.refresh(job)
        assert job.status == "failed"
        assert job.completed_at is not None
        assert job.error == "Max retries exceeded"
        assert job.error_category == "http_timeout"

    async def test_uses_per_type_max_attempts(self, session: AsyncSession):
        """process_content has max_attempts=2, should fail on attempt 2."""
        job = await create_job(
            session,
            status="running",
            attempts=2,
            max_attempts=3,  # model default, but per-type should override
            job_type="process_content",
        )
        await session.commit()

        await fail_job(
            session,
            job.id,
            error_msg="Transcription failed",
            error_category=ErrorCategory.TRANSCRIPTION_FAILED,
        )

        await session.refresh(job)
        # process_content max_attempts=2, attempts=2, so 2 >= 2 -> failed
        assert job.status == "failed"

    async def test_explicit_max_attempts_override(self, session: AsyncSession):
        """When max_attempts is explicitly provided, use it over per-type defaults."""
        job = await create_job(
            session,
            status="running",
            attempts=2,
            job_type="process_content",
        )
        await session.commit()

        await fail_job(
            session,
            job.id,
            error_msg="Retrying",
            error_category=ErrorCategory.HTTP_ERROR,
            max_attempts=5,  # Override the per-type limit
        )

        await session.refresh(job)
        assert job.status == "retrying"  # 2 < 5

    async def test_backoff_timing(self, session: AsyncSession):
        """Backoff should be 2^attempts minutes from now."""
        job = await create_job(
            session,
            status="running",
            attempts=2,
            max_attempts=4,
        )
        await session.commit()

        before = _now()
        await fail_job(
            session,
            job.id,
            error_msg="Retry",
            error_category=ErrorCategory.HTTP_ERROR,
        )
        after = _now()

        await session.refresh(job)
        # 2^2 = 4 minutes
        expected_min = before + timedelta(minutes=4) - timedelta(seconds=1)
        expected_max = after + timedelta(minutes=4) + timedelta(seconds=1)
        assert expected_min <= job.scheduled_at <= expected_max

    async def test_sets_error_category_as_string_value(self, session: AsyncSession):
        """error_category field should be the string value of the enum."""
        job = await create_job(session, status="running", attempts=1, max_attempts=3)
        await session.commit()

        await fail_job(
            session,
            job.id,
            error_msg="Rate limited",
            error_category=ErrorCategory.RATE_LIMITED,
        )

        await session.refresh(job)
        assert job.error_category == "rate_limited"
        assert isinstance(job.error_category, str)
