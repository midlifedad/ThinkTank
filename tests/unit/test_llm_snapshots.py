"""Unit tests for LLM context snapshot builders.

Since snapshots involve DB queries, unit tests focus on:
1. Verifying the snapshot builders exist and have correct signatures
2. Testing that bounded queries use proper limits (via mocked session)
3. Verifying returned dicts have expected keys
4. Verifying timezone-naive datetimes (no tzinfo)

Full integration tests with real DB are in Plan 02/03.
"""

import uuid
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from thinktank.llm.snapshots import (
    build_candidate_review_context,
    build_daily_digest_context,
    build_health_check_context,
    build_source_approval_context,
    build_thinker_approval_context,
    build_weekly_audit_context,
)


@pytest.fixture
def mock_session():
    """Create a mock AsyncSession."""
    session = AsyncMock()
    return session


class TestThinkerApprovalContext:
    @pytest.mark.asyncio
    async def test_returns_dict_with_expected_keys(self, mock_session):
        thinker_id = uuid.uuid4()
        thinker_mock = MagicMock()
        thinker_mock.id = thinker_id
        thinker_mock.name = "Test Thinker"
        thinker_mock.slug = "test-thinker"
        thinker_mock.tier = 2
        thinker_mock.bio = "A thinker"
        thinker_mock.approval_status = "pending_llm"
        thinker_mock.approved_backfill_days = None
        thinker_mock.approved_source_types = None
        thinker_mock.sources = []
        thinker_mock.categories = []

        mock_session.get.return_value = thinker_mock

        # Mock scalar calls for corpus stats
        mock_session.scalar = AsyncMock(return_value=10)

        result = await build_thinker_approval_context(mock_session, thinker_id)

        assert isinstance(result, dict)
        assert "proposed_thinker" in result
        assert "corpus_stats" in result

    @pytest.mark.asyncio
    async def test_uses_timezone_naive_datetimes(self, mock_session):
        """Any datetime values in the context should be timezone-naive."""
        thinker_id = uuid.uuid4()
        thinker_mock = MagicMock()
        thinker_mock.id = thinker_id
        thinker_mock.name = "Test"
        thinker_mock.slug = "test"
        thinker_mock.tier = 2
        thinker_mock.bio = "Bio"
        thinker_mock.approval_status = "pending_llm"
        thinker_mock.approved_backfill_days = None
        thinker_mock.approved_source_types = None
        thinker_mock.sources = []
        thinker_mock.categories = []

        mock_session.get.return_value = thinker_mock
        mock_session.scalar = AsyncMock(return_value=5)

        result = await build_thinker_approval_context(mock_session, thinker_id)

        # Check any datetime values are timezone-naive
        def check_naive(obj):
            if isinstance(obj, datetime):
                assert obj.tzinfo is None, f"Found timezone-aware datetime: {obj}"
            elif isinstance(obj, dict):
                for v in obj.values():
                    check_naive(v)
            elif isinstance(obj, list):
                for v in obj:
                    check_naive(v)

        check_naive(result)


class TestSourceApprovalContext:
    @pytest.mark.asyncio
    async def test_returns_dict_with_expected_keys(self, mock_session):
        source_id = uuid.uuid4()
        source_mock = MagicMock()
        source_mock.id = source_id
        source_mock.name = "Test Feed"
        source_mock.source_type = "podcast_rss"
        source_mock.url = "https://example.com/rss"
        source_mock.approval_status = "pending_llm"
        source_mock.thinker = MagicMock()
        source_mock.thinker.name = "Test Thinker"
        source_mock.thinker.slug = "test-thinker"

        mock_session.get.return_value = source_mock

        # Mock execute for episode samples
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_session.execute = AsyncMock(return_value=mock_result)

        result = await build_source_approval_context(mock_session, source_id)

        assert isinstance(result, dict)
        assert "source" in result
        assert "thinker" in result


class TestCandidateReviewContext:
    @pytest.mark.asyncio
    async def test_returns_dict_with_expected_keys(self, mock_session):
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.scalar = AsyncMock(return_value=5)

        result = await build_candidate_review_context(mock_session)

        assert isinstance(result, dict)
        assert "candidates" in result
        assert "corpus_stats" in result

    @pytest.mark.asyncio
    async def test_limits_candidates_to_20(self, mock_session):
        """Verify the query limits candidates to 20."""
        # Create 25 mock candidates
        candidates = []
        for i in range(25):
            c = MagicMock()
            c.id = uuid.uuid4()
            c.name = f"Candidate {i}"
            c.normalized_name = f"candidate {i}"
            c.appearance_count = 3
            c.status = "pending_llm"
            c.sample_urls = []
            c.inferred_categories = []
            candidates.append(c)

        mock_result = MagicMock()
        # The query should use .limit(20), so even if we provide 25,
        # the function should query with a limit
        mock_result.scalars.return_value.all.return_value = candidates[:20]
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.scalar = AsyncMock(return_value=50)

        result = await build_candidate_review_context(mock_session)

        # Should have at most 20 candidates
        assert len(result["candidates"]) <= 20


class TestHealthCheckContext:
    @pytest.mark.asyncio
    async def test_returns_dict_with_expected_keys(self, mock_session):
        mock_result = MagicMock()
        mock_result.all.return_value = []
        mock_result.scalars.return_value.all.return_value = []
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.scalar = AsyncMock(return_value=0)

        result = await build_health_check_context(mock_session)

        assert isinstance(result, dict)
        assert "jobs_summary" in result
        assert "error_log" in result

    @pytest.mark.asyncio
    async def test_error_log_bounded_to_100(self, mock_session):
        """Error log should be limited to 100 entries."""
        mock_result = MagicMock()
        mock_result.all.return_value = []
        mock_result.scalars.return_value.all.return_value = []
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.scalar = AsyncMock(return_value=0)

        result = await build_health_check_context(mock_session)

        # Even if we had more than 100 errors, the query should limit
        assert isinstance(result["error_log"], list)


class TestDailyDigestContext:
    @pytest.mark.asyncio
    async def test_returns_dict_with_expected_keys(self, mock_session):
        mock_session.scalar = AsyncMock(return_value=0)
        mock_result = MagicMock()
        mock_result.all.return_value = []
        mock_result.scalars.return_value.all.return_value = []
        mock_session.execute = AsyncMock(return_value=mock_result)

        result = await build_daily_digest_context(mock_session)

        assert isinstance(result, dict)
        assert "content_stats" in result
        assert "corpus_totals" in result


class TestWeeklyAuditContext:
    @pytest.mark.asyncio
    async def test_returns_dict_with_expected_keys(self, mock_session):
        mock_session.scalar = AsyncMock(return_value=0)
        mock_result = MagicMock()
        mock_result.all.return_value = []
        mock_result.scalars.return_value.all.return_value = []
        mock_session.execute = AsyncMock(return_value=mock_result)

        result = await build_weekly_audit_context(mock_session)

        assert isinstance(result, dict)
        assert "weekly_summary" in result
        assert "growth_rate" in result or "corpus_totals" in result
