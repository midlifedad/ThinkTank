"""Integration tests for rate limiter against real PostgreSQL.

Tests the sliding-window rate limiting via rate_limit_usage table.
"""

from datetime import timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tests.factories import _now, create_rate_limit_usage, create_system_config


class TestCheckAndAcquireRateLimit:
    """Test check_and_acquire_rate_limit against real DB."""

    async def test_allows_calls_under_limit(self, session: AsyncSession):
        """When under limit, all calls should return True."""
        from src.thinktank.queue.rate_limiter import check_and_acquire_rate_limit

        # Seed: listennotes_calls_per_hour = 3
        await create_system_config(
            session,
            key="listennotes_calls_per_hour",
            value={"value": 3},
        )

        # 3 calls should all succeed
        for i in range(3):
            result = await check_and_acquire_rate_limit(
                session, "listennotes", f"worker-{i}"
            )
            assert result is True, f"Call {i+1} should have succeeded"

    async def test_blocks_at_limit(self, session: AsyncSession):
        """4th call should be blocked when limit is 3."""
        from src.thinktank.queue.rate_limiter import check_and_acquire_rate_limit

        # Seed: listennotes_calls_per_hour = 3
        await create_system_config(
            session,
            key="listennotes_calls_per_hour",
            value={"value": 3},
        )

        # Use up the limit
        for _ in range(3):
            await check_and_acquire_rate_limit(session, "listennotes", "worker-1")

        # 4th call should be blocked
        result = await check_and_acquire_rate_limit(
            session, "listennotes", "worker-1"
        )
        assert result is False

    async def test_fail_open_when_no_config(self, session: AsyncSession):
        """When no system_config entry exists, should return True (fail-open)."""
        from src.thinktank.queue.rate_limiter import check_and_acquire_rate_limit

        # No system_config seeded for this api_name
        result = await check_and_acquire_rate_limit(
            session, "unknown_api", "worker-1"
        )
        assert result is True

    async def test_old_rows_outside_window_not_counted(self, session: AsyncSession):
        """Rows with called_at older than window should not count toward limit."""
        from src.thinktank.queue.rate_limiter import check_and_acquire_rate_limit

        # Seed: youtube_calls_per_hour = 2
        await create_system_config(
            session,
            key="youtube_calls_per_hour",
            value={"value": 2},
        )

        # Insert 2 old rows (2 hours ago - outside the 60-min window)
        two_hours_ago = _now() - timedelta(hours=2)
        for i in range(2):
            await create_rate_limit_usage(
                session,
                api_name="youtube",
                worker_id=f"worker-{i}",
                called_at=two_hours_ago,
            )

        # Should still allow calls because old rows are outside the window
        result = await check_and_acquire_rate_limit(
            session, "youtube", "worker-1"
        )
        assert result is True

    async def test_different_api_names_have_separate_limits(self, session: AsyncSession):
        """Rate limits are per api_name, not global."""
        from src.thinktank.queue.rate_limiter import check_and_acquire_rate_limit

        # Seed limits for two APIs
        await create_system_config(
            session,
            key="listennotes_calls_per_hour",
            value={"value": 1},
        )
        await create_system_config(
            session,
            key="youtube_calls_per_hour",
            value={"value": 1},
        )

        # Use up listennotes limit
        result = await check_and_acquire_rate_limit(
            session, "listennotes", "worker-1"
        )
        assert result is True

        # YouTube should still have capacity
        result = await check_and_acquire_rate_limit(
            session, "youtube", "worker-1"
        )
        assert result is True

    async def test_raw_int_config_value(self, session: AsyncSession):
        """Config stored as raw integer (not wrapped in dict) should work."""
        from src.thinktank.queue.rate_limiter import check_and_acquire_rate_limit

        # Seed with raw integer value
        await create_system_config(
            session,
            key="testapi_calls_per_hour",
            value=2,
        )

        # Two calls should succeed
        for _ in range(2):
            result = await check_and_acquire_rate_limit(
                session, "testapi", "worker-1"
            )
            assert result is True

        # Third should fail
        result = await check_and_acquire_rate_limit(
            session, "testapi", "worker-1"
        )
        assert result is False
