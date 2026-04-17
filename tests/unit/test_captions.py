"""Unit tests for YouTube caption extraction (Pass 1).

Spec reference: Section 7.1.
All yt-dlp and httpx calls are mocked -- no external I/O.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# --- Helpers ---


def _vtt_content(word_count: int) -> str:
    """Generate a VTT string with approximately `word_count` words.

    Each cue has unique text to avoid deduplication reducing the count.
    """
    header = "WEBVTT\n\n"
    # Generate unique words so dedup doesn't collapse cues
    words = [f"word{i}" for i in range(word_count)]
    # Split into cues of ~10 words each
    cues = []
    for idx, i in enumerate(range(0, len(words), 10)):
        chunk = " ".join(words[i : i + 10])
        start = f"00:{idx // 60:02d}:{idx % 60:02d}.000"
        end_idx = idx + 1
        end = f"00:{end_idx // 60:02d}:{end_idx % 60:02d}.000"
        cues.append(f"{start} --> {end}\n{chunk}\n")
    return header + "\n".join(cues)


def _vtt_with_duplicates() -> str:
    """Generate VTT with overlapping cues that repeat text lines."""
    return (
        "WEBVTT\n\n"
        "00:00:00.000 --> 00:00:05.000\n"
        "Hello world this is a test\n\n"
        "00:00:03.000 --> 00:00:08.000\n"
        "Hello world this is a test\n\n"
        "00:00:06.000 --> 00:00:12.000\n"
        "of the caption extraction system\n\n"
        "00:00:10.000 --> 00:00:15.000\n"
        "of the caption extraction system\n\n"
        "00:00:13.000 --> 00:00:18.000\n"
        "which handles duplicate lines\n"
    )


def _make_vtt_response(vtt: str) -> MagicMock:
    """Build a mock httpx.Response with text + no-op raise_for_status."""
    response = MagicMock()
    response.text = vtt
    response.status_code = 200
    response.headers = {}
    response.raise_for_status = MagicMock()
    return response


def _patch_async_client(mock_httpx, response):
    """Wire mock_httpx.AsyncClient as an async context manager with mocked .get()."""
    client = MagicMock()
    client.get = AsyncMock(return_value=response)
    mock_httpx.AsyncClient.return_value.__aenter__ = AsyncMock(return_value=client)
    mock_httpx.AsyncClient.return_value.__aexit__ = AsyncMock(return_value=False)
    return client


# --- Tests ---


class TestExtractYoutubeCaptions:
    """Test suite for extract_youtube_captions."""

    @pytest.mark.asyncio
    @patch("thinktank.transcription.captions.httpx")
    @patch("thinktank.transcription.captions.YoutubeDL")
    async def test_extract_captions_success(self, mock_ydl_cls, mock_httpx):
        """Successful caption extraction returns plain text >= 100 words."""
        from thinktank.transcription.captions import extract_youtube_captions

        mock_ydl = MagicMock()
        mock_ydl_cls.return_value.__enter__ = MagicMock(return_value=mock_ydl)
        mock_ydl_cls.return_value.__exit__ = MagicMock(return_value=False)
        mock_ydl.extract_info.return_value = {
            "requested_subtitles": {"en": {"url": "https://example.com/subs.vtt", "ext": "vtt"}}
        }

        vtt = _vtt_content(150)
        _patch_async_client(mock_httpx, _make_vtt_response(vtt))

        result = await extract_youtube_captions("https://youtube.com/watch?v=test123")

        assert result is not None
        assert len(result.split()) >= 100

    @pytest.mark.asyncio
    @patch("thinktank.transcription.captions.YoutubeDL")
    async def test_extract_captions_no_subs(self, mock_ydl_cls):
        """Returns None when no subtitles are available."""
        from thinktank.transcription.captions import extract_youtube_captions

        mock_ydl = MagicMock()
        mock_ydl_cls.return_value.__enter__ = MagicMock(return_value=mock_ydl)
        mock_ydl_cls.return_value.__exit__ = MagicMock(return_value=False)
        mock_ydl.extract_info.return_value = {"requested_subtitles": {}}

        result = await extract_youtube_captions("https://youtube.com/watch?v=nosubs")

        assert result is None

    @pytest.mark.asyncio
    @patch("thinktank.transcription.captions.httpx")
    @patch("thinktank.transcription.captions.YoutubeDL")
    async def test_extract_captions_too_short(self, mock_ydl_cls, mock_httpx):
        """Returns None when captions have fewer than 100 words."""
        from thinktank.transcription.captions import extract_youtube_captions

        mock_ydl = MagicMock()
        mock_ydl_cls.return_value.__enter__ = MagicMock(return_value=mock_ydl)
        mock_ydl_cls.return_value.__exit__ = MagicMock(return_value=False)
        mock_ydl.extract_info.return_value = {
            "requested_subtitles": {"en": {"url": "https://example.com/short.vtt", "ext": "vtt"}}
        }

        vtt = _vtt_content(50)
        _patch_async_client(mock_httpx, _make_vtt_response(vtt))

        result = await extract_youtube_captions("https://youtube.com/watch?v=short")

        assert result is None

    @pytest.mark.asyncio
    @patch("thinktank.transcription.captions.YoutubeDL")
    async def test_extract_captions_exception_returns_none(self, mock_ydl_cls):
        """Returns None on any exception (fail-safe per spec 7.1)."""
        from thinktank.transcription.captions import extract_youtube_captions

        mock_ydl = MagicMock()
        mock_ydl_cls.return_value.__enter__ = MagicMock(return_value=mock_ydl)
        mock_ydl_cls.return_value.__exit__ = MagicMock(return_value=False)
        mock_ydl.extract_info.side_effect = RuntimeError("yt-dlp exploded")

        result = await extract_youtube_captions("https://youtube.com/watch?v=boom")

        assert result is None

    def test_vtt_parsing_strips_timing_and_deduplicates(self):
        """VTT content with duplicate lines yields 3 unique lines."""
        from thinktank.transcription.captions import _parse_vtt_text

        parsed = _parse_vtt_text(_vtt_with_duplicates())
        lines = parsed.split("\n")

        assert len(lines) == 3
        assert "Hello world this is a test" in lines
        assert "of the caption extraction system" in lines
        assert "which handles duplicate lines" in lines
