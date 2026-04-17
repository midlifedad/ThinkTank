"""Integration tests for backpressure module against real PostgreSQL.

Tests queue depth queries and priority demotion with real data.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from tests.factories import create_job, create_system_config


class TestGetQueueDepth:
    """Test get_queue_depth against real DB."""

    async def test_counts_pending_and_retrying_jobs(self, session: AsyncSession):
        """Should count jobs with status 'pending' or 'retrying'."""
        from thinktank.queue.backpressure import get_queue_depth

        # Create mix of statuses for process_content
        for _ in range(3):
            await create_job(session, job_type="process_content", status="pending")
        for _ in range(2):
            await create_job(session, job_type="process_content", status="retrying")
        # These should NOT be counted
        await create_job(session, job_type="process_content", status="running")
        await create_job(session, job_type="process_content", status="done")
        await create_job(session, job_type="process_content", status="failed")

        depth = await get_queue_depth(session, "process_content")
        assert depth == 5  # 3 pending + 2 retrying

    async def test_counts_only_specified_job_type(self, session: AsyncSession):
        """Should only count jobs of the specified type."""
        from thinktank.queue.backpressure import get_queue_depth

        # Create jobs of different types
        for _ in range(3):
            await create_job(session, job_type="process_content", status="pending")
        for _ in range(2):
            await create_job(session, job_type="discover_thinker", status="pending")

        depth = await get_queue_depth(session, "process_content")
        assert depth == 3

    async def test_returns_zero_when_no_matching_jobs(self, session: AsyncSession):
        """Should return 0 when no pending/retrying jobs exist."""
        from thinktank.queue.backpressure import get_queue_depth

        depth = await get_queue_depth(session, "process_content")
        assert depth == 0


class TestGetEffectivePriorityIntegration:
    """Test get_effective_priority with real DB queries."""

    async def test_demotes_when_above_threshold(self, session: AsyncSession):
        """When 501 process_content jobs pending, discovery priority should be demoted."""
        from thinktank.queue.backpressure import get_effective_priority

        # Set threshold
        await create_system_config(
            session,
            key="max_pending_transcriptions",
            value={"value": 500},
        )

        # Create 501 pending process_content jobs
        for _ in range(501):
            await create_job(session, job_type="process_content", status="pending")

        # Create a discovery job
        job = await create_job(session, job_type="discover_thinker", priority=5, status="pending")

        result = await get_effective_priority(session, job)
        assert result == 8  # 5 + 3

    async def test_normal_priority_below_80_percent(self, session: AsyncSession):
        """When 399 jobs (below 80% of 500), should return original priority."""
        from thinktank.queue.backpressure import get_effective_priority

        # Set threshold
        await create_system_config(
            session,
            key="max_pending_transcriptions",
            value={"value": 500},
        )

        # Create 399 pending process_content jobs (below 80% of 500 = 400)
        for _ in range(399):
            await create_job(session, job_type="process_content", status="pending")

        job = await create_job(session, job_type="discover_thinker", priority=5, status="pending")

        result = await get_effective_priority(session, job)
        assert result == 5  # No demotion

    async def test_unchanged_in_hysteresis_band(self, session: AsyncSession):
        """When 450 jobs (between 80-100%), should return original priority."""
        from thinktank.queue.backpressure import get_effective_priority

        # Set threshold
        await create_system_config(
            session,
            key="max_pending_transcriptions",
            value={"value": 500},
        )

        # Create 450 pending process_content jobs (in 80-100% band)
        for _ in range(450):
            await create_job(session, job_type="process_content", status="pending")

        job = await create_job(session, job_type="discover_thinker", priority=5, status="pending")

        result = await get_effective_priority(session, job)
        assert result == 5  # Hysteresis: no change

    async def test_non_discovery_type_unchanged_regardless(self, session: AsyncSession):
        """Non-discovery job type returns original priority regardless of depth."""
        from thinktank.queue.backpressure import get_effective_priority

        # Set threshold
        await create_system_config(
            session,
            key="max_pending_transcriptions",
            value={"value": 500},
        )

        # Create 1000 pending process_content jobs (way above threshold)
        for _ in range(600):
            await create_job(session, job_type="process_content", status="pending")

        # process_content job itself should NOT be demoted
        job = await create_job(session, job_type="process_content", priority=5, status="pending")

        result = await get_effective_priority(session, job)
        assert result == 5  # Not a backpressure type

    async def test_priority_capped_at_10(self, session: AsyncSession):
        """Demotion should never exceed priority 10."""
        from thinktank.queue.backpressure import get_effective_priority

        await create_system_config(
            session,
            key="max_pending_transcriptions",
            value={"value": 500},
        )

        for _ in range(501):
            await create_job(session, job_type="process_content", status="pending")

        job = await create_job(session, job_type="discover_thinker", priority=9, status="pending")

        result = await get_effective_priority(session, job)
        assert result == 10  # 9 + 3 = 12, capped at 10
