"""Integration tests for the catalog-then-promote pipeline.

End-to-end tests proving:
    1. fetch_podcast_feed creates Content with status='cataloged'
    2. scan_episodes_for_thinkers promotes matching episodes to 'pending'
    3. Guest sources only promote name-matched episodes (80%+ savings)
    4. Host sources promote all episodes
    5. rescan_cataloged_for_thinker handles retroactive thinker approval
    6. Existing pending episodes are not demoted

These tests exercise the full handler chain against real PostgreSQL.
"""

import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.factories import (
    create_content,
    create_job,
    create_source,
    create_source_thinker,
    create_thinker,
)
from thinktank.handlers.fetch_podcast_feed import handle_fetch_podcast_feed
from thinktank.handlers.rescan_cataloged_for_thinker import (
    handle_rescan_cataloged_for_thinker,
)
from thinktank.handlers.scan_episodes_for_thinkers import (
    handle_scan_episodes_for_thinkers,
)
from thinktank.models.content import Content, ContentThinker
from thinktank.models.job import Job

FIXTURES = Path(__file__).parent.parent / "fixtures" / "rss"

pytestmark = pytest.mark.anyio


def _build_rss_feed(episodes: list[dict]) -> str:
    """Build a minimal RSS feed XML string from episode dicts.

    Each episode dict should have: title, url, description, guid.
    Optional: duration_seconds (encoded as enclosure length in bytes).
    """
    items = []
    for i, ep in enumerate(episodes):
        title = ep.get("title", f"Episode {i}")
        url = ep.get("url", f"https://example.com/ep/{uuid.uuid4().hex[:8]}")
        desc = ep.get("description", "No description")
        guid = ep.get("guid", f"guid-{uuid.uuid4().hex[:8]}")
        # Use 7200 seconds (2 hours) as default -- above the 600s min_duration threshold
        dur_seconds = ep.get("duration_seconds", 7200)
        length_bytes = dur_seconds * 16000  # ~16 kBps audio approximation

        items.append(f"""
    <item>
      <title>{title}</title>
      <link>{url}</link>
      <description>{desc}</description>
      <pubDate>Sat, 01 Mar 2026 10:00:00 GMT</pubDate>
      <enclosure url="{url}.mp3" length="{length_bytes}" type="audio/mpeg"/>
      <guid isPermaLink="false">{guid}</guid>
    </item>""")

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Test Podcast</title>
    <link>https://example.com/podcast</link>
    <description>Integration test feed</description>
    {"".join(items)}
  </channel>
