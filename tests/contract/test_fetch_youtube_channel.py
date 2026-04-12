"""Contract tests for fetch_youtube_channel handler.

Tests verify the handler's external contract against a real PostgreSQL database:
    - Creates Content rows with status='cataloged' from YouTube videos
    - Applies duration filtering (videos < min_duration get status='skipped')
    - Applies YouTube category filtering (music/gaming/sports get status='skipped')
    - Applies title pattern filtering
    - Enqueues scan_episodes_for_thinkers job with content_ids
    - Uses URL dedup to avoid re-inserting existing videos
    - Updates source.last_fetched and source.item_count
"""

from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.thinktank.handlers.fetch_youtube_channel import (
    handle_fetch_youtube_channel,
)
from src.thinktank.models.content import Content
from src.thinktank.models.job import Job
from tests.factories import (
    create_content,
    create_job,
    create_source,
    create_system_config,
)

pytestmark = pytest.mark.anyio


def _make_youtube_videos(videos: list[dict]) -> list[dict]:
    """Create video dicts in the format returned by YouTubeClient.fetch_all_channel_videos."""
    results = []
    for v in videos:
        results.append(
            {
                "video_id": v.get("video_id", "vid_default"),
                "title": v.get("title", "Test Video"),
                "description": v.get("description", "Test description"),
                "published_at": v.get("published_at", "2024-01-15T10:00:00Z"),
                "duration_iso": v.get("duration_iso", "PT1H0M0S"),
                "duration_seconds": v.get("duration_seconds", 3600),
                "category_id": v.get("category_id", "27"),
                "thumbnail_url": v.get("thumbnail_url", "https://i.ytimg.com/vi/test/hq.jpg"),
            }
        )
    return results


