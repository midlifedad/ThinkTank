"""Unit tests for YouTube caption extraction (Pass 1).

Spec reference: Section 7.1.
All yt-dlp and httpx calls are mocked -- no external I/O.
"""

from unittest.mock import MagicMock, patch

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


# --- Tests ---


class TestExtractYoutubeCaptions:
    """Test suite for extract_youtube_captions."""

    @patch("thinktank.transcription.captions.httpx")
    @patch("thinktank.transcription.captions.YoutubeDL")
    def test_extract_captions_success(self, mock_ydl_cls, mock_httpx):
        """Successful caption extraction returns plain text >= 100 words."""
        from thinktank.transcription.captions import extract_youtube_captions

        # Mock yt-dlp
        mock_ydl = MagicMock()
        mock_ydl_cls.return_value.__enter__ = MagicMock(return_value=mock_ydl)
        mock_ydl_cls.return_value.__exit__ = MagicMock(return_value=False)
        mock_ydl.extract_info.return_value = {
            "requested_subtitles": {"en": {"url": "https://example.com/subs.vtt", "ext": "vtt"}}
        }

        # Mock httpx.get to return VTT content
        vtt = _vtt_content(150)
        mock_response = MagicMock()
        mock_response.text = vtt
        mock_response.raise_for_status = MagicMock()
        mock_httpx.get.return_value = mock_response

        result = extract_youtube_captions("https://youtube.com/watch?v=test123")

        assert result is not None
        assert len(result.split()) >= 100

    @patch("thinktank.transcription.captions.YoutubeDL")
    def test_extract_captions_no_subs(self, mock_ydl_cls):
        """Returns None when no subtitles are available."""
        from thinktank.transcription.captions import extract_youtube_captions

        mock_ydl = MagicMock()
        mock_ydl_cls.return_value.__enter__ = MagicMock(return_value=mock_ydl)
        mock_ydl_cls.return_value.__exit__ = MagicMock(return_value=False)
        mock_ydl.extract_info.return_value = {"requested_subtitles": {}}

        result = extract_youtube_captions("https://youtube.com/watch?v=nosubs")

        assert result is None

    @patch("thinktank.transcription.captions.httpx")
    @patch("thinktank.transcription.captions.YoutubeDL")
    def test_extract_captions_too_short(self, mock_ydl_cls, mock_httpx):
        """Returns None when captions have fewer than 100 words."""
        from thinktank.transcription.captions import extract_youtube_captions

        mock_ydl = MagicMock()
        mock_ydl_cls.return_value.__enter__ = MagicMock(return_value=mock_ydl)
        mock_ydl_cls.return_value.__exit__ = MagicMock(return_value=False)
        mock_ydl.extract_info.return_value = {
            "requested_subtitles": {"en": {"url": "https://example.com/short.vtt", "ext": "vtt"}}
        }

        vtt = _vtt_content(50)
        mock_response = MagicMock()
        mock_response.text = vtt
        mock_response.raise_for_status = MagicMock()
        mock_httpx.get.return_value = mock_response

        result = extract_youtube_captions("https://youtube.com/watch?v=short")

        assert result is None

    @patch("thinktank.transcription.captions.YoutubeDL")
    def test_extract_captions_exception_returns_none(self, mock_ydl_cls):
        """Returns None on any exception (fail-safe per spec 7.1)."""
        from thinktank.transcription.captions import extract_youtube_captions

        mock_ydl = MagicMock()
        mock_ydl_cls.return_value.__enter__ = MagicMock(return_value=mock_ydl)
        mock_ydl_cls.return_value.__exit__ = MagicMock(return_value=False)
        mock_ydl.extract_info.side_effect = RuntimeError("yt-dlp exploded")

        result = extract_youtube_captions("https://youtube.com/watch?v=boom")

        assert result is None

    @patch("thinktank.transcription.captions.httpx")
    @patch("thinktank.transcription.captions.YoutubeDL")
    def test_vtt_parsing_strips_timing_and_deduplicates(self, mock_ydl_cls, mock_httpx):
        """VTT content with timestamps and duplicate lines yields clean text."""

        mock_ydl = MagicMock()
        mock_ydl_cls.return_value.__enter__ = MagicMock(return_value=mock_ydl)
        mock_ydl_cls.return_value.__exit__ = MagicMock(return_value=False)
        mock_ydl.extract_info.return_value = {
            "requested_subtitles": {"en": {"url": "https://example.com/dups.vtt", "ext": "vtt"}}
        }

        # VTT with 3 unique lines (will be < 100 words, so returns None -- that's expected)
        # We test the dedup logic by checking the parse function directly
        vtt = _vtt_with_duplicates()
        mock_response = MagicMock()
        mock_response.text = vtt
        mock_response.raise_for_status = MagicMock()
        mock_httpx.get.return_value = mock_response

        # Since < 100 words, extract_youtube_captions returns None
        # But we verify the internal VTT parsing via a direct call
        from thinktank.transcription.captions import _parse_vtt_text

        parsed = _parse_vtt_text(vtt)
        lines = parsed.split("\n")

        # Should have 3 unique lines, not 5
        assert len(lines) == 3
        assert "Hello world this is a test" in lines
        assert "of the caption extraction system" in lines
        assert "which handles duplicate lines" in lines
