"""Contract tests for the enqueue_pending_transcriptions sweep handler.

Source: ARCH-REVIEW 2026-05-28 (A1). Content promoted to status='pending'
must eventually get a process_content job. The sweep covers backlog and
desyncs, bounded by the max_pending_transcriptions threshold.

Contract:
    - Given pending content with no process_content job coverage
    - When the sweep runs
    - Then process_content jobs are created (oldest first), never pushing
      the queue past max_pending_transcriptions
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.factories import create_content, create_job, create_source, create_system_config
from thinktank.handlers.enqueue_pending_transcriptions import handle_enqueue_pending_transcriptions
from thinktank.models.job import Job

pytestmark = pytest.mark.anyio


async def _sweep(session: AsyncSession) -> None:
    """Run the sweep handler with a fresh trigger job."""
    job = await create_job(session, job_type="enqueue_pending_transcriptions", payload={})
    await handle_enqueue_pending_transcriptions(session, job)


async def _process_content_jobs(session: AsyncSession) -> list[Job]:
    result = await session.execute(select(Job).where(Job.job_type == "process_content"))
    return list(result.scalars().all())


class TestSweepEnqueuesBacklog:
    """Uncovered pending content gets a process_content job."""

    async def test_pending_content_without_jobs_is_enqueued(self, session: AsyncSession):
        """Three uncovered pending episodes -> three process_content jobs."""
        source = await create_source(session)
        contents = [
            await create_content(session, source_id=source.id, status="pending", title=f"Episode {i}") for i in range(3)
        ]

        await _sweep(session)

        jobs = await _process_content_jobs(session)
        assert len(jobs) == 3
        enqueued_ids = {j.payload["content_id"] for j in jobs}
        assert enqueued_ids == {str(c.id) for c in contents}
        # max_attempts must come from MAX_ATTEMPTS_BY_TYPE (process_content=2),
        # not the generic default of 3.
        assert all(j.max_attempts == 2 for j in jobs)
        assert all(j.status == "pending" for j in jobs)

    async def test_non_pending_content_is_ignored(self, session: AsyncSession):
        """cataloged/done/skipped content never gets a transcription job."""
        source = await create_source(session)
        for status in ("cataloged", "done", "skipped"):
            await create_content(session, source_id=source.id, status=status, title=f"{status} ep")

        await _sweep(session)

        assert await _process_content_jobs(session) == []


class TestSweepSkipsCoveredContent:
    """Content already referenced by a non-done process_content job is skipped."""

    async def test_inflight_job_not_duplicated(self, session: AsyncSession):
        """Pending content with an in-flight job is not re-enqueued."""
        source = await create_source(session)
        content = await create_content(session, source_id=source.id, status="pending")
        await create_job(
            session,
            job_type="process_content",
            payload={"content_id": str(content.id)},
            status="pending",
        )

        await _sweep(session)

        jobs = await _process_content_jobs(session)
        assert len(jobs) == 1  # only the pre-existing job

    async def test_failed_job_not_retried_automatically(self, session: AsyncSession):
        """Permanently failed transcription is NOT auto-re-enqueued (operator
        retries from the admin queue instead of looping the failure)."""
        source = await create_source(session)
        content = await create_content(session, source_id=source.id, status="pending")
        await create_job(
            session,
            job_type="process_content",
            payload={"content_id": str(content.id)},
            status="failed",
        )

        await _sweep(session)

        jobs = await _process_content_jobs(session)
        assert len(jobs) == 1

    async def test_done_job_with_still_pending_content_is_reenqueued(self, session: AsyncSession):
        """Desync healing: a done job whose content is still 'pending' means
        the content update was lost -- the sweep re-enqueues it."""
        source = await create_source(session)
        content = await create_content(session, source_id=source.id, status="pending")
        await create_job(
            session,
            job_type="process_content",
            payload={"content_id": str(content.id)},
            status="done",
        )

        await _sweep(session)

        jobs = await _process_content_jobs(session)
        new_jobs = [j for j in jobs if j.status == "pending"]
        assert len(new_jobs) == 1
        assert new_jobs[0].payload["content_id"] == str(content.id)


class TestSweepRespectsThreshold:
    """The sweep never pushes queue depth past max_pending_transcriptions."""

    async def test_budget_caps_enqueued_count_oldest_first(self, session: AsyncSession):
        """Threshold 2, three uncovered episodes -> only the two oldest enqueued."""
        await create_system_config(session, key="max_pending_transcriptions", value=2)
        source = await create_source(session)
        now = datetime.now(UTC)
        oldest = await create_content(
            session, source_id=source.id, status="pending", discovered_at=now - timedelta(days=2)
        )
        middle = await create_content(
            session, source_id=source.id, status="pending", discovered_at=now - timedelta(days=1)
        )
        await create_content(session, source_id=source.id, status="pending", discovered_at=now)

        await _sweep(session)

        jobs = await _process_content_jobs(session)
        assert len(jobs) == 2
        assert {j.payload["content_id"] for j in jobs} == {str(oldest.id), str(middle.id)}

    async def test_at_capacity_enqueues_nothing(self, session: AsyncSession):
        """Queue already at threshold -> sweep is a no-op."""
        await create_system_config(session, key="max_pending_transcriptions", value=1)
        source = await create_source(session)
        covered = await create_content(session, source_id=source.id, status="pending")
        await create_job(
            session,
            job_type="process_content",
            payload={"content_id": str(covered.id)},
            status="pending",
        )
        await create_content(session, source_id=source.id, status="pending")

        await _sweep(session)

        jobs = await _process_content_jobs(session)
        assert len(jobs) == 1  # only the pre-existing in-flight job
