"""Contract tests for rollup_api_usage handler.

Verifies:
- Handler aggregates rate_limit_usage rows into api_usage with correct counts and costs
- Handler is idempotent (no duplicates on re-run)
- Handler purges rate_limit_usage rows older than 2 hours
- Handler preserves rate_limit_usage rows newer than current hour
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from thinktank.models.api_usage import ApiUsage
from thinktank.models.rate_limit import RateLimitUsage
from tests.factories import create_job, create_rate_limit_usage

pytestmark = pytest.mark.anyio


def _hours_ago(n: int) -> datetime:
    """Return a timezone-aware UTC datetime n hours ago (TIMESTAMPTZ columns)."""
    return datetime.now(UTC) - timedelta(hours=n)


class TestRollupApiUsageHandler:
    """Contract tests for handle_rollup_api_usage."""

    async def test_aggregates_usage_with_correct_counts(self, session: AsyncSession):
        """Insert rate_limit_usage rows 3 hours ago, run handler, verify api_usage counts."""
        from thinktank.handlers.rollup_api_usage import handle_rollup_api_usage

        three_hours_ago = _hours_ago(3)
        for _ in range(5):
            await create_rate_limit_usage(
                session,
                api_name="podcastindex",
                worker_id="w1",
                called_at=three_hours_ago,
            )
        job = await create_job(session, job_type="rollup_api_usage")
        await session.commit()

        await handle_rollup_api_usage(session, job)
        await session.commit()

        result = await session.execute(
            select(ApiUsage).where(ApiUsage.api_name == "podcastindex")
        )
        rows = result.scalars().all()
        assert len(rows) >= 1
        total_calls = sum(r.call_count for r in rows)
        assert total_calls == 5

    async def test_cost_estimates_applied(self, session: AsyncSession):
        """Verify cost estimates are calculated from API_COST_MAP."""
        from thinktank.handlers.rollup_api_usage import handle_rollup_api_usage

        three_hours_ago = _hours_ago(3)
        for _ in range(10):
            await create_rate_limit_usage(
                session,
                api_name="youtube",
                worker_id="w1",
                called_at=three_hours_ago,
            )
        job = await create_job(session, job_type="rollup_api_usage")
        await session.commit()

        await handle_rollup_api_usage(session, job)
        await session.commit()

        result = await session.execute(
            select(ApiUsage).where(ApiUsage.api_name == "youtube")
        )
        rows = result.scalars().all()
        total_cost = sum(float(r.estimated_cost_usd) for r in rows if r.estimated_cost_usd)
        # 10 calls * $0.001 = $0.01
        assert abs(total_cost - 0.01) < 0.001

    async def test_idempotent_on_rerun(self, session: AsyncSession):
        """Running handler twice on same data does not duplicate api_usage rows."""
        from thinktank.handlers.rollup_api_usage import handle_rollup_api_usage

        three_hours_ago = _hours_ago(3)
        for _ in range(3):
            await create_rate_limit_usage(
                session,
                api_name="youtube",
                worker_id="w1",
                called_at=three_hours_ago,
            )
        job = await create_job(session, job_type="rollup_api_usage")
        await session.commit()

        # First run
        await handle_rollup_api_usage(session, job)
        await session.commit()

        # Second run
        await handle_rollup_api_usage(session, job)
        await session.commit()

        result = await session.execute(
            select(ApiUsage).where(ApiUsage.api_name == "youtube")
        )
        rows = result.scalars().all()
        total_calls = sum(r.call_count for r in rows)
        assert total_calls == 3  # Not 6 (no duplicates)

    async def test_purges_old_rate_limit_usage(self, session: AsyncSession):
        """rate_limit_usage rows older than 2 hours are purged after handler runs."""
        from thinktank.handlers.rollup_api_usage import handle_rollup_api_usage

        three_hours_ago = _hours_ago(3)
        for _ in range(3):
            await create_rate_limit_usage(
                session,
                api_name="anthropic",
                worker_id="w1",
                called_at=three_hours_ago,
            )
        job = await create_job(session, job_type="rollup_api_usage")
        await session.commit()

        await handle_rollup_api_usage(session, job)
        await session.commit()

        result = await session.execute(
            select(RateLimitUsage).where(RateLimitUsage.api_name == "anthropic")
        )
        remaining = result.scalars().all()
        assert len(remaining) == 0

    async def test_preserves_recent_rate_limit_usage(self, session: AsyncSession):
        """rate_limit_usage rows newer than current hour are NOT purged."""
        from thinktank.handlers.rollup_api_usage import handle_rollup_api_usage

        # Old rows (should be aggregated and purged)
        three_hours_ago = _hours_ago(3)
        await create_rate_limit_usage(
            session,
            api_name="podcastindex",
            worker_id="w1",
            called_at=three_hours_ago,
        )

        # Recent row (should be preserved)
        recent = datetime.now(UTC) - timedelta(minutes=5)
        await create_rate_limit_usage(
            session,
            api_name="podcastindex",
            worker_id="w1",
            called_at=recent,
        )

        job = await create_job(session, job_type="rollup_api_usage")
        await session.commit()

        await handle_rollup_api_usage(session, job)
        await session.commit()

        result = await session.execute(
            select(RateLimitUsage).where(RateLimitUsage.api_name == "podcastindex")
        )
        remaining = result.scalars().all()
        # The recent row (within current hour) should still be there
        assert len(remaining) == 1