</rss>"""


def _mock_httpx_for_xml(xml_text: str):
    """Create an AsyncMock for httpx.AsyncClient that returns the given XML."""
    mock_response = MagicMock()
    mock_response.text = xml_text
    mock_response.status_code = 200
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)

    mock_cm = AsyncMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_client)
    mock_cm.__aexit__ = AsyncMock(return_value=False)

    return mock_cm


class TestFullPipelineGuestSourceEfficiency:
    """Keystone test: proves the efficiency claim for guest sources.

    10 episodes, only 2 mention a known thinker -> 2 promoted, 8 stay cataloged.
    80% transcription cost savings.
    """

    @patch("thinktank.handlers.fetch_podcast_feed.httpx.AsyncClient")
    async def test_full_pipeline_guest_source_efficiency(self, mock_client_cls: MagicMock, session: AsyncSession):
        # Setup: create an approved thinker
        sam = await create_thinker(session, name="Sam Harris")

        # Setup: create a guest source (no host SourceThinker)
        source = await create_source(
            session,
            url="https://example.com/feed/guest-test.xml",
            approval_status="approved",
            active=True,
            backfill_complete=False,
        )

        # Build RSS feed: 2 episodes mention Sam Harris, 8 do not
        episodes = [
            {
                "title": "Sam Harris on Consciousness",
                "guid": "ep-sam-1",
                "description": "Deep conversation with Sam Harris about free will.",
            },
            {
                "title": "Interview with Sam Harris",
                "guid": "ep-sam-2",
                "description": "Sam Harris discusses meditation and mindfulness.",
            },
        ]
        for i in range(8):
            episodes.append(
                {
                    "title": f"Unrelated Topic Number {i + 1}",
                    "guid": f"ep-unrelated-{i}",
                    "description": f"Discussion about topic {i + 1} with no guest overlap.",
                }
            )

        xml = _build_rss_feed(episodes)
        mock_client_cls.return_value = _mock_httpx_for_xml(xml)

        # Step 1: fetch_podcast_feed -> creates cataloged content
        fetch_job = await create_job(
            session,
            job_type="fetch_podcast_feed",
            payload={"source_id": str(source.id)},
        )
        await session.commit()

        await handle_fetch_podcast_feed(session, fetch_job)

        # Verify: 10 content rows, all with status='cataloged'
        result = await session.execute(select(Content).where(Content.source_id == source.id))
        all_content = result.scalars().all()
        non_skipped = [c for c in all_content if c.status != "skipped"]
        assert len(non_skipped) == 10, f"Expected 10 cataloged, got {len(non_skipped)}"
        for c in non_skipped:
            assert c.status == "cataloged", f"Expected cataloged, got {c.status} for '{c.title}'"

        # Step 2: Load the scan_episodes_for_thinkers job from the jobs table
        scan_result = await session.execute(select(Job).where(Job.job_type == "scan_episodes_for_thinkers"))
        scan_job = scan_result.scalar_one()

        await handle_scan_episodes_for_thinkers(session, scan_job)

        # Verify: 2 promoted to pending, 8 still cataloged
        result = await session.execute(
            select(Content).where(
                Content.source_id == source.id,
                Content.status == "pending",
            )
        )
        pending = result.scalars().all()
        assert len(pending) == 2, f"Expected 2 pending, got {len(pending)}"

        result = await session.execute(
            select(Content).where(
                Content.source_id == source.id,
                Content.status == "cataloged",
            )
        )
        still_cataloged = result.scalars().all()
        assert len(still_cataloged) == 8, f"Expected 8 cataloged, got {len(still_cataloged)}"

        # Verify: ContentThinker rows created for promoted episodes
        ct_result = await session.execute(select(ContentThinker).where(ContentThinker.thinker_id == sam.id))
        attributions = ct_result.scalars().all()
        assert len(attributions) == 2

        # Log efficiency metric
        total = len(non_skipped)
        promoted = len(pending)
        savings_pct = (1 - promoted / total) * 100
        print(f"Efficiency: {promoted}/{total} promoted ({savings_pct:.0f}% savings)")


class TestFullPipelineHostSourceAllPromoted:
    """Host source: ALL cataloged episodes promoted regardless of title content."""

    @patch("thinktank.handlers.fetch_podcast_feed.httpx.AsyncClient")
    async def test_full_pipeline_host_source_all_promoted(self, mock_client_cls: MagicMock, session: AsyncSession):
        # Setup: create thinker and host source
        lex = await create_thinker(session, name="Lex Fridman")
        source = await create_source(
            session,
            url="https://example.com/feed/host-test.xml",
            approval_status="approved",
            active=True,
            backfill_complete=False,
        )
        await create_source_thinker(
            session,
            source_id=source.id,
            thinker_id=lex.id,
            relationship_type="host",
        )

        # Build RSS feed: 5 episodes, none mention Lex in title (shouldn't matter)
        episodes = [
            {"title": "Conversation about Physics", "guid": "ep-host-1"},
            {"title": "The Nature of Intelligence", "guid": "ep-host-2"},
            {"title": "Discussion on Free Will", "guid": "ep-host-3"},
            {"title": "Future of Robotics", "guid": "ep-host-4"},
            {"title": "Programming Best Practices", "guid": "ep-host-5"},
        ]
        xml = _build_rss_feed(episodes)
        mock_client_cls.return_value = _mock_httpx_for_xml(xml)

        # Step 1: fetch_podcast_feed
        fetch_job = await create_job(
            session,
            job_type="fetch_podcast_feed",
            payload={"source_id": str(source.id)},
        )
        await session.commit()

        await handle_fetch_podcast_feed(session, fetch_job)

        # Verify: all 5 cataloged
        result = await session.execute(
            select(Content).where(
                Content.source_id == source.id,
                Content.status == "cataloged",
            )
        )
        cataloged = result.scalars().all()
        assert len(cataloged) == 5

        # Step 2: scan_episodes_for_thinkers
        scan_result = await session.execute(select(Job).where(Job.job_type == "scan_episodes_for_thinkers"))
        scan_job = scan_result.scalar_one()

        await handle_scan_episodes_for_thinkers(session, scan_job)

        # Verify: ALL 5 promoted to pending (host source)
        result = await session.execute(
            select(Content).where(
                Content.source_id == source.id,
                Content.status == "pending",
            )
        )
        pending = result.scalars().all()
        assert len(pending) == 5, f"Expected 5 pending (host), got {len(pending)}"

        # Verify: ContentThinker rows with role=primary, confidence=10
        ct_result = await session.execute(select(ContentThinker).where(ContentThinker.thinker_id == lex.id))
        attributions = ct_result.scalars().all()
        assert len(attributions) == 5
        for attr in attributions:
            assert attr.role == "primary"
            assert attr.confidence == 10


class TestRescanPromotesAfterNewThinker:
    """After adding a new thinker, rescan promotes previously-cataloged episodes."""

    async def test_rescan_promotes_after_new_thinker(self, session: AsyncSession):
        # Setup: create source and 5 cataloged content items
        source = await create_source(session)

        # 1 title mentions "Naval Ravikant", 4 do not
        matching = await create_content(
            session,
            source_id=source.id,
            title="Naval Ravikant on Getting Rich",
            status="cataloged",
        )
        non_matching = []
        for i in range(4):
            c = await create_content(
                session,
                source_id=source.id,
                title=f"Unrelated Episode About Topic {i}",
                status="cataloged",
            )
            non_matching.append(c)

        # No thinker named "Naval Ravikant" exists yet -- content just sits as cataloged

        # Now create the thinker (simulating approval)
        naval = await create_thinker(session, name="Naval Ravikant")

        # Create rescan job
        rescan_job = await create_job(
            session,
            job_type="rescan_cataloged_for_thinker",
            payload={
                "thinker_id": str(naval.id),
                "thinker_name": "Naval Ravikant",
            },
        )
        await session.commit()

        await handle_rescan_cataloged_for_thinker(session, rescan_job)

        # Verify: 1 promoted, 4 still cataloged
        await session.refresh(matching)
        assert matching.status == "pending"

        for c in non_matching:
            await session.refresh(c)
            assert c.status == "cataloged"

        # Verify: ContentThinker with role=guest, confidence=7
        ct = await session.get(ContentThinker, (matching.id, naval.id))
        assert ct is not None
        assert ct.role == "guest"
        assert ct.confidence == 7

    async def test_rescan_word_boundary_rejects_substring(self, session: AsyncSession):
        """Title 'Scam Harrison investigates' must NOT promote for thinker 'Sam Harris' (ME-01)."""
        source = await create_source(session)

        # Substring false-positive candidate — ILIKE would match but word-boundary rejects
        false_positive = await create_content(
            session,
            source_id=source.id,
            title="Scam Harrison investigates podcast fraud",
            status="cataloged",
        )
        # Genuine match
        true_positive = await create_content(
            session,
            source_id=source.id,
            title="Guest: Sam Harris on meditation",
            status="cataloged",
        )

        sam = await create_thinker(session, name="Sam Harris")
        rescan_job = await create_job(
            session,
            job_type="rescan_cataloged_for_thinker",
            payload={
                "thinker_id": str(sam.id),
                "thinker_name": "Sam Harris",
            },
        )
        await session.commit()

        await handle_rescan_cataloged_for_thinker(session, rescan_job)

        await session.refresh(false_positive)
        await session.refresh(true_positive)

        assert false_positive.status == "cataloged", "Scam Harrison must NOT promote — substring false-positive"
        assert true_positive.status == "pending"

        # Only true positive got an attribution
        ct_false = await session.get(ContentThinker, (false_positive.id, sam.id))
        assert ct_false is None
        ct_true = await session.get(ContentThinker, (true_positive.id, sam.id))
        assert ct_true is not None


class TestExistingPendingEpisodesNotDemoted:
    """Per D-05: existing pending episodes should not be affected by scan."""

    async def test_existing_pending_episodes_not_demoted(self, session: AsyncSession):
        # Setup: create source and mixed-status content
        thinker = await create_thinker(session, name="Test Thinker For D05")
        source = await create_source(session)
        await create_source_thinker(
            session,
            source_id=source.id,
            thinker_id=thinker.id,
            relationship_type="host",
        )

        # Pre-existing pending episode (should NOT be touched)
        pre_existing = await create_content(
            session,
            source_id=source.id,
            title="Already Pending Episode",
            status="pending",
        )

        # Cataloged episodes (should be promoted by host scan)
        cataloged_1 = await create_content(
            session,
            source_id=source.id,
            title="New Cataloged Episode 1",
            status="cataloged",
        )
        cataloged_2 = await create_content(
            session,
            source_id=source.id,
            title="New Cataloged Episode 2",
            status="cataloged",
        )

        job = await create_job(
            session,
            job_type="scan_episodes_for_thinkers",
            payload={
                "content_ids": [str(pre_existing.id), str(cataloged_1.id), str(cataloged_2.id)],
                "source_id": str(source.id),
                "descriptions": {},
            },
        )
        await session.commit()

        await handle_scan_episodes_for_thinkers(session, job)

        # Verify: pre-existing pending is still pending (not touched)
        await session.refresh(pre_existing)
        assert pre_existing.status == "pending"

        # Verify: cataloged episodes were promoted
        await session.refresh(cataloged_1)
        assert cataloged_1.status == "pending"

        await session.refresh(cataloged_2)
        assert cataloged_2.status == "pending"

        # Verify: no ContentThinker for the pre-existing pending episode
        # (scan handler skips non-cataloged content)
        ct_pre = await session.get(ContentThinker, (pre_existing.id, thinker.id))
        assert ct_pre is None, "Pre-existing pending episode should not get new attribution"

        # But cataloged episodes should have attribution
        ct_1 = await session.get(ContentThinker, (cataloged_1.id, thinker.id))
        assert ct_1 is not None
        ct_2 = await session.get(ContentThinker, (cataloged_2.id, thinker.id))
        assert ct_2 is not None
