"""Integration tests for discover_guests_podcastindex handler.

Tests source registration from API results, deduplication by normalized URL,
handling of missing feed URLs, and rate limit retry behavior.

Uses real PostgreSQL database with mocked API clients.
"""

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.thinktank.handlers.discover_guests_podcastindex import (
    handle_discover_guests_podcastindex,
)
from src.thinktank.models.source import Source, SourceThinker
from tests.factories import create_job, create_source, create_thinker

pytestmark = pytest.mark.anyio


# ---- Podcast Index handler tests ----


def _mock_podcastindex_client(return_value):
    """Create a mock PodcastIndexClient that returns the given data."""
    mock_instance = AsyncMock()
    mock_instance.search_by_person = AsyncMock(return_value=return_value)
    mock_cls = lambda api_key, api_secret: mock_instance  # noqa: E731
    return patch(
        "src.thinktank.handlers.discover_guests_podcastindex.PodcastIndexClient",
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
    result = await session.execute(
        select(Source).where(Source.approval_status == "pending_llm")
    )
    sources = result.scalars().all()
    assert len(sources) == 1
    assert sources[0].name == "Science Talk"
    assert sources[0].approval_status == "pending_llm"

    # Verify junction row links source to thinker
    junc_result = await session.execute(
        select(SourceThinker).where(SourceThinker.source_id == sources[0].id)
    )
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
        select(func.count())
        .select_from(Source)
        .where(Source.approval_status == "pending_llm")
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
        select(func.count())
        .select_from(Source)
        .where(Source.approval_status == "pending_llm")
    )
    assert count == 0
