"""Contract tests for ingest_expert_papers (W3.2).

OpenAlex mocked; the contract is: papers -> authored Content
(status='done', role='author') under a per-expert openalex source, ready
for the embed sweep. Dedup by canonical_url; idempotent.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.factories import create_job, create_thinker
from thinktank.discovery.openalex_papers import PaperRecord
from thinktank.handlers.ingest_expert_papers import handle_ingest_expert_papers
from thinktank.models.content import Content, ContentThinker
from thinktank.models.source import Source, SourceThinker

pytestmark = pytest.mark.anyio


def _paper(wid, title, abstract="Some grounded abstract about rapamycin.", date=(2023, 1, 1)):
    return PaperRecord(
        openalex_id=wid,
        title=title,
        abstract=abstract,
        published_at=datetime(*date, tzinfo=UTC),
        landing_url=f"https://doi.org/10.1/{wid}",
    )


async def _run(session: AsyncSession, thinker, papers) -> None:
    job = await create_job(session, job_type="ingest_expert_papers", payload={"thinker_id": str(thinker.id)})
    with patch("thinktank.handlers.ingest_expert_papers.fetch_author_papers", new=AsyncMock(return_value=papers)):
        await handle_ingest_expert_papers(session, job)


class TestIngestExpertPapers:
    async def test_papers_become_authored_done_content(self, session: AsyncSession):
        thinker = await create_thinker(session, name="Dr. Scholar")
        await _run(session, thinker, [_paper("W1", "Paper One"), _paper("W2", "Paper Two")])

        content = (await session.execute(select(Content))).scalars().all()
        assert len(content) == 2
        for c in content:
            assert c.status == "done"
            assert c.content_type == "paper"
            assert c.body_text  # abstract as body
            assert c.published_at is not None

        links = (await session.execute(select(ContentThinker))).scalars().all()
        assert len(links) == 2
        assert all(link.role == "author" for link in links)

        # Immediate embed enqueue -- one embed_content job per paper, in the
        # same transaction, so it chunks+embeds within seconds (not the
        # hourly sweep).
        from thinktank.models.job import Job as JobModel

        embed_jobs = (
            (await session.execute(select(JobModel).where(JobModel.job_type == "embed_content"))).scalars().all()
        )
        assert {j.payload["content_id"] for j in embed_jobs} == {str(c.id) for c in content}

        # A per-expert openalex source, auto-approved, owns-linked.
        source = (await session.execute(select(Source).where(Source.source_type == "openalex"))).scalars().one()
        assert source.approval_status == "approved"
        st = (await session.execute(select(SourceThinker).where(SourceThinker.source_id == source.id))).scalars().one()
        assert st.relationship_type == "owns"

    async def test_dedupes_on_rerun(self, session: AsyncSession):
        thinker = await create_thinker(session, name="Dr. Scholar")
        await _run(session, thinker, [_paper("W1", "Paper One")])
        await _run(session, thinker, [_paper("W1", "Paper One"), _paper("W2", "Paper Two")])

        content = (await session.execute(select(Content))).scalars().all()
        assert len(content) == 2  # W1 not duplicated, W2 added
        sources = (await session.execute(select(Source).where(Source.source_type == "openalex"))).scalars().all()
        assert len(sources) == 1  # one openalex source reused

    async def test_no_papers_creates_nothing(self, session: AsyncSession):
        thinker = await create_thinker(session, name="Dr. Silent")
        await _run(session, thinker, [])
        assert (await session.execute(select(Content))).scalars().all() == []
        assert (await session.execute(select(Source).where(Source.source_type == "openalex"))).scalars().all() == []

    async def test_missing_thinker_id_raises(self, session: AsyncSession):
        job = await create_job(session, job_type="ingest_expert_papers", payload={})
        with pytest.raises(ValueError, match="thinker_id missing"):
            await handle_ingest_expert_papers(session, job)
