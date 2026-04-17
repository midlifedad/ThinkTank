"""Integration tests for rate limiter against real PostgreSQL.

Tests the sliding-window rate limiting via rate_limit_usage table.
"""

import asyncio
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.factories import create_system_config


class TestCheckAndAcquireRateLimit:
    """Test check_and_acquire_rate_limit against real DB."""

    async def test_allows_calls_under_limit(self, session: AsyncSession):
        """When under limit, all calls should return True."""
        from thinktank.queue.rate_limiter import check_and_acquire_rate_limit

        # Seed: podcastindex_calls_per_hour = 3
        await create_system_config(
            session,
            key="podcastindex_calls_per_hour",
            value={"value": 3},
        )

        # 3 calls should all succeed
        for i in range(3):
            result = await check_and_acquire_rate_limit(
                session, "podcastindex", f"worker-{i}"
            )
            assert result is True, f"Call {i+1} should have succeeded"

    async def test_blocks_at_limit(self, session: AsyncSession):
        """4th call should be blocked when limit is 3."""
        from thinktank.queue.rate_limiter import check_and_acquire_rate_limit

        # Seed: podcastindex_calls_per_hour = 3
        await create_system_config(
            session,
            key="podcastindex_calls_per_hour",
            value={"value": 3},
        )

        # Use up the limit
        for _ in range(3):
            await check_and_acquire_rate_limit(session, "podcastindex", "worker-1")

        # 4th call should be blocked
        result = await check_and_acquire_rate_limit(
            session, "podcastindex", "worker-1"
        )
        assert result is False

    async def test_fail_open_when_no_config(self, session: AsyncSession):
        """When no system_config entry exists, should return True (fail-open)."""
        from thinktank.queue.rate_limiter import check_and_acquire_rate_limit

        # No system_config seeded for this api_name
        result = await check_and_acquire_rate_limit(
            session, "unknown_api", "worker-1"
        )
        assert result is True

    async def test_old_rows_outside_window_not_counted(self, session: AsyncSession):
        """Rows with called_at older than window should not count toward limit."""
        from thinktank.queue.rate_limiter import check_and_acquire_rate_limit

        # Seed: youtube_calls_per_hour = 2
        await create_system_config(
            session,
            key="youtube_calls_per_hour",
            value={"value": 2},
        )

        # Insert 2 old rows using PG LOCALTIMESTAMP so the time base matches
        # the sliding-window query (avoids Python UTC vs PG local timezone mismatch).
        for _ in range(2):
            await session.execute(
                text(
                    "INSERT INTO rate_limit_usage (id, api_name, worker_id, called_at) "
                    "VALUES (:id, :api_name, :worker_id, LOCALTIMESTAMP - INTERVAL '2 hours')"
                ),
                {
                    "id": str(uuid.uuid4()),
                    "api_name": "youtube",
                    "worker_id": "old-worker",
                },
            )
        await session.flush()

        # Should still allow calls because old rows are outside the 60-min window
        result = await check_and_acquire_rate_limit(
            session, "youtube", "worker-1"
        )
        assert result is True

    async def test_different_api_names_have_separate_limits(self, session: AsyncSession):
        """Rate limits are per api_name, not global."""
        from thinktank.queue.rate_limiter import check_and_acquire_rate_limit

        # Seed limits for two APIs
        await create_system_config(
            session,
            key="podcastindex_calls_per_hour",
            value={"value": 1},
        )
        await create_system_config(
            session,
            key="youtube_calls_per_hour",
            value={"value": 1},
        )

        # Use up podcastindex limit
        result = await check_and_acquire_rate_limit(
            session, "podcastindex", "worker-1"
        )
        assert result is True

        # YouTube should still have capacity
        result = await check_and_acquire_rate_limit(
            session, "youtube", "worker-1"
        )
        assert result is True

    async def test_raw_int_config_value(self, session: AsyncSession):
        """Config stored as raw integer (not wrapped in dict) should work."""
        from thinktank.queue.rate_limiter import check_and_acquire_rate_limit

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


class TestConcurrentAcquires:
    """TOCTOU regression: concurrent acquires must respect the limit.

    Source: INTEGRATIONS-REVIEW H-01. Without serialization, count+insert
    is a classic time-of-check-to-time-of-use race: all N concurrent
    callers see the same count, all pass the limit check, all insert.
    """

    async def test_concurrent_acquires_respect_limit(self, session_factory):
        """Spawn 10 concurrent acquires with limit=5. Exactly 5 must succeed."""
        from thinktank.queue.rate_limiter import check_and_acquire_rate_limit

        async with session_factory() as setup_session:
            await create_system_config(
                setup_session,
                key="concurrent_api_calls_per_hour",
                value={"value": 5},
            )
            await setup_session.commit()

        async def acquire_one(idx: int) -> bool:
            async with session_factory() as sess:
                result = await check_and_acquire_rate_limit(
                    sess, "concurrent_api", f"worker-{idx}"
                )
                await sess.commit()
                return result

        results = await asyncio.gather(*[acquire_one(i) for i in range(10)])
        successes = sum(1 for r in results if r is True)
        assert successes == 5, (
            f"Expected exactly 5 successful acquires, got {successes}. "
            f"Results: {results}"
        )
