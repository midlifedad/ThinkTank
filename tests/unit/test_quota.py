"""Tests for discovery.quota -- daily candidate quota tracking.

Covers:
- check_daily_quota returns (True, 0, 20) when no candidates today
- check_daily_quota returns (False, 20, 20) when at limit
- check_daily_quota returns (True, 15, 20) when under limit
- check_daily_quota reads max_candidates_per_day from system_config
- check_daily_quota counts candidates with first_seen_at >= today midnight
- should_trigger_llm_review returns True at 80% of limit (16/20)
- should_trigger_llm_review returns False below 80% (15/20)
- get_pending_candidate_count counts pending_llm candidates
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from thinktank.discovery.quota import (
    check_daily_quota,
    get_pending_candidate_count,
    should_trigger_llm_review,
)


class TestShouldTriggerLlmReview:
    """Pure function -- no mocks needed."""

    def test_at_80_percent_returns_true(self):
        assert should_trigger_llm_review(16, 20) is True

    def test_above_80_percent_returns_true(self):
        assert should_trigger_llm_review(18, 20) is True

    def test_at_limit_returns_true(self):
        assert should_trigger_llm_review(20, 20) is True

    def test_below_80_percent_returns_false(self):
        assert should_trigger_llm_review(15, 20) is False

    def test_zero_candidates_returns_false(self):
        assert should_trigger_llm_review(0, 20) is False

    def test_exact_boundary(self):
        """80% of 10 = 8, so 8 should trigger."""
        assert should_trigger_llm_review(8, 10) is True
        assert should_trigger_llm_review(7, 10) is False


class TestCheckDailyQuota:
    """Mocked async tests for check_daily_quota."""

    async def test_no_candidates_today(self):
        """Returns (True, 0, 20) when no candidates today."""
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = 0
        mock_session.execute.return_value = mock_result

        with patch(
            "thinktank.discovery.quota.get_config_value",
            new_callable=AsyncMock,
            return_value=20,
        ):
            can_continue, count, limit = await check_daily_quota(mock_session)

        assert can_continue is True
        assert count == 0
        assert limit == 20

    async def test_at_limit(self):
        """Returns (False, 20, 20) when at limit."""
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = 20
        mock_session.execute.return_value = mock_result

        with patch(
            "thinktank.discovery.quota.get_config_value",
            new_callable=AsyncMock,
            return_value=20,
        ):
            can_continue, count, limit = await check_daily_quota(mock_session)

        assert can_continue is False
        assert count == 20
        assert limit == 20

    async def test_under_limit(self):
        """Returns (True, 15, 20) when under limit."""
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = 15
        mock_session.execute.return_value = mock_result

        with patch(
            "thinktank.discovery.quota.get_config_value",
            new_callable=AsyncMock,
            return_value=20,
        ):
            can_continue, count, limit = await check_daily_quota(mock_session)

        assert can_continue is True
        assert count == 15
        assert limit == 20

    async def test_reads_custom_limit_from_config(self):
        """Reads max_candidates_per_day from system_config."""
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = 5
        mock_session.execute.return_value = mock_result

        with patch(
            "thinktank.discovery.quota.get_config_value",
            new_callable=AsyncMock,
            return_value=50,
        ) as mock_get_config:
            can_continue, count, limit = await check_daily_quota(mock_session)

            mock_get_config.assert_called_once_with(
                mock_session, "max_candidates_per_day", 20
            )

        assert can_continue is True
        assert limit == 50

    async def test_null_count_treated_as_zero(self):
        """NULL count from DB (no rows) treated as 0."""
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_result

        with patch(
            "thinktank.discovery.quota.get_config_value",
            new_callable=AsyncMock,
            return_value=20,
        ):
            can_continue, count, limit = await check_daily_quota(mock_session)

        assert can_continue is True
        assert count == 0


class TestGetPendingCandidateCount:
    """Test get_pending_candidate_count."""

    async def test_returns_count(self):
        """Returns count of pending_llm candidates."""
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = 42
        mock_session.execute.return_value = mock_result

        result = await get_pending_candidate_count(mock_session)
        assert result == 42

    async def test_null_returns_zero(self):
        """NULL count returns 0."""
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_result

        result = await get_pending_candidate_count(mock_session)
        assert result == 0
