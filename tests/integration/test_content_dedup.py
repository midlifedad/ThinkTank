"""Integration tests for content deduplication layers.

Tests URL normalization dedup (Layer 1), fingerprint dedup (Layer 2),
and NULL fingerprint behavior.
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.factories import create_content, create_job, create_source, create_thinker
from thinktank.handlers.fetch_podcast_feed import handle_fetch_podcast_feed
from thinktank.ingestion.fingerprint import compute_fingerprint
from thinktank.ingestion.url_normalizer import normalize_url
from thinktank.models.content import Content

FIXTURES = Path(__file__).parent.parent / "fixtures" / "rss"

pytestmark = pytest.mark.anyio


def _make_httpx_mock(fixture_name: str):
    """Create an AsyncMock for httpx.AsyncClient context manager."""
    xml_content = (FIXTURES / fixture_name).read_text()
    mock_response = MagicMock()
    mock_response.text = xml_content
    mock_response.status_code = 200
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)

    mock_cm = AsyncMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_client)
    mock_cm.__aexit__ = AsyncMock(return_value=False)

    return mock_cm


@patch("thinktank.handlers.fetch_podcast_feed.httpx.AsyncClient")
async def test_url_normalization_dedup(mock_client_cls: MagicMock, session: AsyncSession):
    """Insert content with canonical_url. Feed with same URL + tracking params -> dedup catches it."""
    thinker = await create_thinker(session)
    source = await create_source(
        session,
        thinker_id=thinker.id,
        url="https://example.com/feed/dedup-url.xml",
        approval_status="approved",
        active=True,
        backfill_complete=False,
    )

    # Pre-insert content with the canonical URL of the climate episode from
    # podcast_duplicates.xml. The fixture has tracking params on the enclosure URL:
    # https://cdn.example.com/episodes/climate.mp3?utm_source=feed&utm_medium=rss&ref=homepage
    # which normalizes to: https://cdn.example.com/episodes/climate.mp3
    canonical = normalize_url(
        "https://cdn.example.com/episodes/climate.mp3?utm_source=feed&utm_medium=rss&ref=homepage"
    )
    await create_content(
        session,
        source_id=source.id,
        source_owner_id=thinker.id,
        url="https://cdn.example.com/episodes/climate.mp3",
        canonical_url=canonical,
        title="Climate Adaptation Strategies",
    )
    await session.commit()

    # Now poll the duplicates feed -- the climate episode should be deduped by URL
    mock_client_cls.return_value = _make_httpx_mock("podcast_duplicates.xml")

    job = await create_job(
        session,
        job_type="fetch_podcast_feed",
        payload={"source_id": str(source.id)},
    )
    await session.commit()

    await handle_fetch_podcast_feed(session, job)

    # Query all content for this source
    result = await session.execute(select(Content).where(Content.source_id == source.id))
    content_rows = result.scalars().all()

    # Should have: 1 pre-existing + 2 new (quantum-a and quantum-b have different URLs,
    # but quantum-b gets fingerprint-deduped against quantum-a)
    # Actually: quantum-a inserts, quantum-b has same fingerprint -> deduped, climate deduped by URL
    # So total = 1 pre-existing + 1 new (quantum-a) = 2
    assert len(content_rows) == 2

    titles = {c.title for c in content_rows}
    assert "Climate Adaptation Strategies" in titles
    assert "Understanding Quantum Computing" in titles


@patch("thinktank.handlers.fetch_podcast_feed.httpx.AsyncClient")
async def test_fingerprint_dedup(mock_client_cls: MagicMock, session: AsyncSession):
    """Pre-insert content with fingerprint. Feed with different URL but same title/date/duration -> dedup catches it."""
    from datetime import datetime

    thinker = await create_thinker(session)
    source = await create_source(
        session,
        thinker_id=thinker.id,
        url="https://example.com/feed/dedup-fp.xml",
        approval_status="approved",
        active=True,
        backfill_complete=False,
    )

    # Pre-insert content matching the first quantum episode from podcast_duplicates.xml
    # Title: "Understanding Quantum Computing", pubDate: 2026-03-04 09:00, duration: 3600
    fp = compute_fingerprint(
        "Understanding Quantum Computing",
        datetime(2026, 3, 4, 9, 0, 0),
        3600,
    )
    await create_content(
        session,
        source_id=source.id,
        source_owner_id=thinker.id,
        url="https://other-platform.com/quantum",
        canonical_url="https://other-platform.com/quantum",
        content_fingerprint=fp,
        title="Understanding Quantum Computing",
        published_at=datetime(2026, 3, 4, 9, 0, 0),
        duration_seconds=3600,
    )
    await session.commit()

    # Poll duplicates feed -- both quantum episodes should be caught by fingerprint
    mock_client_cls.return_value = _make_httpx_mock("podcast_duplicates.xml")

    job = await create_job(
        session,
        job_type="fetch_podcast_feed",
        payload={"source_id": str(source.id)},
    )
    await session.commit()

    await handle_fetch_podcast_feed(session, job)

    result = await session.execute(select(Content).where(Content.source_id == source.id))
    content_rows = result.scalars().all()

    # Pre-existing (quantum from other-platform) + climate episode = 2
    # Both quantum episodes deduped (first by fingerprint, second also by fingerprint)
    assert len(content_rows) == 2

    titles = {c.title for c in content_rows}
    assert "Understanding Quantum Computing" in titles
    assert "Climate Adaptation Strategies" in titles


async def test_null_fingerprint_not_deduped(session: AsyncSession):
    """Content with no title (NULL fingerprint) can have multiple rows with NULL fingerprint."""
    thinker = await create_thinker(session)
    source = await create_source(
        session,
        thinker_id=thinker.id,
        url="https://example.com/feed/null-fp.xml",
    )

    # Create two content rows with NULL fingerprint (empty titles produce NULL fingerprint)
    await create_content(
        session,
        source_id=source.id,
        source_owner_id=thinker.id,
        url="https://example.com/ep1",
        canonical_url="https://example.com/ep1",
        content_fingerprint=None,
        title="",
    )
    await create_content(
        session,
        source_id=source.id,
        source_owner_id=thinker.id,
        url="https://example.com/ep2",
        canonical_url="https://example.com/ep2",
        content_fingerprint=None,
        title="",
    )
    await session.commit()

    # Both rows should exist -- NULL fingerprints don't violate UNIQUE
    result = await session.execute(select(Content).where(Content.source_id == source.id))
    content_rows = result.scalars().all()
    assert len(content_rows) == 2

    # Both have NULL fingerprint
    for c in content_rows:
        assert c.content_fingerprint is None
