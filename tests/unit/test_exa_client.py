"""Unit tests for the Exa client (Web-Lane Hardening W1).

HTTP is mocked; the contract under test is: parse results into
ExaResult (text truncation, date parsing, author), degrade to [] on
missing key / failure, and record an api_usage row on success.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from thinktank.discovery.exa_client import ExaResult, _parse_published, _to_results, exa_contents, exa_search

pytestmark = pytest.mark.anyio


class TestParsePublished:
    def test_full_iso(self):
        dt = _parse_published("2024-03-15T10:30:00Z")
        assert dt is not None and dt.year == 2024 and dt.tzinfo is not None

    def test_date_only_gets_utc(self):
        dt = _parse_published("2024-03-15")
        assert dt is not None and dt.tzinfo is not None

    def test_none_and_garbage(self):
        assert _parse_published(None) is None
        assert _parse_published("not a date") is None


class TestToResults:
    def test_maps_fields_and_truncates(self):
        payload = {
            "results": [
                {
                    "url": "https://ex.com/a",
                    "title": "A",
                    "text": "x" * 100_000,
                    "publishedDate": "2023-01-02",
                    "author": "Dr. Test",
                },
                {"url": "https://ex.com/b", "title": "B", "text": "", "publishedDate": None},
                {"title": "no url — dropped"},
            ]
        }
        results = _to_results(payload)
        assert len(results) == 2
        assert len(results[0].text) == 60_000  # MAX_TEXT_CHARS
        assert results[0].author == "Dr. Test"
        assert results[0].published_at is not None
        assert results[1].text is None  # empty string -> None


def _resp(json_body):
    r = MagicMock()
    r.json.return_value = json_body
    r.status_code = 200
    r.raise_for_status = MagicMock()
    return r


class TestExaSearch:
    async def test_missing_key_returns_empty(self, session):
        with patch("thinktank.discovery.exa_client.get_secret", new=AsyncMock(return_value=None)):
            assert await exa_search(session, "query") == []

    async def test_success_returns_results_and_records_cost(self, session):
        from sqlalchemy import func, select

        from thinktank.models.api_usage import ApiUsage

        body = {"results": [{"url": "https://ex.com/a", "title": "A", "text": "hello", "publishedDate": "2024-01-01"}]}
        cost_q = select(func.count()).select_from(ApiUsage).where(ApiUsage.endpoint == "exa_search")
        before = await session.scalar(cost_q)
        with (
            patch("thinktank.discovery.exa_client.get_secret", new=AsyncMock(return_value="key")),
            patch("thinktank.discovery.exa_client.httpx.AsyncClient") as mock_client,
        ):
            instance = mock_client.return_value.__aenter__.return_value
            instance.post = AsyncMock(return_value=_resp(body))
            results = await exa_search(session, "query")

        assert len(results) == 1 and results[0].url == "https://ex.com/a"
        assert await session.scalar(cost_q) == before + 1

    async def test_http_failure_degrades_to_empty(self, session):
        with (
            patch("thinktank.discovery.exa_client.get_secret", new=AsyncMock(return_value="key")),
            patch("thinktank.discovery.exa_client.httpx.AsyncClient") as mock_client,
        ):
            instance = mock_client.return_value.__aenter__.return_value
            instance.post = AsyncMock(side_effect=RuntimeError("boom"))
            assert await exa_search(session, "query") == []


class TestExaContents:
    async def test_returns_result_with_text(self, session):
        body = {"results": [{"url": "https://ex.com/a", "title": "A", "text": "body text", "publishedDate": None}]}
        with (
            patch("thinktank.discovery.exa_client.get_secret", new=AsyncMock(return_value="key")),
            patch("thinktank.discovery.exa_client.httpx.AsyncClient") as mock_client,
        ):
            instance = mock_client.return_value.__aenter__.return_value
            instance.post = AsyncMock(return_value=_resp(body))
            result = await exa_contents(session, "https://ex.com/a")
        assert isinstance(result, ExaResult) and result.text == "body text"

    async def test_no_text_returns_none(self, session):
        body = {"results": [{"url": "https://ex.com/a", "title": "A", "text": "", "publishedDate": None}]}
        with (
            patch("thinktank.discovery.exa_client.get_secret", new=AsyncMock(return_value="key")),
            patch("thinktank.discovery.exa_client.httpx.AsyncClient") as mock_client,
        ):
            instance = mock_client.return_value.__aenter__.return_value
            instance.post = AsyncMock(return_value=_resp(body))
            assert await exa_contents(session, "https://ex.com/a") is None

    async def test_missing_key_returns_none(self, session):
        with patch("thinktank.discovery.exa_client.get_secret", new=AsyncMock(return_value=None)):
            assert await exa_contents(session, "https://ex.com/a") is None
