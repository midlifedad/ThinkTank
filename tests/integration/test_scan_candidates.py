"""Integration tests for scan_for_candidates handler.

Tests name extraction from episode content, candidate creation with
quota enforcement, cascade pause behavior, and LLM review triggering.

Uses real PostgreSQL database with factory-generated test data.
Trigram functions (find_similar_thinkers, find_similar_candidates) are
real DB operations requiring the pg_trgm extension.
"""

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from thinktank.handlers.scan_for_candidates import handle_scan_for_candidates
from thinktank.models.candidate import CandidateThinker
from thinktank.models.job import Job
from tests.factories import (
    create_candidate_thinker,
    create_content,
    create_job,
    create_source,
    create_thinker,
)

pytestmark = pytest.mark.anyio


async def test_scan_creates_candidates(session: AsyncSession):
    """Content with guest names in title creates CandidateThinker rows."""
    owner = await create_thinker(session, name="Host Person")
    source = await create_source(session, thinker_id=owner.id)

    content1 = await create_content(
        session,
        source_id=source.id,
        source_owner_id=owner.id,
        title="Interview with John Smith",
    )
    content2 = await create_content(
        session,
        source_id=source.id,
        source_owner_id=owner.id,
        title="Conversation: Jane Doe",
    )
    content3 = await create_content(
        session,
        source_id=source.id,
        source_owner_id=owner.id,
        title="feat. Alice Walker on AI",
    )
    job = await create_job(
        session,
        job_type="scan_for_candidates",
        payload={
            "content_ids": [
                str(content1.id),
                str(content2.id),
                str(content3.id),
            ]
        },
    )
    await session.commit()

    await handle_scan_for_candidates(session, job)

    result = await session.execute(
        select(CandidateThinker)
    )
    candidates = result.scalars().all()

    # Should have extracted at least the guest names
    candidate_names = {c.normalized_name for c in candidates}
    assert "john smith" in candidate_names
    assert "jane doe" in candidate_names
    assert "alice walker" in candidate_names

    # All should have status pending_llm
    for c in candidates:
        assert c.status == "pending_llm"
        assert c.appearance_count >= 1


async def test_scan_skips_existing_thinkers(session: AsyncSession):
    """Names matching existing thinkers via trigram are skipped."""
    # Create an existing thinker named "John Smith"
    owner = await create_thinker(session, name="Host Person")
    _existing = await create_thinker(session, name="John Smith")
    source = await create_source(session, thinker_id=owner.id)

    content = await create_content(
        session,
        source_id=source.id,
        source_owner_id=owner.id,
        title="Interview with John Smith",
    )
    job = await create_job(
        session,
        job_type="scan_for_candidates",
        payload={"content_ids": [str(content.id)]},
    )
    await session.commit()

    await handle_scan_for_candidates(session, job)

    # No CandidateThinker should be created for "John Smith"
    count = await session.scalar(
        select(func.count()).select_from(CandidateThinker)
    )
    assert count == 0


async def test_scan_increments_existing_candidate(session: AsyncSession):
    """Names matching existing candidates get appearance_count incremented."""
    owner = await create_thinker(session, name="Host Person")
    source = await create_source(session, thinker_id=owner.id)

    existing_candidate = await create_candidate_thinker(
        session,
        name="john smith",
        normalized_name="john smith",
        appearance_count=2,
    )
    original_last_seen = existing_candidate.last_seen_at

    content = await create_content(
        session,
        source_id=source.id,
        source_owner_id=owner.id,
        title="Interview with John Smith",
    )
    job = await create_job(
        session,
        job_type="scan_for_candidates",
        payload={"content_ids": [str(content.id)]},
    )
    await session.commit()

    await handle_scan_for_candidates(session, job)

    # Refresh candidate to see updates
    await session.refresh(existing_candidate)
    assert existing_candidate.appearance_count == 3
    assert existing_candidate.last_seen_at >= original_last_seen


async def test_quota_pause(session: AsyncSession):
    """Quota exhaustion stops candidate creation."""
    owner = await create_thinker(session, name="Host Person")
    source = await create_source(session, thinker_id=owner.id)
    content = await create_content(
        session,
        source_id=source.id,
        source_owner_id=owner.id,
        title="Interview with John Smith",
    )
    job = await create_job(
        session,
        job_type="scan_for_candidates",
        payload={"content_ids": [str(content.id)]},
    )
    await session.commit()

    # Mock check_daily_quota to return exhausted
    with patch(
        "thinktank.handlers.scan_for_candidates.check_daily_quota",
        new_callable=AsyncMock,
        return_value=(False, 20, 20),
    ):
        await handle_scan_for_candidates(session, job)

    count = await session.scalar(
        select(func.count()).select_from(CandidateThinker)
    )
    assert count == 0


async def test_quota_triggers_review(session: AsyncSession):
    """80% quota triggers llm_approval_check job creation."""
    owner = await create_thinker(session, name="Host Person")
    source = await create_source(session, thinker_id=owner.id)
    content = await create_content(
        session,
        source_id=source.id,
        source_owner_id=owner.id,
        title="Interview with John Smith",
    )
    job = await create_job(
        session,
        job_type="scan_for_candidates",
        payload={"content_ids": [str(content.id)]},
    )
    await session.commit()

    # Mock should_trigger_llm_review to always return True
    with patch(
        "thinktank.handlers.scan_for_candidates.should_trigger_llm_review",
        return_value=True,
    ):
        await handle_scan_for_candidates(session, job)

    # Check for llm_approval_check job
    result = await session.execute(
        select(Job).where(Job.job_type == "llm_approval_check")
    )
    review_jobs = result.scalars().all()
    assert len(review_jobs) >= 1

    review_job = review_jobs[0]
    assert review_job.payload.get("review_type") == "candidate_review"
    assert review_job.priority == 1


async def test_cascade_pause_pending_queue(session: AsyncSession):
    """Pending queue > 40 causes early return with no candidates created."""
    owner = await create_thinker(session, name="Host Person")
    source = await create_source(session, thinker_id=owner.id)
    content = await create_content(
        session,
        source_id=source.id,
        source_owner_id=owner.id,
        title="Interview with John Smith",
    )
    job = await create_job(
        session,
        job_type="scan_for_candidates",
        payload={"content_ids": [str(content.id)]},
    )
    await session.commit()

    # Mock pending count > 40
    with patch(
        "thinktank.handlers.scan_for_candidates.get_pending_candidate_count",
        new_callable=AsyncMock,
        return_value=41,
    ):
        await handle_scan_for_candidates(session, job)

    count = await session.scalar(
        select(func.count()).select_from(CandidateThinker)
    )
    assert count == 0
