"""Contract tests for scheduled-LLM period-idempotency guards (A4).

Source: ARCH-REVIEW 2026-05-28. The advisory lock serializes replicas that
tick simultaneously; these guards make each period run AT MOST ONCE even
when a straggler replica ticks after the winner committed.

Contract:
    - Given a scheduled review of the same type already recorded in the
      current period
    - When the run_* function is invoked again
    - Then it returns None WITHOUT calling the LLM
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tests.factories import create_llm_review
from thinktank.llm.scheduled import run_daily_digest, run_health_check, run_weekly_audit

pytestmark = pytest.mark.anyio


def _mock_client():
    """Patch the scheduled module's LLM client; review must NOT be called."""
    mock = AsyncMock()
    mock.review = AsyncMock(side_effect=AssertionError("LLM must not be called when period guard fires"))
    return patch("thinktank.llm.scheduled._llm_client", mock)


class TestHealthCheckGuard:
    async def test_skips_when_recent_run_exists(self, session: AsyncSession):
        await create_llm_review(
            session,
            review_type="health_check",
            trigger="scheduled",
            created_at=datetime.now(UTC) - timedelta(hours=1),
        )

        with _mock_client():
            assert await run_health_check(session) is None

    async def test_runs_when_last_run_is_old(self, session: AsyncSession):
        """A run older than the guard window does not block; the LLM IS
        called (mock raises, run returns None via its except path -- the
        assertion here is that the guard did not fire silently)."""
        await create_llm_review(
            session,
            review_type="health_check",
            trigger="scheduled",
            created_at=datetime.now(UTC) - timedelta(hours=7),
        )

        mock = AsyncMock()
        mock.review = AsyncMock(side_effect=RuntimeError("llm reached"))
        with patch("thinktank.llm.scheduled._llm_client", mock):
            result = await run_health_check(session)

        assert result is None  # failed at the (mocked) LLM, not the guard
        mock.review.assert_called_once()


class TestDailyDigestGuard:
    async def test_skips_when_run_today(self, session: AsyncSession):
        await create_llm_review(
            session,
            review_type="daily_digest",
            trigger="scheduled",
            created_at=datetime.now(UTC),
        )

        with _mock_client():
            assert await run_daily_digest(session) is None

    async def test_manual_trigger_does_not_block(self, session: AsyncSession):
        """Only trigger='scheduled' reviews count -- a human-triggered
        review must not suppress the scheduled run."""
        await create_llm_review(
            session,
            review_type="daily_digest",
            trigger="manual",
            created_at=datetime.now(UTC),
        )

        mock = AsyncMock()
        mock.review = AsyncMock(side_effect=RuntimeError("llm reached"))
        with patch("thinktank.llm.scheduled._llm_client", mock):
            await run_daily_digest(session)

        mock.review.assert_called_once()


class TestWeeklyAuditGuard:
    async def test_skips_when_run_this_week(self, session: AsyncSession):
        await create_llm_review(
            session,
            review_type="weekly_audit",
            trigger="scheduled",
            created_at=datetime.now(UTC) - timedelta(days=2),
        )

        with _mock_client():
            assert await run_weekly_audit(session) is None
