"""Unit tests for existing transcript fetch (Pass 2).

Spec reference: Section 7.2.
All httpx calls mocked -- no external I/O.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest


class TestFetchExistingTranscript:
    """Test suite for fetch_existing_transcript."""

    @pytest.mark.asyncio
    @patch("src.thinktank.transcription.existing.httpx.AsyncClient")
    async def test_fetch_existing_success(self, mock_client_cls):
        """Successful fetch returns extracted plain text from HTML response."""
        from src.thinktank.transcription.existing import fetch_existing_transcript

        html_body = "<html><body><p>This is the transcript text.</p></body></html>"
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = html_body
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        result = await fetch_existing_transcript(
            content_url="https://show.com/episodes/ep-42",
            transcript_url_pattern="https://show.com/transcripts/{slug}",
        )

        assert result is not None
        assert "This is the transcript text." in result
        # No HTML tags in result
        assert "<p>" not in result
        assert "</p>" not in result

    @pytest.mark.asyncio
    async def test_fetch_existing_no_pattern(self):
        """Returns None immediately when no transcript_url_pattern is provided."""
        from src.thinktank.transcription.existing import fetch_existing_transcript

        result = await fetch_existing_transcript(
            content_url="https://show.com/episodes/ep-42",
            transcript_url_pattern=None,
        )

        assert result is None

    @pytest.mark.asyncio
    @patch("src.thinktank.transcription.existing.httpx.AsyncClient")
    async def test_fetch_existing_404(self, mock_client_cls):
        """Returns None on 404 response (transcript doesn't exist)."""
        from src.thinktank.transcription.existing import fetch_existing_transcript

        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.raise_for_status = MagicMock(
            side_effect=httpx.HTTPStatusError(
                "Not Found", request=MagicMock(), response=mock_response
            )
        )

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        result = await fetch_existing_transcript(
            content_url="https://show.com/episodes/ep-99",
            transcript_url_pattern="https://show.com/transcripts/{slug}",
        )

        assert result is None

    @pytest.mark.asyncio
    @patch("src.thinktank.transcription.existing.httpx.AsyncClient")
    async def test_fetch_existing_timeout(self, mock_client_cls):
        """Returns None on timeout."""
        from src.thinktank.transcription.existing import fetch_existing_transcript

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=httpx.TimeoutException("timed out"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        result = await fetch_existing_transcript(
            content_url="https://show.com/episodes/ep-slow",
            transcript_url_pattern="https://show.com/transcripts/{slug}",
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_fetch_existing_follows_redirects(self):
        """Client follows 302 redirects to the final transcript URL.

        Regression: httpx.AsyncClient defaults to follow_redirects=False, which
        caused redirect responses (common for CDN-backed transcripts) to be
        treated as failures. Verify via httpx.MockTransport that a 302 is
        transparently followed and the final body is returned.
        """
        from src.thinktank.transcription import existing as existing_mod

        def _handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/ep-42"):
                return httpx.Response(
                    302,
                    headers={"Location": "https://cdn.show.com/transcripts/ep-42-final"},
                )
            return httpx.Response(
                200,
                text="<html><body><p>Redirected body.</p></body></html>",
            )

        transport = httpx.MockTransport(_handler)
        real_async_client = httpx.AsyncClient

        def _client_with_transport(*args, **kwargs):
            # Preserve the follow_redirects arg under test; inject our transport
            kwargs.setdefault("transport", transport)
            kwargs["transport"] = transport
            return real_async_client(*args, **kwargs)

        with patch.object(existing_mod.httpx, "AsyncClient", side_effect=_client_with_transport):
            result = await existing_mod.fetch_existing_transcript(
                content_url="https://show.com/episodes/ep-42",
                transcript_url_pattern="https://show.com/transcripts/{slug}",
            )

        assert result is not None
        assert "Redirected body." in result
