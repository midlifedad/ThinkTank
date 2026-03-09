"""Integration tests for LLM timeout escalation.

Tests escalation logic against a real PostgreSQL test database.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from src.thinktank.llm.escalation import escalate_timed_out_reviews
from src.thinktank.models.review import LLMReview

from tests.factories import create_job, create_system_config


def _now() -> datetime:
    """Return current UTC time as timezone-naive datetime."""
    return datetime.now(UTC).replace(tzinfo=None)


@pytest.mark.asyncio
async def test_escalation_flags_timed_out_job(session: AsyncSession):
    """Jobs older than timeout hours get needs_human_review flag set."""
    # Setup: config + job older than timeout
    await create_system_config(
        session, key="llm_timeout_hours", value=2, set_by="test"
    )
    three_hours_ago = _now() - timedelta(hours=3)
    job = await create_job(
        session,
        status="awaiting_llm",
        job_type="approve_thinker",
        created_at=three_hours_ago,
    )
    await session.commit()

    # Act
    count = await escalate_timed_out_reviews(session)
    await session.commit()

    # Assert
    assert count == 1
    # Verify payload flag was set
    result = await session.execute(
        text("SELECT payload->>'needs_human_review' FROM jobs WHERE id = :id"),
        {"id": job.id},
    )
    flag = result.scalar_one()
    assert flag == "true"

    # Verify LLMReview row created
    reviews = (
        await session.execute(
            select(LLMReview).where(LLMReview.review_type == "timeout_escalation")
        )
    ).scalars().all()
    assert len(reviews) == 1
    assert reviews[0].decision == "escalate_to_human"
    assert str(job.id) in reviews[0].context_snapshot["job_id"]


@pytest.mark.asyncio
async def test_escalation_skips_recent_job(session: AsyncSession):
    """Jobs younger than timeout hours are NOT escalated."""
    await create_system_config(
        session, key="llm_timeout_hours", value=2, set_by="test"
    )
    one_hour_ago = _now() - timedelta(hours=1)
    await create_job(
        session,
        status="awaiting_llm",
        job_type="approve_thinker",
        created_at=one_hour_ago,
    )
    await session.commit()

    count = await escalate_timed_out_reviews(session)
    await session.commit()

    assert count == 0

    # No LLMReview rows
    reviews = (
        await session.execute(
            select(LLMReview).where(LLMReview.review_type == "timeout_escalation")
        )
    ).scalars().all()
    assert len(reviews) == 0


@pytest.mark.asyncio
async def test_escalation_skips_already_flagged(session: AsyncSession):
    """Jobs already flagged with needs_human_review are NOT re-escalated."""
    await create_system_config(
        session, key="llm_timeout_hours", value=2, set_by="test"
    )
    three_hours_ago = _now() - timedelta(hours=3)
    await create_job(
        session,
        status="awaiting_llm",
        job_type="approve_thinker",
        created_at=three_hours_ago,
        payload={"needs_human_review": True},
    )
    await session.commit()

    count = await escalate_timed_out_reviews(session)
    await session.commit()

    assert count == 0

    # No new LLMReview rows
    reviews = (
        await session.execute(
            select(LLMReview).where(LLMReview.review_type == "timeout_escalation")
        )
    ).scalars().all()
    assert len(reviews) == 0


@pytest.mark.asyncio
async def test_escalation_returns_count(session: AsyncSession):
    """Escalation returns the count of escalated jobs."""
    await create_system_config(
        session, key="llm_timeout_hours", value=2, set_by="test"
    )
    three_hours_ago = _now() - timedelta(hours=3)
    one_hour_ago = _now() - timedelta(hours=1)

    # Two timed-out jobs
    await create_job(
        session,
        status="awaiting_llm",
        job_type="approve_thinker",
        created_at=three_hours_ago,
    )
    await create_job(
        session,
        status="awaiting_llm",
        job_type="approve_source",
        created_at=three_hours_ago,
    )
    # One recent job (should not be escalated)
    await create_job(
        session,
        status="awaiting_llm",
        job_type="approve_thinker",
        created_at=one_hour_ago,
    )
    await session.commit()

    count = await escalate_timed_out_reviews(session)
    await session.commit()

    assert count == 2

    # Verify exactly 2 LLMReview rows
    reviews = (
        await session.execute(
            select(LLMReview).where(LLMReview.review_type == "timeout_escalation")
        )
    ).scalars().all()
    assert len(reviews) == 2
