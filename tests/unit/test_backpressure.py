"""Unit tests for backpressure module.

Tests BACKPRESSURE_JOB_TYPES membership and priority demotion math.
These are pure logic tests -- no database required.
"""

import pytest


class TestBackpressureJobTypes:
    """Test that BACKPRESSURE_JOB_TYPES contains the correct 10 discovery/fetch types."""

    def test_contains_all_10_discovery_types(self):
        """All 10 discovery/fetch job types from spec Section 6 must be present."""
        from src.thinktank.queue.backpressure import BACKPRESSURE_JOB_TYPES

        expected = {
            "discover_thinker",
            "refresh_due_sources",
            "fetch_podcast_feed",
            "scrape_substack",
            "fetch_youtube_channel",
            "fetch_guest_feed",
            "discover_guests_listennotes",
            "discover_guests_podcastindex",
            "search_youtube_appearances",
            "scan_for_candidates",
        }
        assert BACKPRESSURE_JOB_TYPES == expected

    def test_process_content_not_in_backpressure_types(self):
        """process_content is the monitored queue, not a backpressure target."""
        from src.thinktank.queue.backpressure import BACKPRESSURE_JOB_TYPES

        assert "process_content" not in BACKPRESSURE_JOB_TYPES

    def test_is_a_set(self):
        """Should be a set for O(1) membership checks."""
        from src.thinktank.queue.backpressure import BACKPRESSURE_JOB_TYPES

        assert isinstance(BACKPRESSURE_JOB_TYPES, set)


class TestPriorityDemotionMath:
    """Test the priority demotion arithmetic in isolation.

    These verify the formula: min(priority + 3, 10).
    """

    def test_priority_5_demoted_to_8(self):
        """Standard demotion: 5 + 3 = 8."""
        assert min(5 + 3, 10) == 8

    def test_priority_8_capped_at_10(self):
        """Cap at 10: 8 + 3 = 11, capped to 10."""
        assert min(8 + 3, 10) == 10

    def test_priority_1_demoted_to_4(self):
        """High priority demotion: 1 + 3 = 4."""
        assert min(1 + 3, 10) == 4

    def test_priority_10_stays_at_10(self):
        """Already lowest priority: 10 + 3 = 13, capped to 10."""
        assert min(10 + 3, 10) == 10

    def test_priority_7_demoted_to_10(self):
        """Edge case: 7 + 3 = 10, exactly the cap."""
        assert min(7 + 3, 10) == 10


class TestGetEffectivePriority:
    """Test get_effective_priority logic with mocked session."""

    async def test_non_backpressure_type_returns_unchanged(self):
        """Non-discovery job types should return original priority."""
        from unittest.mock import AsyncMock

        from src.thinktank.queue.backpressure import get_effective_priority
        from tests.factories import make_job

        mock_session = AsyncMock()
        job = make_job(job_type="process_content", priority=5)

        result = await get_effective_priority(mock_session, job)
        assert result == 5
        # Should not have queried queue depth
        mock_session.execute.assert_not_called()

    async def test_discovery_type_demoted_when_above_threshold(self):
        """Discovery job should be demoted by +3 when depth > threshold."""
        from unittest.mock import AsyncMock

        from src.thinktank.queue.backpressure import get_effective_priority
        from tests.factories import make_job

        mock_session = AsyncMock()
        # First call: get_queue_depth returns 501
        mock_depth_result = AsyncMock()
        mock_depth_result.scalar_one.return_value = 501
        # Second call: get threshold config returns 500
        mock_config_result = AsyncMock()
        mock_config_result.scalar_one_or_none.return_value = {"value": 500}

        mock_session.execute.side_effect = [mock_depth_result, mock_config_result]

        job = make_job(job_type="discover_thinker", priority=5)
        result = await get_effective_priority(mock_session, job)
        assert result == 8  # 5 + 3

    async def test_discovery_type_normal_when_below_80_percent(self):
        """Discovery job returns original priority when depth < 80% of threshold."""
        from unittest.mock import AsyncMock

        from src.thinktank.queue.backpressure import get_effective_priority
        from tests.factories import make_job

        mock_session = AsyncMock()
        # First call: get_queue_depth returns 399 (below 80% of 500)
        mock_depth_result = AsyncMock()
        mock_depth_result.scalar_one.return_value = 399
        # Second call: get threshold config returns 500
        mock_config_result = AsyncMock()
        mock_config_result.scalar_one_or_none.return_value = {"value": 500}

        mock_session.execute.side_effect = [mock_depth_result, mock_config_result]

        job = make_job(job_type="discover_thinker", priority=5)
        result = await get_effective_priority(mock_session, job)
        assert result == 5

    async def test_discovery_type_unchanged_in_hysteresis_band(self):
        """Discovery job in 80-100% band returns original priority."""
        from unittest.mock import AsyncMock

        from src.thinktank.queue.backpressure import get_effective_priority
        from tests.factories import make_job

        mock_session = AsyncMock()
        # First call: get_queue_depth returns 450 (between 400 and 500)
        mock_depth_result = AsyncMock()
        mock_depth_result.scalar_one.return_value = 450
        # Second call: get threshold config returns 500
        mock_config_result = AsyncMock()
        mock_config_result.scalar_one_or_none.return_value = {"value": 500}

        mock_session.execute.side_effect = [mock_depth_result, mock_config_result]

        job = make_job(job_type="discover_thinker", priority=5)
        result = await get_effective_priority(mock_session, job)
        assert result == 5

    async def test_defaults_threshold_to_500_when_no_config(self):
        """When max_pending_transcriptions config is missing, default to 500."""
        from unittest.mock import AsyncMock

        from src.thinktank.queue.backpressure import get_effective_priority
        from tests.factories import make_job

        mock_session = AsyncMock()
        # First call: get_queue_depth returns 501 (above default 500)
        mock_depth_result = AsyncMock()
        mock_depth_result.scalar_one.return_value = 501
        # Second call: no config found
        mock_config_result = AsyncMock()
        mock_config_result.scalar_one_or_none.return_value = None

        mock_session.execute.side_effect = [mock_depth_result, mock_config_result]

        job = make_job(job_type="discover_thinker", priority=5)
        result = await get_effective_priority(mock_session, job)
        assert result == 8  # 5 + 3, using default threshold of 500
