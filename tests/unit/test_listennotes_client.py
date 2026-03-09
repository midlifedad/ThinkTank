"""Tests for discovery.listennotes_client -- Listen Notes API wrapper.

Covers:
- Success path: returns parsed JSON fixture
- Rate-limited path: returns None when check_and_acquire_rate_limit is False
- HTTP error path: raises httpx.HTTPStatusError on 4xx/5xx
- Correct headers (X-ListenAPI-Key) and params (q, type, offset)
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.thinktank.discovery.listennotes_client import ListenNotesClient

FIXTURE_PATH = Path(__file__).parent.parent / "fixtures" / "listennotes" / "search_episodes.json"


@pytest.fixture
def fixture_data():
    return json.loads(FIXTURE_PATH.read_text())


@pytest.fixture
def client():
    return ListenNotesClient(api_key="test-api-key-123")


@pytest.fixture
def mock_session():
    return AsyncMock()


class TestSearchEpisodesByPerson:
    """Test ListenNotesClient.search_episodes_by_person."""

    async def test_success_returns_parsed_json(self, client, mock_session, fixture_data):
        """Successful API call returns parsed JSON response."""
        mock_response = MagicMock()
        mock_response.json.return_value = fixture_data
        mock_response.raise_for_status = MagicMock()

        with (
            patch(
                "src.thinktank.discovery.listennotes_client.check_and_acquire_rate_limit",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch("src.thinktank.discovery.listennotes_client.httpx.AsyncClient") as mock_client_cls,
        ):
            mock_client_instance = AsyncMock()
            mock_client_instance.get.return_value = mock_response
            mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
            mock_client_instance.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client_instance

            result = await client.search_episodes_by_person(
                mock_session, "worker-1", "John Smith"
            )

        assert result == fixture_data
        assert result["count"] == 10
        assert len(result["results"]) == 3

    async def test_rate_limited_returns_none(self, client, mock_session):
        """Returns None when rate limiter denies the request."""
        with patch(
            "src.thinktank.discovery.listennotes_client.check_and_acquire_rate_limit",
            new_callable=AsyncMock,
            return_value=False,
        ):
            result = await client.search_episodes_by_person(
                mock_session, "worker-1", "John Smith"
            )

        assert result is None

    async def test_correct_headers_and_params(self, client, mock_session, fixture_data):
        """Sends correct X-ListenAPI-Key header and query params."""
        mock_response = MagicMock()
        mock_response.json.return_value = fixture_data
        mock_response.raise_for_status = MagicMock()

        with (
            patch(
                "src.thinktank.discovery.listennotes_client.check_and_acquire_rate_limit",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch("src.thinktank.discovery.listennotes_client.httpx.AsyncClient") as mock_client_cls,
        ):
            mock_client_instance = AsyncMock()
            mock_client_instance.get.return_value = mock_response
            mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
            mock_client_instance.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client_instance

            await client.search_episodes_by_person(
                mock_session, "worker-1", "John Smith", offset=20
            )

            mock_client_instance.get.assert_called_once_with(
                "https://listen-api.listennotes.com/api/v2/search",
                params={"q": "John Smith", "type": "episode", "offset": 20},
                headers={"X-ListenAPI-Key": "test-api-key-123"},
                timeout=30.0,
            )

    async def test_http_error_raises(self, client, mock_session):
        """Raises httpx.HTTPStatusError on 4xx/5xx responses."""
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Not Found",
            request=MagicMock(),
            response=MagicMock(status_code=404),
        )

        with (
            patch(
                "src.thinktank.discovery.listennotes_client.check_and_acquire_rate_limit",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch("src.thinktank.discovery.listennotes_client.httpx.AsyncClient") as mock_client_cls,
        ):
            mock_client_instance = AsyncMock()
            mock_client_instance.get.return_value = mock_response
            mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
            mock_client_instance.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client_instance

            with pytest.raises(httpx.HTTPStatusError):
                await client.search_episodes_by_person(
                    mock_session, "worker-1", "John Smith"
                )
