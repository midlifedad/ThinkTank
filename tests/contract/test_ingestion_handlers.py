"""Contract tests for all Phase 3 ingestion handlers per QUAL-04.

Each test documents the handler's external contract:
    - Given a specific input payload
    - What side effects are produced (rows created, jobs enqueued, fields updated)

Contract tests are self-contained and verify handler behavior against
a real PostgreSQL database with factory-generated test data.

Handlers tested:
    - fetch_podcast_feed: RSS polling, content insertion, tag job creation
    - refresh_due_sources: Tier-based scheduling, fetch job creation
    - tag_content_thinkers: Content attribution with roles and confidence
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.thinktank.handlers.fetch_podcast_feed import handle_fetch_podcast_feed
from src.thinktank.handlers.refresh_due_sources import handle_refresh_due_sources
from src.thinktank.handlers.tag_content_thinkers import handle_tag_content_thinkers
from src.thinktank.models.content import Content, ContentThinker
from src.thinktank.models.job import Job
from tests.factories import (
    create_content,
    create_job,
    create_source,
    create_source_thinker,
    create_thinker,
)

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


class TestFetchPodcastFeedContract:
    """Contract: fetch_podcast_feed handler.

    Given: job with payload {"source_id": "<uuid>"}, approved active source, RSS feed
    Then: creates content rows, updates source.last_fetched, enqueues tag_content_thinkers
          job with descriptions in payload
    """

    @patch("src.thinktank.handlers.fetch_podcast_feed.httpx.AsyncClient")
    async def test_fetch_podcast_feed_contract(
        self, mock_client_cls: MagicMock, session: AsyncSession
    ):
        """Approved source + RSS feed -> content rows + tag job with descriptions."""
        mock_client_cls.return_value = _make_httpx_mock("podcast_basic.xml")

        thinker = await create_thinker(session, name="Contract Thinker")
        source = await create_source(
            session,
            thinker_id=thinker.id,
            url="https://example.com/feed/contract-fetch.xml",
            approval_status="approved",
            active=True,
            backfill_complete=False,
        )
        job = await create_job(
            session,
            job_type="fetch_podcast_feed",
            payload={"source_id": str(source.id)},
        )
        await session.commit()

        await handle_fetch_podcast_feed(session, job)

        # Contract 1: Content rows created
        result = await session.execute(
            select(Content).where(Content.source_id == source.id)
        )
        content_rows = result.scalars().all()
        assert len(content_rows) == 3

        # Contract 2: Source last_fetched updated
        await session.refresh(source)
        assert source.last_fetched is not None
        assert source.item_count == 3

        # Contract 3: scan_episodes_for_thinkers job enqueued with descriptions
        result = await session.execute(
            select(Job).where(Job.job_type == "scan_episodes_for_thinkers")
        )
        tag_jobs = result.scalars().all()
        assert len(tag_jobs) == 1

        tag_payload = tag_jobs[0].payload
        assert "content_ids" in tag_payload
        assert "source_id" in tag_payload
        assert "descriptions" in tag_payload
        assert len(tag_payload["content_ids"]) == 3
        assert tag_payload["source_id"] == str(source.id)

        # Descriptions dict maps content_id -> description string
        assert isinstance(tag_payload["descriptions"], dict)
        assert len(tag_payload["descriptions"]) == 3
        for cid in tag_payload["content_ids"]:
            assert cid in tag_payload["descriptions"]


class TestRefreshDueSourcesContract:
    """Contract: refresh_due_sources handler.

    Given: no payload + due sources in DB (approved, active, never fetched or interval expired)
    Then: creates fetch_podcast_feed jobs for each due source
    """

    async def test_refresh_due_sources_contract(self, session: AsyncSession):
        """Due sources -> fetch_podcast_feed jobs created."""
        thinker = await create_thinker(session, name="Contract Thinker 2")

        # Due source (never fetched)
        source1 = await create_source(
            session,
            thinker_id=thinker.id,
            url="https://example.com/feed/contract-due1.xml",
            approval_status="approved",
            active=True,
            refresh_interval_hours=6,
            last_fetched=None,
        )

        # Not due source (not approved)
        await create_source(
            session,
            thinker_id=thinker.id,
            url="https://example.com/feed/contract-notdue.xml",
            approval_status="pending_llm",
            active=True,
            refresh_interval_hours=6,
        )

        job = await create_job(
            session,
            job_type="refresh_due_sources",
            payload={},
        )
        await session.commit()

        await handle_refresh_due_sources(session, job)

        # Contract: fetch_podcast_feed jobs created for due sources only
        result = await session.execute(
            select(Job).where(Job.job_type == "fetch_podcast_feed")
        )
        fetch_jobs = result.scalars().all()
        assert len(fetch_jobs) == 1
        assert fetch_jobs[0].payload["source_id"] == str(source1.id)


class TestTagContentThinkersContract:
    """Contract: tag_content_thinkers handler.

    Given: job with payload {"content_ids": [...], "source_id": "...", "descriptions": {...}}
    Then: creates ContentThinker attribution rows with correct roles
    """

    async def test_tag_content_thinkers_contract(self, session: AsyncSession):
        """Content + thinkers -> ContentThinker attributions with correct roles."""
        owner = await create_thinker(session, name="Contract Owner")
        guest = await create_thinker(session, name="Contract Guest")
        source = await create_source(session, thinker_id=owner.id)
        await create_source_thinker(
            session, source_id=source.id, thinker_id=owner.id, relationship_type="host"
        )
        content = await create_content(
            session,
            source_id=source.id,
            source_owner_id=owner.id,
            title="Interview with Contract Guest",
        )

        job = await create_job(
            session,
            job_type="tag_content_thinkers",
            payload={
                "content_ids": [str(content.id)],
                "source_id": str(source.id),
                "descriptions": {
                    str(content.id): "An interview episode."
                },
            },
        )
        await session.commit()

        await handle_tag_content_thinkers(session, job)

        # Contract: ContentThinker rows created with correct roles
        result = await session.execute(
            select(ContentThinker).where(ContentThinker.content_id == content.id)
        )
        attributions = result.scalars().all()

        # Source owner -> primary/10, guest in title -> guest/9
        assert len(attributions) == 2

        by_thinker = {a.thinker_id: a for a in attributions}
        assert by_thinker[owner.id].role == "primary"
        assert by_thinker[owner.id].confidence == 10
        assert by_thinker[guest.id].role == "guest"
        assert by_thinker[guest.id].confidence == 9
