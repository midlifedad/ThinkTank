"""Contract tests for Phase 6 discovery handlers.

Verifies handler side effects: given known input payload and (where needed)
mocked external APIs, each handler produces exactly the expected database
rows. Follows the pattern from test_llm_approval_handler.py.

Contract pattern: Given input payload -> expected side effects.
"""

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.factories import create_content, create_job, create_source, create_thinker
from thinktank.handlers.discover_guests_podcastindex import handle_discover_guests_podcastindex
from thinktank.handlers.scan_for_candidates import handle_scan_for_candidates
from thinktank.models.candidate import CandidateThinker
from thinktank.models.source import Source, SourceThinker

pytestmark = pytest.mark.anyio


class TestScanForCandidatesContract:
    """Given content with guest name -> creates 1 CandidateThinker row."""

    async def test_scan_for_candidates_contract(self, session: AsyncSession):
        """Content with one guest name produces exactly 1 candidate."""
        await create_thinker(session, name="Host Person")
        source = await create_source(session)
        content = await create_content(session, source_id=source.id, title="Interview with Alice Johnson")
        job = await create_job(session, job_type="scan_for_candidates", payload={"content_ids": [str(content.id)]})
        await session.commit()

        await handle_scan_for_candidates(session, job)

        # Contract: exactly 1 CandidateThinker row created
        candidate_count = await session.scalar(select(func.count()).select_from(CandidateThinker))
        assert candidate_count == 1

        # Contract: status is pending_llm, normalized_name matches
        result = await session.execute(select(CandidateThinker))
        candidate = result.scalar_one()
        assert candidate.status == "pending_llm"
        assert candidate.normalized_name == "alice johnson"
        assert candidate.appearance_count == 1


class TestDiscoverGuestsPodcastindexContract:
    """Given thinker + mocked API -> creates Source rows with approval_status=pending_llm."""

    async def test_discover_guests_podcastindex_contract(self, session: AsyncSession):
        """API with 1 result with feedUrl -> exactly 1 Source created."""
        thinker = await create_thinker(session, name="Carol Davis")
        job = await create_job(
            session, job_type="discover_guests_podcastindex", payload={"thinker_id": str(thinker.id)}
        )
        await session.commit()

        api_data = {
            "items": [
                {
                    "feedTitle": "Tech Insights Podcast",
                    "feedUrl": "https://feeds.example.com/tech-insights.xml",
                },
            ],
        }

        mock_instance = AsyncMock()
        mock_instance.search_by_person = AsyncMock(return_value=api_data)

        with (
            patch(
                "thinktank.handlers.discover_guests_podcastindex.PodcastIndexClient",
                lambda api_key, api_secret: mock_instance,
            ),
            patch.dict("os.environ", {"PODCASTINDEX_API_KEY": "test-key", "PODCASTINDEX_API_SECRET": "test-secret"}),
        ):
            await handle_discover_guests_podcastindex(session, job)

        # Contract: exactly 1 Source created
        source_count = await session.scalar(
            select(func.count()).select_from(Source).where(Source.approval_status == "pending_llm")
        )
        assert source_count == 1

        # Contract: Source has correct approval_status and junction link
        result = await session.execute(select(Source).where(Source.approval_status == "pending_llm"))
        source = result.scalar_one()
        assert source.approval_status == "pending_llm"
        assert source.name == "Tech Insights Podcast"

        # Verify junction row links source to thinker
        junc_result = await session.execute(select(SourceThinker).where(SourceThinker.source_id == source.id))
        junc = junc_result.scalar_one()
        assert junc.thinker_id == thinker.id
