"""Integration tests for scheduled LLM tasks.

Tests run_health_check, run_daily_digest, and run_weekly_audit
against a real PostgreSQL test database with mocked LLM client.
"""

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from thinktank.llm.scheduled import (
    run_daily_digest,
    run_health_check,
    run_weekly_audit,
)
from thinktank.llm.schemas import (
    DailyDigestResponse,
    HealthCheckResponse,
    WeeklyAuditResponse,
)
from thinktank.models.review import LLMReview


@pytest.mark.asyncio
async def test_health_check_creates_review(session: AsyncSession):
    """run_health_check creates an llm_reviews row with correct type and decision."""
    mock_response = HealthCheckResponse(
        status="healthy",
        findings=["All systems nominal"],
    )
    mock_review = AsyncMock(return_value=(mock_response, 150, 1200))

    with patch("thinktank.llm.scheduled._llm_client") as mock_client:
        mock_client.review = mock_review
        mock_client.model = "claude-sonnet-4-20250514"
        result = await run_health_check(session)
        await session.commit()

    assert result is not None
    assert result.review_type == "health_check"
    assert result.decision == "healthy"
    assert result.tokens_used == 150
    assert result.duration_ms == 1200

    # Verify persisted in DB
    reviews = (await session.execute(select(LLMReview).where(LLMReview.review_type == "health_check"))).scalars().all()
    assert len(reviews) == 1


@pytest.mark.asyncio
async def test_daily_digest_creates_review(session: AsyncSession):
    """run_daily_digest creates an llm_reviews row with correct type."""
    mock_response = DailyDigestResponse(
        summary="Quiet day with steady ingestion.",
        highlights=["10 new episodes processed"],
    )
    mock_review = AsyncMock(return_value=(mock_response, 200, 1500))

    with patch("thinktank.llm.scheduled._llm_client") as mock_client:
        mock_client.review = mock_review
        mock_client.model = "claude-sonnet-4-20250514"
        result = await run_daily_digest(session)
        await session.commit()

    assert result is not None
    assert result.review_type == "daily_digest"
    assert result.decision == "digest_generated"
    assert "Quiet day" in result.decision_reasoning

    # Verify persisted in DB
    reviews = (await session.execute(select(LLMReview).where(LLMReview.review_type == "daily_digest"))).scalars().all()
    assert len(reviews) == 1


@pytest.mark.asyncio
async def test_weekly_audit_creates_review(session: AsyncSession):
    """run_weekly_audit creates an llm_reviews row with correct type."""
    mock_response = WeeklyAuditResponse(
        summary="Strong growth week with 500 new episodes.",
    )
    mock_review = AsyncMock(return_value=(mock_response, 300, 2000))

    with patch("thinktank.llm.scheduled._llm_client") as mock_client:
        mock_client.review = mock_review
        mock_client.model = "claude-sonnet-4-20250514"
        result = await run_weekly_audit(session)
        await session.commit()

    assert result is not None
    assert result.review_type == "weekly_audit"
    assert result.decision == "audit_complete"
    assert "Strong growth" in result.decision_reasoning

    # Verify persisted in DB
    reviews = (await session.execute(select(LLMReview).where(LLMReview.review_type == "weekly_audit"))).scalars().all()
    assert len(reviews) == 1


@pytest.mark.asyncio
async def test_scheduled_task_handles_api_error(session: AsyncSession):
    """Scheduled task returns None and does NOT crash on LLM failure."""
    mock_review = AsyncMock(side_effect=Exception("Anthropic API down"))

    with patch("thinktank.llm.scheduled._llm_client") as mock_client:
        mock_client.review = mock_review
        mock_client.model = "claude-sonnet-4-20250514"
        result = await run_health_check(session)
        await session.commit()

    assert result is None

    # No LLMReview rows created
    reviews = (await session.execute(select(LLMReview).where(LLMReview.review_type == "health_check"))).scalars().all()
    assert len(reviews) == 0


@pytest.mark.asyncio
async def test_health_check_with_config_adjustments(session: AsyncSession):
    """Health check with config_adjustments logs the adjustments."""
    mock_response = HealthCheckResponse(
        status="issues_detected",
        findings=["High error rate on source X"],
        config_adjustments={"error_threshold": 10},
    )
    mock_review = AsyncMock(return_value=(mock_response, 180, 1300))

    with patch("thinktank.llm.scheduled._llm_client") as mock_client:
        mock_client.review = mock_review
        mock_client.model = "claude-sonnet-4-20250514"
        result = await run_health_check(session)
        await session.commit()

    assert result is not None
    assert result.decision == "issues_detected"

    # Verify the response was stored
    import json

    stored = json.loads(result.llm_response)
    assert stored["config_adjustments"] == {"error_threshold": 10}
