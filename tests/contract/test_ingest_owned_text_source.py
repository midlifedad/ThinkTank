"""Contract tests for ingest_owned_text_source (W3.2b).

RSS/Exa/fetch mocked; the contract is: website + Substack owned sources
become authored 'article' Content (role='author', status='done'), with
the website path kept to the source's own domain.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.factories import create_job, create_source, create_source_thinker, create_thinker
from thinktank.discovery.exa_client import ExaResult
from thinktank.handlers.ingest_owned_text_source import handle_ingest_owned_text_source
from thinktank.models.claim import Document
from thinktank.models.content import Content, ContentThinker

pytestmark = pytest.mark.anyio


async def _owned_source(session, thinker, source_type, url):
    source = await create_source(session, source_type=source_type, url=url, approval_status="approved")
    await create_source_thinker(session, source_id=source.id, thinker_id=thinker.id, relationship_type="owns")
    return source


def _doc(url, text, date=(2023, 6, 1)):
    return Document(
        url=url,
        text_content=text,
        published_at=datetime(*date, tzinfo=UTC),
        fetch_status="fetched",
    )


class TestSubstackIngestion:
    async def test_rss_posts_become_authored_articles(self, session: AsyncSession):
        thinker = await create_thinker(session, name="Dr. Blogger")
        source = await _owned_source(session, thinker, "substack", "https://drblogger.substack.com")

        from thinktank.ingestion.feed_parser import FeedEntry

        entries = [
            FeedEntry(
                title="On Rapamycin",
                url="https://drblogger.substack.com/p/rapamycin",
                published_at=datetime(2024, 1, 2, tzinfo=UTC),
                duration_seconds=None,
                show_name="Dr. Blogger",
                description="excerpt",
            )
        ]
        resp = MagicMock()
        resp.text = "<rss/>"
        resp.raise_for_status = MagicMock()
        job = await create_job(session, job_type="ingest_owned_text_source", payload={"source_id": str(source.id)})
        with (
            patch("thinktank.handlers.ingest_owned_text_source.httpx.AsyncClient") as client,
            patch("thinktank.handlers.ingest_owned_text_source.parse_feed", return_value=entries),
            patch(
                "thinktank.handlers.ingest_owned_text_source.fetch_document",
                new=AsyncMock(return_value=_doc("https://drblogger.substack.com/p/rapamycin", "Full post body.")),
            ),
        ):
            client.return_value.__aenter__.return_value.get = AsyncMock(return_value=resp)
            await handle_ingest_owned_text_source(session, job)

        content = (await session.execute(select(Content))).scalars().one()
        assert content.content_type == "article"
        assert content.status == "done"
        assert content.body_text == "Full post body."
        link = (await session.execute(select(ContentThinker))).scalars().one()
        assert link.role == "author" and link.thinker_id == thinker.id


class TestWebsiteIngestion:
    async def test_only_own_domain_articles_ingested(self, session: AsyncSession):
        thinker = await create_thinker(session, name="Dr. Site")
        source = await _owned_source(session, thinker, "website", "https://drsite.com")

        results = [
            ExaResult(
                url="https://drsite.com/essay-1",
                title="Essay One",
                text="An essay by the expert.",
                published_at=datetime(2022, 3, 3, tzinfo=UTC),
                author="Dr. Site",
            ),
            ExaResult(  # third-party domain -> must be skipped
                url="https://someoutlet.com/about-dr-site",
                title="Profile",
                text="A journalist writing about the expert.",
                published_at=None,
                author="Reporter",
            ),
        ]
        job = await create_job(session, job_type="ingest_owned_text_source", payload={"source_id": str(source.id)})
        with patch("thinktank.handlers.ingest_owned_text_source.exa_search", new=AsyncMock(return_value=results)):
            await handle_ingest_owned_text_source(session, job)

        content = (await session.execute(select(Content))).scalars().all()
        assert len(content) == 1  # only the own-domain essay
        assert content[0].url == "https://drsite.com/essay-1"
        assert content[0].body_text == "An essay by the expert."


class TestGuards:
    async def test_source_without_owner_noops(self, session: AsyncSession):
        source = await create_source(session, source_type="website", url="https://orphan.com")
        job = await create_job(session, job_type="ingest_owned_text_source", payload={"source_id": str(source.id)})
        await handle_ingest_owned_text_source(session, job)
        assert (await session.execute(select(Content))).scalars().all() == []

    async def test_missing_source_id_raises(self, session: AsyncSession):
        job = await create_job(session, job_type="ingest_owned_text_source", payload={})
        with pytest.raises(ValueError, match="source_id missing"):
            await handle_ingest_owned_text_source(session, job)
