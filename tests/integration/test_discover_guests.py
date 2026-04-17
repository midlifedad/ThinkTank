"""Integration tests for discover_guests_podcastindex handler.

Tests source registration from API results, deduplication by normalized URL,
handling of missing feed URLs, and rate limit retry behavior.

Uses real PostgreSQL database with mocked API clients.
"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.factories import create_job, create_source, create_thinker
from thinktank.handlers.discover_guests_podcastindex import (
    handle_discover_guests_podcastindex,
)
from thinktank.models.source import Source, SourceThinker

pytestmark = pytest.mark.anyio


# ---- Podcast Index handler tests ----


def _mock_podcastindex_client(return_value):
    """Create a mock PodcastIndexClient that returns the given data."""
    mock_instance = AsyncMock()
    mock_instance.search_by_person = AsyncMock(return_value=return_value)
    mock_cls = lambda api_key, api_secret: mock_instance  # noqa: E731
    return patch(
        "thinktank.handlers.discover_guests_podcastindex.PodcastIndexClient",
        mock_cls,
    )


async def test_podcastindex_registers_source(session: AsyncSession):
    """Podcast Index handler creates Source rows from API results."""
    thinker = await create_thinker(session, name="Jane Doe")
    job = await create_job(
        session,
        job_type="discover_guests_podcastindex",
        payload={"thinker_id": str(thinker.id)},
    )
    await session.commit()

    api_data = {
        "items": [
            {
                "feedTitle": "Science Talk",
                "feedUrl": "https://feeds.example.com/science-talk.xml",
            },
        ],
    }

    with (
        _mock_podcastindex_client(api_data),
        patch.dict(
            "os.environ",
            {"PODCASTINDEX_API_KEY": "test-key", "PODCASTINDEX_API_SECRET": "test-secret"},
        ),
    ):
        await handle_discover_guests_podcastindex(session, job)

    # Source should exist with pending_llm status (thinker_id no longer set on source)
    result = await session.execute(select(Source).where(Source.approval_status == "pending_llm"))
    sources = result.scalars().all()
    assert len(sources) == 1
    assert sources[0].name == "Science Talk"
    assert sources[0].approval_status == "pending_llm"

    # Verify junction row links source to thinker
    junc_result = await session.execute(select(SourceThinker).where(SourceThinker.source_id == sources[0].id))
    junc = junc_result.scalar_one()
    assert junc.thinker_id == thinker.id


async def test_podcastindex_skips_existing(session: AsyncSession):
    """Podcast Index handler skips sources that already exist."""
    thinker = await create_thinker(session, name="Jane Doe")
    existing_url = "https://feeds.example.com/existing-pi.xml"
    await create_source(
        session,
        thinker_id=thinker.id,
        url=existing_url,
    )
    job = await create_job(
        session,
        job_type="discover_guests_podcastindex",
        payload={"thinker_id": str(thinker.id)},
    )
    await session.commit()

    api_data = {
        "items": [
            {
                "feedTitle": "Existing PI Podcast",
                "feedUrl": existing_url,
            },
        ],
    }

    with (
        _mock_podcastindex_client(api_data),
        patch.dict(
            "os.environ",
            {"PODCASTINDEX_API_KEY": "test-key", "PODCASTINDEX_API_SECRET": "test-secret"},
        ),
    ):
        await handle_discover_guests_podcastindex(session, job)

    count = await session.scalar(
        select(func.count()).select_from(Source).where(Source.approval_status == "pending_llm")
    )
    assert count == 0


async def test_podcastindex_rate_limited(session: AsyncSession):
    """Podcast Index handler raises ValueError when rate-limited."""
    thinker = await create_thinker(session, name="Jane Doe")
    job = await create_job(
        session,
        job_type="discover_guests_podcastindex",
        payload={"thinker_id": str(thinker.id)},
    )
    await session.commit()

    with (
        _mock_podcastindex_client(None),
        patch.dict(
            "os.environ",
            {"PODCASTINDEX_API_KEY": "test-key", "PODCASTINDEX_API_SECRET": "test-secret"},
        ),
        pytest.raises(ValueError, match="Rate limited"),
    ):
        await handle_discover_guests_podcastindex(session, job)


async def test_podcastindex_skips_no_feedurl(session: AsyncSession):
    """Podcast Index handler skips items without feedUrl."""
    thinker = await create_thinker(session, name="Jane Doe")
    job = await create_job(
        session,
        job_type="discover_guests_podcastindex",
        payload={"thinker_id": str(thinker.id)},
    )
    await session.commit()

    api_data = {
        "items": [
            {
                "feedTitle": "No Feed URL",
                # No "feedUrl" key
            },
        ],
    }

    with (
        _mock_podcastindex_client(api_data),
        patch.dict(
            "os.environ",
            {"PODCASTINDEX_API_KEY": "test-key", "PODCASTINDEX_API_SECRET": "test-secret"},
        ),
    ):
        await handle_discover_guests_podcastindex(session, job)

    count = await session.scalar(
        select(func.count()).select_from(Source).where(Source.approval_status == "pending_llm")
    )
    assert count == 0


class TestConcurrentDiscoveryRace:
    """Regression: concurrent discovers of the same feedUrl must not crash.

    Source: INTEGRATIONS-REVIEW H-03. Two thinkers whose guest discovery
    runs in parallel and both find the same podcast feed would previously
    hit a unique-violation on sources.url.
    """

    async def test_concurrent_discover_same_feed_upserts(self, session_factory):
        """Two workers discover the same feedUrl — exactly one inserts, no crash."""
        async with session_factory() as setup:
            t1 = await create_thinker(setup, name="Thinker One")
            t2 = await create_thinker(setup, name="Thinker Two")
            job1 = await create_job(
                setup,
                job_type="discover_guests_podcastindex",
                payload={"thinker_id": str(t1.id)},
            )
            job2 = await create_job(
                setup,
                job_type="discover_guests_podcastindex",
                payload={"thinker_id": str(t2.id)},
            )
            await setup.commit()
            job1_id, job2_id = job1.id, job2.id

        shared_feed = {
            "items": [
                {
                    "feedTitle": "Shared Podcast",
                    "feedUrl": "https://feeds.example.com/shared.xml",
                }
            ]
        }

        async def run_handler(job_id) -> None:
            from thinktank.models.job import Job as JobModel

            async with session_factory() as s:
                job = await s.get(JobModel, job_id)
                await handle_discover_guests_podcastindex(s, job)

        # Patch env + client once outside the gather — patch contexts aren't
        # coroutine-safe when two contexts enter/exit interleaved under
        # asyncio.gather (one's exit tears down what the other still needs).
        with (
            _mock_podcastindex_client(shared_feed),
            patch.dict(
                "os.environ",
                {
                    "PODCASTINDEX_API_KEY": "test-key",
                    "PODCASTINDEX_API_SECRET": "test-secret",
                },
            ),
        ):
            # Must not raise IntegrityError from concurrent insert of same URL.
            await asyncio.gather(run_handler(job1_id), run_handler(job2_id))

        async with session_factory() as check:
            src_count = await check.scalar(
                select(func.count()).select_from(Source).where(Source.url == "https://feeds.example.com/shared.xml")
            )
            assert src_count == 1, f"Expected 1 source, got {src_count}"

            junc_count = await check.scalar(select(func.count()).select_from(SourceThinker))
            assert junc_count == 2, f"Expected 2 junction rows (one per thinker), got {junc_count}"
