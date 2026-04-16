"""Tests for discovery.podcastindex_client -- Podcast Index API wrapper.

Covers:
- Success path: returns parsed JSON fixture
- Rate-limited path: returns None
- Correct auth headers (X-Auth-Key, X-Auth-Date, Authorization SHA-1)
- Auth headers regenerated per request (fresh timestamp each time)
- HTTP error path: raises httpx.HTTPStatusError on 4xx/5xx
"""

import hashlib
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from thinktank.discovery.podcastindex_client import PodcastIndexClient, _podcastindex_headers

FIXTURE_PATH = Path(__file__).parent.parent / "fixtures" / "podcastindex" / "search_byperson.json"


@pytest.fixture
def fixture_data():
    return json.loads(FIXTURE_PATH.read_text())


@pytest.fixture
def client():
    return PodcastIndexClient(api_key="test-key", api_secret="test-secret")


@pytest.fixture
def mock_session():
    return AsyncMock()


class TestPodcastIndexHeaders:
    """Test _podcastindex_headers auth header generation."""

    def test_generates_correct_sha1(self):
        """Auth header is SHA-1 of api_key + api_secret + epoch_time."""
        with patch("thinktank.discovery.podcastindex_client.time") as mock_time:
            mock_time.time.return_value = 1700000000.0

            headers = _podcastindex_headers("my-key", "my-secret")

            expected_hash = hashlib.sha1(
                "my-keymy-secret1700000000".encode("utf-8")
            ).hexdigest()

            assert headers["X-Auth-Key"] == "my-key"
            assert headers["X-Auth-Date"] == "1700000000"
            assert headers["Authorization"] == expected_hash
            assert headers["User-Agent"] == "ThinkTank/1.0"

    def test_fresh_timestamp_per_call(self):
        """Each call gets a fresh timestamp from time.time()."""
        with patch("thinktank.discovery.podcastindex_client.time") as mock_time:
            mock_time.time.side_effect = [1000.0, 2000.0]

            h1 = _podcastindex_headers("k", "s")
            h2 = _podcastindex_headers("k", "s")

            assert h1["X-Auth-Date"] == "1000"
            assert h2["X-Auth-Date"] == "2000"
            assert h1["Authorization"] != h2["Authorization"]


class TestSearchByPerson:
    """Test PodcastIndexClient.search_by_person."""

    async def test_success_returns_parsed_json(self, client, mock_session, fixture_data):
        """Successful API call returns parsed JSON response."""
        mock_response = MagicMock()
        mock_response.json.return_value = fixture_data
        mock_response.raise_for_status = MagicMock()

        with (
            patch(
                "thinktank.discovery.podcastindex_client.check_and_acquire_rate_limit",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch("thinktank.discovery.podcastindex_client.httpx.AsyncClient") as mock_client_cls,
            patch("thinktank.discovery.podcastindex_client.time") as mock_time,
        ):
            mock_time.time.return_value = 1700000000.0
            mock_client_instance = AsyncMock()
            mock_client_instance.get.return_value = mock_response
            mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
            mock_client_instance.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client_instance

            result = await client.search_by_person(
                mock_session, "worker-1", "John Smith"
            )

        assert result == fixture_data
        assert result["count"] == 2
        assert len(result["items"]) == 2

    async def test_rate_limited_returns_none(self, client, mock_session):
        """Returns None when rate limiter denies the request."""
        with patch(
            "thinktank.discovery.podcastindex_client.check_and_acquire_rate_limit",
            new_callable=AsyncMock,
            return_value=False,
        ):
            result = await client.search_by_person(
                mock_session, "worker-1", "John Smith"
            )

        assert result is None

    async def test_correct_url_and_params(self, client, mock_session, fixture_data):
        """Sends correct URL and query params."""
        mock_response = MagicMock()
        mock_response.json.return_value = fixture_data
        mock_response.raise_for_status = MagicMock()

        with (
            patch(
                "thinktank.discovery.podcastindex_client.check_and_acquire_rate_limit",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch("thinktank.discovery.podcastindex_client.httpx.AsyncClient") as mock_client_cls,
            patch("thinktank.discovery.podcastindex_client.time") as mock_time,
        ):
            mock_time.time.return_value = 1700000000.0
            mock_client_instance = AsyncMock()
            mock_client_instance.get.return_value = mock_response
            mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
            mock_client_instance.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client_instance

            await client.search_by_person(mock_session, "worker-1", "John Smith")

            call_args = mock_client_instance.get.call_args
            assert call_args[0][0] == "https://api.podcastindex.org/api/1.0/search/byperson"
            assert call_args[1]["params"] == {"q": "John Smith"}
            assert call_args[1]["timeout"] == 30.0

    async def test_http_error_raises(self, client, mock_session):
        """Raises httpx.HTTPStatusError on 4xx/5xx responses."""
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Unauthorized",
            request=MagicMock(),
            response=MagicMock(status_code=401),
        )

        with (
            patch(
                "thinktank.discovery.podcastindex_client.check_and_acquire_rate_limit",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch("thinktank.discovery.podcastindex_client.httpx.AsyncClient") as mock_client_cls,
            patch("thinktank.discovery.podcastindex_client.time") as mock_time,
        ):
            mock_time.time.return_value = 1700000000.0
            mock_client_instance = AsyncMock()
            mock_client_instance.get.return_value = mock_response
            mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
            mock_client_instance.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client_instance

            with pytest.raises(httpx.HTTPStatusError):
                await client.search_by_person(mock_session, "worker-1", "John Smith")
