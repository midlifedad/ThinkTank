"""Contract tests for discover_expert_sources (W3.1).

Discovery is mocked; the contract under test is source registration:
owned channels -> Source rows (relationship_type='owns'), approval jobs
for new sources, URL dedupe, and idempotency.
"""

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.factories import create_job, create_source, create_thinker
from thinktank.discovery.owned_sources import OwnedChannels
from thinktank.handlers.discover_expert_sources import handle_discover_expert_sources
from thinktank.models.job import Job
from thinktank.models.source import Source, SourceThinker

pytestmark = pytest.mark.anyio


def _channels(**kw):
    base = dict(youtube_channel_url=None, podcast_url=None, substack_url=None, website_url=None, reasoning="test")
    base.update(kw)
    return OwnedChannels(**base)


async def _run(session: AsyncSession, thinker, channels) -> None:
    job = await create_job(session, job_type="discover_expert_sources", payload={"thinker_id": str(thinker.id)})
    with patch(
        "thinktank.handlers.discover_expert_sources.find_owned_channels",
        new=AsyncMock(return_value=channels),
    ):
        await handle_discover_expert_sources(session, job)


class TestDiscoverExpertSources:
    async def test_registers_owned_sources_with_approval_jobs(self, session: AsyncSession):
        thinker = await create_thinker(session, name="Dr. Owner")
        await _run(
            session,
            thinker,
            _channels(
                youtube_channel_url="https://youtube.com/@drowner",
                podcast_url="https://feeds.example.com/drowner.rss",
            ),
        )

        sources = (await session.execute(select(Source))).scalars().all()
        assert {s.source_type for s in sources} == {"youtube_channel", "podcast_rss"}
        for s in sources:
            assert s.approval_status == "pending_llm"
            assert s.config.get("owned_by_thinker") == str(thinker.id)

        links = (await session.execute(select(SourceThinker))).scalars().all()
        assert all(link.relationship_type == "owns" for link in links)
        assert len(links) == 2

        approval_jobs = (await session.execute(select(Job).where(Job.job_type == "llm_approval_check"))).scalars().all()
        assert len(approval_jobs) == 2
        assert all(j.payload["review_type"] == "source_approval" for j in approval_jobs)

    async def test_registers_website_and_substack_without_ingestion_types(self, session: AsyncSession):
        thinker = await create_thinker(session, name="Dr. Web")
        await _run(
            session,
            thinker,
            _channels(website_url="https://drweb.org", substack_url="https://drweb.substack.com"),
        )
        types = {s.source_type for s in (await session.execute(select(Source))).scalars().all()}
        assert types == {"website", "substack"}

    async def test_dedupes_existing_url_adds_owns_link(self, session: AsyncSession):
        thinker = await create_thinker(session, name="Dr. Dup")
        await create_source(session, url="https://youtube.com/@dup", source_type="youtube_channel")
        await _run(session, thinker, _channels(youtube_channel_url="https://youtube.com/@dup"))

        sources = (
            (await session.execute(select(Source).where(Source.url == "https://youtube.com/@dup"))).scalars().all()
        )
        assert len(sources) == 1  # not duplicated
        link = (await session.execute(select(SourceThinker).where(SourceThinker.thinker_id == thinker.id))).scalar_one()
        assert link.relationship_type == "owns"
        # No new approval job for a pre-existing source.
        assert (await session.execute(select(Job).where(Job.job_type == "llm_approval_check"))).scalars().all() == []

    async def test_no_channels_registers_nothing(self, session: AsyncSession):
        thinker = await create_thinker(session, name="Dr. Empty")
        job = await create_job(session, job_type="discover_expert_sources", payload={"thinker_id": str(thinker.id)})
        with patch("thinktank.handlers.discover_expert_sources.find_owned_channels", new=AsyncMock(return_value=None)):
            await handle_discover_expert_sources(session, job)
        assert (await session.execute(select(Source))).scalars().all() == []

    async def test_missing_thinker_id_raises(self, session: AsyncSession):
        job = await create_job(session, job_type="discover_expert_sources", payload={})
        with pytest.raises(ValueError, match="thinker_id missing"):
            await handle_discover_expert_sources(session, job)
