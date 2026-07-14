"""Contract tests for the web_fetch fallback chain (Exa -> Jina -> bs4)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from thinktank.discovery.exa_client import ExaResult
from thinktank.ingestion.web_fetch import fetch_document, store_exa_result
from thinktank.models.claim import Document

pytestmark = pytest.mark.anyio


def _exa(url, text, date=None):
    from datetime import UTC, datetime

    pub = datetime(*date, tzinfo=UTC) if date else None
    return ExaResult(url=url, title="T", text=text, published_at=pub, author="Dr. A")


class TestStoreExaResult:
    async def test_persists_with_date_and_author(self, session: AsyncSession):
        doc = await store_exa_result(session, _exa("https://ex.com/x", "real body", (2024, 2, 1)), found_via="inquiry")
        assert doc is not None
        assert doc.text_content == "real body"
        assert doc.published_at.year == 2024
        assert doc.author == "Dr. A"
        assert doc.fetch_status == "fetched"

    async def test_dedupes_by_url(self, session: AsyncSession):
        await store_exa_result(session, _exa("https://ex.com/dup", "body"), found_via="inquiry")
        again = await store_exa_result(session, _exa("https://ex.com/dup", "body"), found_via="inquiry")
        rows = (await session.execute(select(Document).where(Document.url == "https://ex.com/dup"))).scalars().all()
        assert len(rows) == 1 and again is not None

    async def test_no_text_returns_none(self, session: AsyncSession):
        assert await store_exa_result(session, _exa("https://ex.com/empty", None), found_via="inquiry") is None


class TestFetchDocumentFallbackChain:
    async def test_exa_first(self, session: AsyncSession):
        with patch(
            "thinktank.ingestion.web_fetch.exa_contents",
            new=AsyncMock(return_value=_exa("https://ex.com/a", "exa text", (2023, 1, 1))),
        ):
            doc = await fetch_document(session, "https://ex.com/a", found_via="test")
        assert doc.text_content == "exa text"
        assert doc.published_at.year == 2023

    async def test_falls_back_to_jina(self, session: AsyncSession):
        with (
            patch("thinktank.ingestion.web_fetch.exa_contents", new=AsyncMock(return_value=None)),
            patch(
                "thinktank.ingestion.web_fetch.fetch_via_jina",
                new=AsyncMock(return_value=("jina markdown body", "Jina Title")),
            ),
        ):
            doc = await fetch_document(session, "https://ex.com/b", found_via="test")
        assert doc.text_content == "jina markdown body"
        assert doc.title == "Jina Title"

    async def test_falls_back_to_bs4_with_date(self, session: AsyncSession):
        html = (
            "<html><head><title>T</title>"
            '<meta property="article:published_time" content="2020-06-15T00:00:00Z"></head>'
            "<body><article><p>bs4 extracted body text here</p></article></body></html>"
        )
        resp = MagicMock()
        resp.text = html
        resp.headers = {"content-type": "text/html"}
        resp.raise_for_status = MagicMock()
        with (
            patch("thinktank.ingestion.web_fetch.exa_contents", new=AsyncMock(return_value=None)),
            patch("thinktank.ingestion.web_fetch.fetch_via_jina", new=AsyncMock(return_value=None)),
            patch("thinktank.ingestion.web_fetch.httpx.AsyncClient") as mock_client,
        ):
            instance = mock_client.return_value.__aenter__.return_value
            instance.get = AsyncMock(return_value=resp)
            doc = await fetch_document(session, "https://ex.com/c", found_via="test")
        assert "bs4 extracted body text" in doc.text_content
        assert doc.published_at.year == 2020

    async def test_all_fail_records_failed_row_returns_none(self, session: AsyncSession):
        with (
            patch("thinktank.ingestion.web_fetch.exa_contents", new=AsyncMock(return_value=None)),
            patch("thinktank.ingestion.web_fetch.fetch_via_jina", new=AsyncMock(return_value=None)),
            patch("thinktank.ingestion.web_fetch.httpx.AsyncClient") as mock_client,
        ):
            instance = mock_client.return_value.__aenter__.return_value
            instance.get = AsyncMock(side_effect=RuntimeError("dead link"))
            doc = await fetch_document(session, "https://ex.com/dead", found_via="test")
        assert doc is None
        row = (await session.execute(select(Document).where(Document.url == "https://ex.com/dead"))).scalar_one()
        assert row.fetch_status == "failed" and row.text_content is None
