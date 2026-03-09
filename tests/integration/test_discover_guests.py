"""Integration tests for discover_guests_listennotes and discover_guests_podcastindex handlers.

Tests source registration from API results, deduplication by normalized URL,
handling of missing RSS/feed URLs, and rate limit retry behavior.

Uses real PostgreSQL database with mocked API clients.
"""

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.thinktank.handlers.discover_guests_listennotes import (
    handle_discover_guests_listennotes,
)
from src.thinktank.handlers.discover_guests_podcastindex import (
    handle_discover_guests_podcastindex,
)
from src.thinktank.models.source import Source
from tests.factories import create_job, create_source, create_thinker

pytestmark = pytest.mark.anyio


# ---- Listen Notes handler tests ----


def _mock_listennotes_client(return_value):
    """Create a mock ListenNotesClient that returns the given data."""
    mock_instance = AsyncMock()
    mock_instance.search_episodes_by_person = AsyncMock(return_value=return_value)
    mock_cls = lambda api_key: mock_instance  # noqa: E731
    return patch(
        "src.thinktank.handlers.discover_guests_listennotes.ListenNotesClient",
        mock_cls,
    )


async def test_listennotes_registers_source(session: AsyncSession):
    """Listen Notes handler creates Source rows from API results with RSS URL."""
    thinker = await create_thinker(session, name="John Smith")
    job = await create_job(
        session,
        job_type="discover_guests_listennotes",
        payload={"thinker_id": str(thinker.id)},
    )
    await session.commit()

    api_data = {
        "results": [
            {
                "podcast": {
                    "title_original": "Great Podcast",
                    "rss": "https://feeds.example.com/great-podcast.xml",
                },
            },
        ],
    }

    with (
        _mock_listennotes_client(api_data),
        patch.dict("os.environ", {"LISTENNOTES_API_KEY": "test-key"}),
    ):
        await handle_discover_guests_listennotes(session, job)

    result = await session.execute(
        select(Source).where(Source.thinker_id == thinker.id)
    )
    sources = result.scalars().all()
    # Filter to only new sources (not the thinker's existing ones)
    new_sources = [s for s in sources if s.approval_status == "pending_llm"]
    assert len(new_sources) == 1
    assert new_sources[0].name == "Great Podcast"
    assert new_sources[0].approval_status == "pending_llm"


async def test_listennotes_skips_no_rss(session: AsyncSession):
    """Listen Notes handler skips results without podcast.rss URL."""
    thinker = await create_thinker(session, name="John Smith")
    job = await create_job(
        session,
        job_type="discover_guests_listennotes",
        payload={"thinker_id": str(thinker.id)},
    )
    await session.commit()

    api_data = {
        "results": [
            {
                "podcast": {
                    "title_original": "No RSS Podcast",
                    # No "rss" key -- free tier
                },
            },
        ],
    }

    with (
        _mock_listennotes_client(api_data),
        patch.dict("os.environ", {"LISTENNOTES_API_KEY": "test-key"}),
    ):
        await handle_discover_guests_listennotes(session, job)

    count = await session.scalar(
        select(func.count())
        .select_from(Source)
        .where(Source.approval_status == "pending_llm")
    )
    assert count == 0


async def test_listennotes_skips_existing_source(session: AsyncSession):
    """Listen Notes handler skips sources that already exist (dedup by URL)."""
    thinker = await create_thinker(session, name="John Smith")
    existing_url = "https://feeds.example.com/existing.xml"
    await create_source(
        session,
        thinker_id=thinker.id,
        url=existing_url,
    )
    job = await create_job(
        session,
        job_type="discover_guests_listennotes",
        payload={"thinker_id": str(thinker.id)},
    )
    await session.commit()

    api_data = {
        "results": [
            {
                "podcast": {
                    "title_original": "Existing Podcast",
                    "rss": existing_url,
                },
            },
        ],
    }

    with (
        _mock_listennotes_client(api_data),
        patch.dict("os.environ", {"LISTENNOTES_API_KEY": "test-key"}),
    ):
        await handle_discover_guests_listennotes(session, job)

    # Should not have created any new pending_llm sources
    count = await session.scalar(
        select(func.count())
        .select_from(Source)
        .where(Source.approval_status == "pending_llm")
    )
    assert count == 0


async def test_listennotes_rate_limited(session: AsyncSession):
    """Listen Notes handler raises ValueError when rate-limited (client returns None)."""
    thinker = await create_thinker(session, name="John Smith")
    job = await create_job(
        session,
        job_type="discover_guests_listennotes",
        payload={"thinker_id": str(thinker.id)},
    )
    await session.commit()

    with (
        _mock_listennotes_client(None),
        patch.dict("os.environ", {"LISTENNOTES_API_KEY": "test-key"}),
        pytest.raises(ValueError, match="Rate limited"),
    ):
        await handle_discover_guests_listennotes(session, job)


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

    result = await session.execute(
        select(Source).where(
            Source.thinker_id == thinker.id,
            Source.approval_status == "pending_llm",
        )
    )
    sources = result.scalars().all()
    assert len(sources) == 1
    assert sources[0].name == "Science Talk"
    assert sources[0].approval_status == "pending_llm"


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