class TestFetchYouTubeChannelContract:
    """Contract: fetch_youtube_channel handler."""

    async def _setup_source_and_job(
        self,
        session: AsyncSession,
        *,
        source_type: str = "youtube_channel",
        external_id: str = "UCtest123channel",
        config: dict | None = None,
    ) -> tuple:
        """Helper to create source, api key config, and job."""
        source = await create_source(
            session,
            source_type=source_type,
            name="Test YouTube Channel",
            url=f"https://youtube.com/channel/{external_id}",
            external_id=external_id,
            approval_status="approved",
            active=True,
            backfill_complete=False,
            config=config or {},
        )
        # Add YouTube API key to system_config
        await create_system_config(
            session,
            key="youtube_api_key",
            value="test-youtube-api-key-123",
        )
        job = await create_job(
            session,
            job_type="fetch_youtube_channel",
            payload={"source_id": str(source.id)},
        )
        await session.commit()
        return source, job

    @patch("src.thinktank.handlers.fetch_youtube_channel.YouTubeClient")
    async def test_fetch_creates_cataloged_content(
        self, mock_client_cls: MagicMock, session: AsyncSession
    ):
        """YouTube videos -> Content rows with status='cataloged'."""
        source, job = await self._setup_source_and_job(session)

        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_client.fetch_all_channel_videos.return_value = _make_youtube_videos(
            [
                {"video_id": "vid001", "title": "Deep Interview", "duration_seconds": 5400, "category_id": "27"},
                {"video_id": "vid002", "title": "Long Discussion", "duration_seconds": 3600, "category_id": "24"},
                {"video_id": "vid003", "title": "Panel Talk", "duration_seconds": 2700, "category_id": "22"},
            ]
        )
        mock_client.quota_used = 3

        await handle_fetch_youtube_channel(session, job)

        result = await session.execute(
            select(Content).where(Content.source_id == source.id)
        )
        content_rows = result.scalars().all()
        assert len(content_rows) == 3

        for row in content_rows:
            assert row.status == "cataloged"
            assert row.content_type == "video"
            assert row.source_id == source.id

    @patch("src.thinktank.handlers.fetch_youtube_channel.YouTubeClient")
    async def test_fetch_applies_duration_filter(
        self, mock_client_cls: MagicMock, session: AsyncSession
    ):
        """Videos shorter than min_duration get status='skipped'."""
        source, job = await self._setup_source_and_job(session)

        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_client.fetch_all_channel_videos.return_value = _make_youtube_videos(
            [
                {"video_id": "vid_long", "title": "Long Interview", "duration_seconds": 3600, "category_id": "27"},
                {"video_id": "vid_short", "title": "Short Clip", "duration_seconds": 300, "category_id": "27"},
            ]
        )
        mock_client.quota_used = 2

        await handle_fetch_youtube_channel(session, job)

        result = await session.execute(
            select(Content).where(Content.source_id == source.id)
        )
        content_rows = result.scalars().all()
        by_url = {c.canonical_url: c for c in content_rows}

        long_url = "https://youtube.com/watch?v=vid_long"
        short_url = "https://youtube.com/watch?v=vid_short"

        assert by_url[long_url].status == "cataloged"
        assert by_url[short_url].status == "skipped"

    @patch("src.thinktank.handlers.fetch_youtube_channel.YouTubeClient")
    async def test_fetch_applies_category_filter(
        self, mock_client_cls: MagicMock, session: AsyncSession
    ):
        """Music (10) category videos get status='skipped'."""
        source, job = await self._setup_source_and_job(session)

        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_client.fetch_all_channel_videos.return_value = _make_youtube_videos(
            [
                {"video_id": "vid_edu", "title": "Education Talk", "duration_seconds": 3600, "category_id": "27"},
                {"video_id": "vid_music", "title": "Music Video", "duration_seconds": 2700, "category_id": "10"},
            ]
        )
        mock_client.quota_used = 2

        await handle_fetch_youtube_channel(session, job)

        result = await session.execute(
            select(Content).where(Content.source_id == source.id)
        )
        content_rows = result.scalars().all()
        by_url = {c.canonical_url: c for c in content_rows}

        assert by_url["https://youtube.com/watch?v=vid_edu"].status == "cataloged"
        assert by_url["https://youtube.com/watch?v=vid_music"].status == "skipped"

    @patch("src.thinktank.handlers.fetch_youtube_channel.YouTubeClient")
    async def test_fetch_applies_title_filter(
        self, mock_client_cls: MagicMock, session: AsyncSession
    ):
        """Title matching skip patterns -> status='skipped'."""
        source, job = await self._setup_source_and_job(session)

        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_client.fetch_all_channel_videos.return_value = _make_youtube_videos(
            [
                {"video_id": "vid_ok", "title": "Great Interview", "duration_seconds": 3600, "category_id": "27"},
                {"video_id": "vid_shorts", "title": "Best of #shorts compilation", "duration_seconds": 3600, "category_id": "27"},
            ]
        )
        mock_client.quota_used = 2

        await handle_fetch_youtube_channel(session, job)

        result = await session.execute(
            select(Content).where(Content.source_id == source.id)
        )
        content_rows = result.scalars().all()
        by_url = {c.canonical_url: c for c in content_rows}

        assert by_url["https://youtube.com/watch?v=vid_ok"].status == "cataloged"
        assert by_url["https://youtube.com/watch?v=vid_shorts"].status == "skipped"

    @patch("src.thinktank.handlers.fetch_youtube_channel.YouTubeClient")
    async def test_fetch_enqueues_scan_job(
        self, mock_client_cls: MagicMock, session: AsyncSession
    ):
        """Handler enqueues scan_episodes_for_thinkers job with content_ids."""
        source, job = await self._setup_source_and_job(session)

        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_client.fetch_all_channel_videos.return_value = _make_youtube_videos(
            [
                {"video_id": "vid_scan1", "title": "Interview 1", "duration_seconds": 3600, "category_id": "27"},
                {"video_id": "vid_scan2", "title": "Interview 2", "duration_seconds": 2700, "category_id": "24"},
            ]
        )
        mock_client.quota_used = 2

        await handle_fetch_youtube_channel(session, job)

        result = await session.execute(
            select(Job).where(Job.job_type == "scan_episodes_for_thinkers")
        )
        scan_jobs = result.scalars().all()
        assert len(scan_jobs) == 1

        payload = scan_jobs[0].payload
        assert "content_ids" in payload
        assert len(payload["content_ids"]) == 2
        assert "descriptions" in payload

    @patch("src.thinktank.handlers.fetch_youtube_channel.YouTubeClient")
    async def test_fetch_dedup_by_canonical_url(
        self, mock_client_cls: MagicMock, session: AsyncSession
    ):
        """Pre-existing Content with matching canonical_url -> no duplicate created."""
        source, job = await self._setup_source_and_job(session)

        # Pre-create content with same canonical URL
        await create_content(
            session,
            source_id=source.id,
            url="https://www.youtube.com/watch?v=vid_existing",
            canonical_url="https://youtube.com/watch?v=vid_existing",
            title="Existing Video",
        )
        await session.commit()

        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_client.fetch_all_channel_videos.return_value = _make_youtube_videos(
            [
                {"video_id": "vid_existing", "title": "Existing Video", "duration_seconds": 3600, "category_id": "27"},
                {"video_id": "vid_new", "title": "New Video", "duration_seconds": 3600, "category_id": "27"},
            ]
        )
        mock_client.quota_used = 2

        await handle_fetch_youtube_channel(session, job)

        result = await session.execute(
            select(Content).where(Content.source_id == source.id)
        )
        content_rows = result.scalars().all()
        # Should be 2: the pre-existing one + the new one (not a duplicate)
        assert len(content_rows) == 2

    @patch("src.thinktank.handlers.fetch_youtube_channel.YouTubeClient")
    async def test_fetch_updates_source_metadata(
        self, mock_client_cls: MagicMock, session: AsyncSession
    ):
        """source.last_fetched updated, source.item_count incremented."""
        source, job = await self._setup_source_and_job(session)

        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_client.fetch_all_channel_videos.return_value = _make_youtube_videos(
            [
                {"video_id": "vid_meta1", "title": "Meta Video 1", "duration_seconds": 3600, "category_id": "27"},
                {"video_id": "vid_meta2", "title": "Meta Video 2", "duration_seconds": 3600, "category_id": "27"},
            ]
        )
        mock_client.quota_used = 2

        await handle_fetch_youtube_channel(session, job)

        await session.refresh(source)
        assert source.last_fetched is not None
        # item_count should include ALL inserted rows (cataloged + skipped)
        assert source.item_count == 2
        assert source.backfill_complete is True
