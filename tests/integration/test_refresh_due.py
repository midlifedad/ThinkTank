"""Integration tests for refresh_due_sources handler.

Tests tier-based scheduling, source eligibility, and discovery orchestration.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from thinktank.handlers.refresh_due_sources import handle_refresh_due_sources
from thinktank.models.job import Job
from tests.factories import create_job, create_source, create_thinker

pytestmark = pytest.mark.anyio


def _now() -> datetime:
    """Return current UTC time as timezone-aware datetime (TIMESTAMPTZ)."""
    return datetime.now(UTC)


async def test_due_source_gets_job(session: AsyncSession):
    """Source with last_fetched 25h ago, refresh_interval_hours=24 -> fetch job created."""
    thinker = await create_thinker(session)
    source = await create_source(
        session,
        thinker_id=thinker.id,
        approval_status="approved",
        active=True,
        refresh_interval_hours=24,
        last_fetched=_now() - timedelta(hours=25),
    )
    trigger_job = await create_job(
        session,
        job_type="refresh_due_sources",
        payload={},
    )
    await session.commit()

    await handle_refresh_due_sources(session, trigger_job)

    result = await session.execute(
        select(Job).where(
            Job.job_type == "fetch_podcast_feed",
            Job.payload["source_id"].astext == str(source.id),
        )
    )
    fetch_jobs = result.scalars().all()
    assert len(fetch_jobs) == 1
    assert fetch_jobs[0].priority == 2
    assert fetch_jobs[0].status == "pending"


async def test_not_due_source_skipped(session: AsyncSession):
    """Source with last_fetched 5h ago, refresh_interval_hours=24 -> no job created."""
    thinker = await create_thinker(session)
    await create_source(
        session,
        thinker_id=thinker.id,
        approval_status="approved",
        active=True,
        refresh_interval_hours=24,
        last_fetched=_now() - timedelta(hours=5),
    )
    trigger_job = await create_job(
        session,
        job_type="refresh_due_sources",
        payload={},
    )
    await session.commit()

    await handle_refresh_due_sources(session, trigger_job)

    result = await session.execute(
        select(Job).where(Job.job_type == "fetch_podcast_feed")
    )
    fetch_jobs = result.scalars().all()
    assert len(fetch_jobs) == 0


async def test_never_fetched_source_due(session: AsyncSession):
    """Source with last_fetched=NULL -> job created (first fetch)."""
    thinker = await create_thinker(session)
    source = await create_source(
        session,
        thinker_id=thinker.id,
        approval_status="approved",
        active=True,
        refresh_interval_hours=24,
        last_fetched=None,
    )
    trigger_job = await create_job(
        session,
        job_type="refresh_due_sources",
        payload={},
    )
    await session.commit()

    await handle_refresh_due_sources(session, trigger_job)

    result = await session.execute(
        select(Job).where(Job.job_type == "fetch_podcast_feed")
    )
    fetch_jobs = result.scalars().all()
    assert len(fetch_jobs) == 1
    assert fetch_jobs[0].payload["source_id"] == str(source.id)


async def test_unapproved_source_not_due(session: AsyncSession):
    """Source with approval_status='pending_llm' -> no job even if never fetched."""
    thinker = await create_thinker(session)
    await create_source(
        session,
        thinker_id=thinker.id,
        approval_status="pending_llm",
        active=True,
        refresh_interval_hours=24,
        last_fetched=None,
    )
    trigger_job = await create_job(
        session,
        job_type="refresh_due_sources",
        payload={},
    )
    await session.commit()

    await handle_refresh_due_sources(session, trigger_job)

    result = await session.execute(
        select(Job).where(Job.job_type == "fetch_podcast_feed")
    )
    assert len(result.scalars().all()) == 0


async def test_inactive_source_not_due(session: AsyncSession):
    """Source with active=False -> no job."""
    thinker = await create_thinker(session)
    await create_source(
        session,
        thinker_id=thinker.id,
        approval_status="approved",
        active=False,
        refresh_interval_hours=24,
        last_fetched=None,
    )
    trigger_job = await create_job(
        session,
        job_type="refresh_due_sources",
        payload={},
    )
    await session.commit()

    await handle_refresh_due_sources(session, trigger_job)

    result = await session.execute(
        select(Job).where(Job.job_type == "fetch_podcast_feed")
    )
    assert len(result.scalars().all()) == 0


async def test_orchestrator_creates_jobs(session: AsyncSession):
    """3 approved sources with staggered refresh times, 2 are due -> exactly 2 fetch jobs."""
    thinker = await create_thinker(session)

    # Source 1: due (last_fetched 25h ago, interval 24h)
    source1 = await create_source(
        session,
        thinker_id=thinker.id,
        approval_status="approved",
        active=True,
        refresh_interval_hours=24,
        last_fetched=_now() - timedelta(hours=25),
    )

    # Source 2: due (never fetched)
    source2 = await create_source(
        session,
        thinker_id=thinker.id,
        approval_status="approved",
        active=True,
        refresh_interval_hours=6,
        last_fetched=None,
    )

    # Source 3: NOT due (last_fetched 1h ago, interval 24h)
    await create_source(
        session,
        thinker_id=thinker.id,
        approval_status="approved",
        active=True,
        refresh_interval_hours=24,
        last_fetched=_now() - timedelta(hours=1),
    )

    trigger_job = await create_job(
        session,
        job_type="refresh_due_sources",
        payload={},
    )
    await session.commit()

    await handle_refresh_due_sources(session, trigger_job)

    result = await session.execute(
        select(Job).where(Job.job_type == "fetch_podcast_feed")
    )
    fetch_jobs = result.scalars().all()
    assert len(fetch_jobs) == 2

    # Verify the correct sources got jobs
    source_ids_with_jobs = {j.payload["source_id"] for j in fetch_jobs}
    assert str(source1.id) in source_ids_with_jobs
    assert str(source2.id) in source_ids_with_jobs


async def test_existing_inflight_job_dedup(session: AsyncSession):
    """Source already has a pending fetch job -> refresh does NOT double-enqueue (HI-01)."""
    thinker = await create_thinker(session)
    source = await create_source(
        session,
        thinker_id=thinker.id,
        approval_status="approved",
        active=True,
        refresh_interval_hours=24,
        last_fetched=_now() - timedelta(hours=25),
    )
    # Pre-existing pending fetch job for the same source
    await create_job(
        session,
        job_type="fetch_podcast_feed",
        payload={"source_id": str(source.id)},
        status="pending",
    )
    trigger_job = await create_job(
        session,
        job_type="refresh_due_sources",
        payload={},
    )
    await session.commit()

    await handle_refresh_due_sources(session, trigger_job)

    result = await session.execute(
        select(Job).where(
            Job.job_type == "fetch_podcast_feed",
            Job.payload["source_id"].astext == str(source.id),
        )
    )
    fetch_jobs = result.scalars().all()
    # Only the original pre-existing job — no duplicate enqueued
    assert len(fetch_jobs) == 1


async def test_youtube_source_gets_youtube_job(session: AsyncSession):
    """Due source with source_type='youtube_channel' -> fetch_youtube_channel job (HI-01)."""
    thinker = await create_thinker(session)
    source = await create_source(
        session,
        thinker_id=thinker.id,
        source_type="youtube_channel",
        approval_status="approved",
        active=True,
        refresh_interval_hours=24,
        last_fetched=_now() - timedelta(hours=25),
    )
    trigger_job = await create_job(
        session,
        job_type="refresh_due_sources",
        payload={},
    )
    await session.commit()

    await handle_refresh_due_sources(session, trigger_job)

    result = await session.execute(
        select(Job).where(
            Job.job_type == "fetch_youtube_channel",
            Job.payload["source_id"].astext == str(source.id),
        )
    )
    yt_jobs = result.scalars().all()
    assert len(yt_jobs) == 1

    # And no podcast_feed job was created for this youtube source
    rss_result = await session.execute(
        select(Job).where(
            Job.job_type == "fetch_podcast_feed",
            Job.payload["source_id"].astext == str(source.id),
        )
    )
    assert len(rss_result.scalars().all()) == 0
