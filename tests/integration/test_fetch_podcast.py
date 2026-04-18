"""Integration tests for fetch_podcast_feed handler.

Tests the full RSS polling pipeline: fetch, parse, dedup, filter,
content insertion, source updates, and tag job creation.

Uses mocked httpx to return RSS fixture XML.
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.factories import create_job, create_source, create_thinker
from thinktank.handlers.fetch_podcast_feed import handle_fetch_podcast_feed
from thinktank.models.content import Content
from thinktank.models.job import Job

FIXTURES = Path(__file__).parent.parent / "fixtures" / "rss"

pytestmark = pytest.mark.anyio


def _mock_httpx_response(fixture_name: str) -> MagicMock:
    """Create a mock httpx response returning fixture XML content."""
    xml_content = (FIXTURES / fixture_name).read_text()
    mock_response = MagicMock()
    mock_response.text = xml_content
    mock_response.status_code = 200
    mock_response.raise_for_status = MagicMock()
    return mock_response


def _make_httpx_mock(fixture_name: str):
    """Create an AsyncMock for httpx.AsyncClient context manager."""
    mock_response = _mock_httpx_response(fixture_name)
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)

    mock_cm = AsyncMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_client)
    mock_cm.__aexit__ = AsyncMock(return_value=False)

    return mock_cm


@patch("thinktank.handlers.fetch_podcast_feed.httpx.AsyncClient")
async def test_basic_feed_poll(mock_client_cls: MagicMock, session: AsyncSession):
    """Approved source + basic feed -> 3 content rows with correct metadata."""
    mock_client_cls.return_value = _make_httpx_mock("podcast_basic.xml")

    await create_thinker(session, name="Test Thinker")
    source = await create_source(
        session,
        url="https://example.com/feed/basic.xml",
        approval_status="approved",
        active=True,
        backfill_complete=False,
    )
    job = await create_job(session, job_type="fetch_podcast_feed", payload={"source_id": str(source.id)})
    await session.commit()

    await handle_fetch_podcast_feed(session, job)

    # Verify content rows
    result = await session.execute(select(Content).where(Content.source_id == source.id))
    content_rows = result.scalars().all()
    assert len(content_rows) == 3

    # Verify metadata on first episode
    titles = {c.title for c in content_rows}
    assert "Deep Dive into AI Safety" in titles
    assert "The Future of Energy" in titles
    assert "Crypto Markets Q4 Review" in titles

    for c in content_rows:
        assert c.content_type == "episode"
        assert c.show_name == "ThinkTank Test Podcast"


@patch("thinktank.handlers.fetch_podcast_feed.httpx.AsyncClient")
async def test_duplicate_poll_no_new_rows(mock_client_cls: MagicMock, session: AsyncSession):
    """Polling same feed twice -> second poll inserts 0 new rows (URL dedup)."""
    mock_client_cls.return_value = _make_httpx_mock("podcast_basic.xml")

    await create_thinker(session)
    source = await create_source(
        session,
        url="https://example.com/feed/basic-dup.xml",
        approval_status="approved",
        active=True,
        backfill_complete=False,
    )
    job1 = await create_job(session, job_type="fetch_podcast_feed", payload={"source_id": str(source.id)})
    await session.commit()

    # First poll
    await handle_fetch_podcast_feed(session, job1)

    result = await session.execute(select(Content).where(Content.source_id == source.id))
    first_count = len(result.scalars().all())
    assert first_count == 3

    # Reset mock for second poll
    mock_client_cls.return_value = _make_httpx_mock("podcast_basic.xml")

    # Reload source since it was committed
    await session.refresh(source)

    job2 = await create_job(session, job_type="fetch_podcast_feed", payload={"source_id": str(source.id)})
    await session.commit()

    # Second poll
    await handle_fetch_podcast_feed(session, job2)

    result = await session.execute(select(Content).where(Content.source_id == source.id))
    second_count = len(result.scalars().all())
    assert second_count == 3  # No new rows


@patch("thinktank.handlers.fetch_podcast_feed.httpx.AsyncClient")
async def test_unapproved_source_skipped(mock_client_cls: MagicMock, session: AsyncSession):
    """Source with approval_status='pending_llm' -> no content, no error."""
    mock_client_cls.return_value = _make_httpx_mock("podcast_basic.xml")

    await create_thinker(session)
    source = await create_source(
        session, url="https://example.com/feed/unapproved.xml", approval_status="pending_llm", active=True
    )
    job = await create_job(session, job_type="fetch_podcast_feed", payload={"source_id": str(source.id)})
    await session.commit()

    # Should not raise, just return
    await handle_fetch_podcast_feed(session, job)

    result = await session.execute(select(Content).where(Content.source_id == source.id))
    assert len(result.scalars().all()) == 0

    # httpx should NOT have been called
    mock_client_cls.return_value.__aenter__.assert_not_awaited()


@patch("thinktank.handlers.fetch_podcast_feed.httpx.AsyncClient")
async def test_inactive_source_skipped(mock_client_cls: MagicMock, session: AsyncSession):
    """Source with active=False -> no content inserted."""
    mock_client_cls.return_value = _make_httpx_mock("podcast_basic.xml")

    await create_thinker(session)
    source = await create_source(
        session, url="https://example.com/feed/inactive.xml", approval_status="approved", active=False
    )
    job = await create_job(session, job_type="fetch_podcast_feed", payload={"source_id": str(source.id)})
    await session.commit()

    await handle_fetch_podcast_feed(session, job)

    result = await session.execute(select(Content).where(Content.source_id == source.id))
    assert len(result.scalars().all()) == 0


@patch("thinktank.handlers.fetch_podcast_feed.httpx.AsyncClient")
async def test_short_episodes_skipped(mock_client_cls: MagicMock, session: AsyncSession):
    """Short episodes get status='skipped', long episode gets 'pending'."""
    mock_client_cls.return_value = _make_httpx_mock("podcast_short_episodes.xml")

    await create_thinker(session)
    source = await create_source(
        session,
        url="https://example.com/feed/short.xml",
        approval_status="approved",
        active=True,
        backfill_complete=False,
    )
    job = await create_job(session, job_type="fetch_podcast_feed", payload={"source_id": str(source.id)})
    await session.commit()

    await handle_fetch_podcast_feed(session, job)

    result = await session.execute(select(Content).where(Content.source_id == source.id))
    content_rows = result.scalars().all()
    assert len(content_rows) == 3

    by_title = {c.title: c for c in content_rows}

    # 120s and 300s episodes should be skipped (min_duration default = 600)
    assert by_title["Quick Update"].status == "skipped"
    assert by_title["Short Bonus"].status == "skipped"

    # 3600s episode should be cataloged (Phase 13: catalog-then-promote)
    assert by_title["Full Episode"].status == "cataloged"


@patch("thinktank.handlers.fetch_podcast_feed.httpx.AsyncClient")
async def test_skip_title_patterns(mock_client_cls: MagicMock, session: AsyncSession):
    """Trailer, Best of, and Announcement episodes get status='skipped'."""
    mock_client_cls.return_value = _make_httpx_mock("podcast_skip_titles.xml")

    await create_thinker(session)
    source = await create_source(
        session,
        url="https://example.com/feed/skip-titles.xml",
        approval_status="approved",
        active=True,
        backfill_complete=False,
    )
    job = await create_job(session, job_type="fetch_podcast_feed", payload={"source_id": str(source.id)})
    await session.commit()

    await handle_fetch_podcast_feed(session, job)

    result = await session.execute(select(Content).where(Content.source_id == source.id))
    content_rows = result.scalars().all()
    assert len(content_rows) == 4

    by_title = {c.title: c for c in content_rows}

    assert by_title["Season 3 Trailer"].status == "skipped"
    assert by_title["Best of 2025"].status == "skipped"
    assert by_title["Announcement: New Season"].status == "skipped"
    assert by_title["Full Interview with Expert"].status == "cataloged"


@patch("thinktank.handlers.fetch_podcast_feed.httpx.AsyncClient")
async def test_source_last_fetched_updated(mock_client_cls: MagicMock, session: AsyncSession):
    """After successful poll, source.last_fetched is set and item_count matches."""
    mock_client_cls.return_value = _make_httpx_mock("podcast_basic.xml")

    await create_thinker(session)
    source = await create_source(
        session,
        url="https://example.com/feed/fetched.xml",
        approval_status="approved",
        active=True,
        backfill_complete=False,
        item_count=0,
    )
    assert source.last_fetched is None

    job = await create_job(session, job_type="fetch_podcast_feed", payload={"source_id": str(source.id)})
    await session.commit()

    await handle_fetch_podcast_feed(session, job)

    # Reload source
    await session.refresh(source)
    assert source.last_fetched is not None
    assert source.item_count == 3


@patch("thinktank.handlers.fetch_podcast_feed.httpx.AsyncClient")
async def test_backfill_then_incremental(mock_client_cls: MagicMock, session: AsyncSession):
    """First poll: backfill_complete set True. Second poll: incremental, no new rows."""
    mock_client_cls.return_value = _make_httpx_mock("podcast_basic.xml")

    await create_thinker(session)
    source = await create_source(
        session,
        url="https://example.com/feed/backfill.xml",
        approval_status="approved",
        active=True,
        backfill_complete=False,
    )
    job1 = await create_job(session, job_type="fetch_podcast_feed", payload={"source_id": str(source.id)})
    await session.commit()

    # First poll (backfill)
    await handle_fetch_podcast_feed(session, job1)

    await session.refresh(source)
    assert source.backfill_complete is True

    # Second poll (incremental) with same feed
    mock_client_cls.return_value = _make_httpx_mock("podcast_basic.xml")

    job2 = await create_job(session, job_type="fetch_podcast_feed", payload={"source_id": str(source.id)})
    await session.commit()

    await handle_fetch_podcast_feed(session, job2)

    # Should have same 3 rows (no new content in incremental mode since
    # all entries have published_at <= last_fetched)
    result = await session.execute(select(Content).where(Content.source_id == source.id))
    assert len(result.scalars().all()) == 3


@patch("thinktank.handlers.fetch_podcast_feed.httpx.AsyncClient")
async def test_tag_job_enqueued_with_descriptions(mock_client_cls: MagicMock, session: AsyncSession):
    """After successful poll, tag_content_thinkers job has content_ids, source_id, and descriptions."""
    mock_client_cls.return_value = _make_httpx_mock("podcast_basic.xml")

    await create_thinker(session)
    source = await create_source(
        session,
        url="https://example.com/feed/tag.xml",
        approval_status="approved",
        active=True,
        backfill_complete=False,
    )
    job = await create_job(session, job_type="fetch_podcast_feed", payload={"source_id": str(source.id)})
    await session.commit()

    await handle_fetch_podcast_feed(session, job)

    # Phase 13: fetch_podcast_feed now chains to scan_episodes_for_thinkers
    result = await session.execute(select(Job).where(Job.job_type == "scan_episodes_for_thinkers"))
    scan_jobs = result.scalars().all()
    assert len(scan_jobs) == 1

    scan_job = scan_jobs[0]
    payload = scan_job.payload

    assert "content_ids" in payload
    assert "source_id" in payload
    assert "descriptions" in payload

    assert payload["source_id"] == str(source.id)
    assert len(payload["content_ids"]) == 3

    # Descriptions dict maps content_id to description string
    desc_dict = payload["descriptions"]
    assert isinstance(desc_dict, dict)
    assert len(desc_dict) == 3

    # Each content_id in descriptions should also be in content_ids
    for cid in payload["content_ids"]:
        assert cid in desc_dict

    # Check description content
    descriptions = list(desc_dict.values())
    desc_text = " ".join(descriptions)
    assert "alignment research" in desc_text.lower() or "ai" in desc_text.lower()


@patch("thinktank.handlers.fetch_podcast_feed.httpx.AsyncClient")
async def test_per_source_duration_override(mock_client_cls: MagicMock, session: AsyncSession):
    """Source with min_duration_override=300, feed has 400s episode -> NOT skipped."""
    mock_client_cls.return_value = _make_httpx_mock("podcast_short_episodes.xml")

    await create_thinker(session)
    source = await create_source(
        session,
        url="https://example.com/feed/override.xml",
        approval_status="approved",
        active=True,
        backfill_complete=False,
        config={"min_duration_override": 200},
    )
    job = await create_job(session, job_type="fetch_podcast_feed", payload={"source_id": str(source.id)})
    await session.commit()

    await handle_fetch_podcast_feed(session, job)

    result = await session.execute(select(Content).where(Content.source_id == source.id))
    content_rows = result.scalars().all()
    by_title = {c.title: c for c in content_rows}

    # With override of 200s, the 300s episode is NOT skipped (cataloged instead)
    assert by_title["Short Bonus"].status == "cataloged"
    # 120s is still below override
    assert by_title["Quick Update"].status == "skipped"
    # 3600s cataloged (Phase 13: catalog-then-promote)
    assert by_title["Full Episode"].status == "cataloged"
