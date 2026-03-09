"""Unit tests for rate limiter module.

Tests the sliding window concept and helper logic.
Since rate_limiter depends on DB, unit tests focus on
validating the module's imports and constants are correct.
Integration tests cover the actual DB queries.
"""

from unittest.mock import AsyncMock, patch

import pytest


class TestGetRateLimitConfig:
    """Test get_rate_limit_config helper logic."""

    async def test_returns_none_when_no_config_found(self):
        """When no system_config row exists, should return None."""
        from src.thinktank.queue.rate_limiter import get_rate_limit_config

        mock_session = AsyncMock()
        mock_result = AsyncMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_result

        result = await get_rate_limit_config(mock_session, "listennotes")
        assert result is None

    async def test_extracts_int_from_jsonb_dict(self):
        """When JSONB value is {"value": 100}, should extract 100."""
        from src.thinktank.queue.rate_limiter import get_rate_limit_config

        mock_session = AsyncMock()
        mock_result = AsyncMock()
        mock_result.scalar_one_or_none.return_value = {"value": 100}
        mock_session.execute.return_value = mock_result

        result = await get_rate_limit_config(mock_session, "listennotes")
        assert result == 100

    async def test_extracts_int_from_raw_int(self):
        """When JSONB value is stored as a raw integer, should return it directly."""
        from src.thinktank.queue.rate_limiter import get_rate_limit_config

        mock_session = AsyncMock()
        mock_result = AsyncMock()
        mock_result.scalar_one_or_none.return_value = 50
        mock_session.execute.return_value = mock_result

        result = await get_rate_limit_config(mock_session, "youtube")
        assert result == 50

    async def test_constructs_correct_config_key(self):
        """Should query system_config with key '{api_name}_calls_per_hour'."""
        from src.thinktank.queue.rate_limiter import get_rate_limit_config

        mock_session = AsyncMock()
        mock_result = AsyncMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_result

        await get_rate_limit_config(mock_session, "listennotes")

        # Verify execute was called (the actual SQL is tested in integration)
        mock_session.execute.assert_called_once()


class TestCheckAndAcquireRateLimit:
    """Test check_and_acquire_rate_limit logic paths."""

    async def test_returns_true_when_no_config_exists(self):
        """Fail-open: no config = no limit, should return True."""
        from src.thinktank.queue.rate_limiter import check_and_acquire_rate_limit

        mock_session = AsyncMock()
        # First call: COUNT returns 0
        mock_count_result = AsyncMock()
        mock_count_result.scalar_one.return_value = 0
        # Second call: config returns None (no limit configured)
        mock_config_result = AsyncMock()
        mock_config_result.scalar_one_or_none.return_value = None

        mock_session.execute.side_effect = [mock_count_result, mock_config_result]

        result = await check_and_acquire_rate_limit(
            mock_session, "listennotes", "worker-1"
        )
        assert result is True

    async def test_returns_false_when_at_limit(self):
        """When count >= configured limit, should return False."""
        from src.thinktank.queue.rate_limiter import check_and_acquire_rate_limit

        mock_session = AsyncMock()
        # First call: COUNT returns 100 (at limit)
        mock_count_result = AsyncMock()
        mock_count_result.scalar_one.return_value = 100
        # Second call: config returns 100
        mock_config_result = AsyncMock()
        mock_config_result.scalar_one_or_none.return_value = {"value": 100}

        mock_session.execute.side_effect = [mock_count_result, mock_config_result]

        result = await check_and_acquire_rate_limit(
            mock_session, "listennotes", "worker-1"
        )
        assert result is False

    async def test_returns_true_and_records_when_under_limit(self):
        """When count < configured limit, should insert a row and return True."""
        from src.thinktank.queue.rate_limiter import check_and_acquire_rate_limit

        mock_session = AsyncMock()
        # First call: COUNT returns 5 (under limit)
        mock_count_result = AsyncMock()
        mock_count_result.scalar_one.return_value = 5
        # Second call: config returns 100
        mock_config_result = AsyncMock()
        mock_config_result.scalar_one_or_none.return_value = {"value": 100}

        mock_session.execute.side_effect = [mock_count_result, mock_config_result]

        result = await check_and_acquire_rate_limit(
            mock_session, "listennotes", "worker-1"
        )
        assert result is True
        # Should have added a RateLimitUsage row
        mock_session.add.assert_called_once()
        mock_session.flush.assert_called_once()
