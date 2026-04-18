"""Integration tests for scan_for_candidates handler.

Tests name extraction from episode content, candidate creation with
quota enforcement, cascade pause behavior, and LLM review triggering.

Uses real PostgreSQL database with factory-generated test data.
Trigram functions (find_similar_thinkers, find_similar_candidates) are
real DB operations requiring the pg_trgm extension.
"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.factories import (
    create_candidate_thinker,
    create_content,
    create_job,
    create_source,
    create_system_config,
    create_thinker,
)
from thinktank.discovery.quota import check_daily_quota
from thinktank.handlers.scan_for_candidates import handle_scan_for_candidates
from thinktank.models.candidate import CandidateThinker
from thinktank.models.job import Job

pytestmark = pytest.mark.anyio


async def test_scan_creates_candidates(session: AsyncSession):
    """Content with guest names in title creates CandidateThinker rows."""
    await create_thinker(session, name="Host Person")
    source = await create_source(session)

    content1 = await create_content(session, source_id=source.id, title="Interview with John Smith")
    content2 = await create_content(session, source_id=source.id, title="Conversation: Jane Doe")
    content3 = await create_content(session, source_id=source.id, title="feat. Alice Walker on AI")
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

    result = await session.execute(select(CandidateThinker))
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
    await create_thinker(session, name="Host Person")
    _existing = await create_thinker(session, name="John Smith")
    source = await create_source(session)

    content = await create_content(session, source_id=source.id, title="Interview with John Smith")
    job = await create_job(session, job_type="scan_for_candidates", payload={"content_ids": [str(content.id)]})
    await session.commit()

    await handle_scan_for_candidates(session, job)

    # No CandidateThinker should be created for "John Smith"
    count = await session.scalar(select(func.count()).select_from(CandidateThinker))
    assert count == 0


async def test_scan_increments_existing_candidate(session: AsyncSession):
    """Names matching existing candidates get appearance_count incremented."""
    await create_thinker(session, name="Host Person")
    source = await create_source(session)

    existing_candidate = await create_candidate_thinker(
        session, name="john smith", normalized_name="john smith", appearance_count=2
    )
    original_last_seen = existing_candidate.last_seen_at

    content = await create_content(session, source_id=source.id, title="Interview with John Smith")
    job = await create_job(session, job_type="scan_for_candidates", payload={"content_ids": [str(content.id)]})
    await session.commit()

    await handle_scan_for_candidates(session, job)

    # Refresh candidate to see updates
    await session.refresh(existing_candidate)
    assert existing_candidate.appearance_count == 3
    assert existing_candidate.last_seen_at >= original_last_seen


async def test_quota_pause(session: AsyncSession):
    """Quota exhaustion stops candidate creation."""
    await create_thinker(session, name="Host Person")
    source = await create_source(session)
    content = await create_content(session, source_id=source.id, title="Interview with John Smith")
    job = await create_job(session, job_type="scan_for_candidates", payload={"content_ids": [str(content.id)]})
    await session.commit()

    # Mock check_daily_quota to return exhausted
    with patch(
        "thinktank.handlers.scan_for_candidates.check_daily_quota", new_callable=AsyncMock, return_value=(False, 20, 20)
    ):
        await handle_scan_for_candidates(session, job)

    count = await session.scalar(select(func.count()).select_from(CandidateThinker))
    assert count == 0


async def test_quota_triggers_review(session: AsyncSession):
    """80% quota triggers llm_approval_check job creation."""
    await create_thinker(session, name="Host Person")
    source = await create_source(session)
    content = await create_content(session, source_id=source.id, title="Interview with John Smith")
    job = await create_job(session, job_type="scan_for_candidates", payload={"content_ids": [str(content.id)]})
    await session.commit()

    # Mock should_trigger_llm_review to always return True
    with patch("thinktank.handlers.scan_for_candidates.should_trigger_llm_review", return_value=True):
        await handle_scan_for_candidates(session, job)

    # Check for llm_approval_check job
    result = await session.execute(select(Job).where(Job.job_type == "llm_approval_check"))
    review_jobs = result.scalars().all()
    assert len(review_jobs) >= 1

    review_job = review_jobs[0]
    assert review_job.payload.get("review_type") == "candidate_review"
    assert review_job.priority == 1


async def test_cascade_pause_pending_queue(session: AsyncSession):
    """Pending queue > 40 causes early return with no candidates created."""
    await create_thinker(session, name="Host Person")
    source = await create_source(session)
    content = await create_content(session, source_id=source.id, title="Interview with John Smith")
    job = await create_job(session, job_type="scan_for_candidates", payload={"content_ids": [str(content.id)]})
    await session.commit()

    # Mock pending count > 40
    with patch(
        "thinktank.handlers.scan_for_candidates.get_pending_candidate_count", new_callable=AsyncMock, return_value=41
    ):
        await handle_scan_for_candidates(session, job)

    count = await session.scalar(select(func.count()).select_from(CandidateThinker))
    assert count == 0


class TestConcurrentDailyQuota:
    """TOCTOU regression: concurrent quota checks must not breach daily_limit.

    Sources: INTEGRATIONS-REVIEW H-02, HANDLERS-REVIEW ME-04.
    Without serialization, two concurrent check_daily_quota + insert flows
    both see the same count, both pass the limit check, both insert — blowing
    past max_candidates_per_day.
    """

    async def test_concurrent_quota_inserts_respect_limit(self, session_factory):
        """Limit=3 with 2 pre-seeded; two concurrent workers, only one can win."""
        async with session_factory() as setup:
            await create_system_config(setup, key="max_candidates_per_day", value={"value": 3})
            # Pre-seed 2 candidates "today"
            await create_candidate_thinker(setup, name="Existing 1", normalized_name="existing 1")
            await create_candidate_thinker(setup, name="Existing 2", normalized_name="existing 2")
            await setup.commit()

        async def check_and_insert(worker_idx: int) -> bool:
            """Simulate the handler critical section: quota check + insert + commit."""
            async with session_factory() as s:
                can_continue, _count, _limit = await check_daily_quota(s)
                if can_continue:
                    s.add(CandidateThinker(name=f"New {worker_idx}", normalized_name=f"new {worker_idx}"))
                await s.commit()
                return can_continue

        # Two concurrent workers: only 1 slot left (3 - 2 = 1)
        results = await asyncio.gather(*[check_and_insert(i) for i in range(2)])

        # Exactly one worker should see can_continue=True and insert
        assert sum(1 for r in results if r) == 1, f"Expected exactly 1 worker to pass the quota check, got {results}"

        # DB should hold exactly 3 candidates (2 pre-seeded + 1 new), never 4
        async with session_factory() as check:
            total = await check.scalar(select(func.count()).select_from(CandidateThinker))
        assert total == 3, f"Expected 3 candidates total (quota=3), got {total}"
