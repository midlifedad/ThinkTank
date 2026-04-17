"""YouTube caption extraction (Pass 1).

Spec reference: Section 7.1.
Uses yt-dlp Python API to extract auto-generated or manual captions.
Captions with fewer than 100 words are rejected (returns None).
Entire function is fail-safe: any exception returns None.
"""

import asyncio
import io

import httpx
import structlog
import webvtt
from yt_dlp import YoutubeDL

from thinktank.http_utils import raise_for_status_with_backoff

logger = structlog.get_logger(__name__)

# Minimum word count for captions to be considered valid (spec 7.1)
_MIN_WORD_COUNT = 100


def _parse_vtt_text(vtt_content: str) -> str:
    """Parse VTT content to plain text, removing duplicates from overlapping cues.

    Args:
        vtt_content: Raw WebVTT subtitle content.

    Returns:
        Plain text with duplicate lines removed, joined by newlines.
    """
    buf = io.StringIO(vtt_content)
    vtt_parsed = webvtt.from_buffer(buf)
    seen: list[str] = []

    for caption in vtt_parsed:
        text = caption.text.strip()
        if text and text not in seen:
            seen.append(text)

    return "\n".join(seen)


def _ytdlp_extract(video_url: str) -> dict | None:
    """Sync yt-dlp helper, isolated so the async wrapper can `to_thread` it."""
    opts = {
        "writeautomaticsub": True,
        "writesubtitles": True,
        "subtitleslangs": ["en"],
        "skip_download": True,
        "quiet": True,
        "no_warnings": True,
    }
    with YoutubeDL(opts) as ydl:
        return ydl.extract_info(video_url, download=False)


async def extract_youtube_captions(video_url: str) -> str | None:
    """Extract YouTube captions via yt-dlp.

    Returns plain text transcript or None if:
    - No captions available
    - Captions have fewer than 100 words
    - Any error occurs (fail-safe)

    yt-dlp is sync + blocking (makes its own HTTP calls), so it runs via
    `asyncio.to_thread`. The subtitle VTT fetch uses `httpx.AsyncClient`
    so nothing blocks the event loop. INTEGRATIONS L-03.

    Args:
        video_url: YouTube video URL.

    Returns:
        Plain text transcript or None.
    """
    try:
        info = await asyncio.to_thread(_ytdlp_extract, video_url)
        if info is None:
            return None

        subs = info.get("requested_subtitles") or {}
        en_sub = subs.get("en")
        if not en_sub:
            logger.info("captions_not_available", url=video_url)
            return None

        sub_url = en_sub.get("url")
        if not sub_url:
            logger.info("captions_no_url", url=video_url)
            return None

        async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
            response = await client.get(sub_url)
        raise_for_status_with_backoff(response)

        text = _parse_vtt_text(response.text)

        if len(text.split()) >= _MIN_WORD_COUNT:
            logger.info(
                "captions_extracted",
                url=video_url,
                word_count=len(text.split()),
            )
            return text

        logger.info(
            "captions_too_short",
            url=video_url,
            word_count=len(text.split()),
            threshold=_MIN_WORD_COUNT,
        )
        return None

    except Exception:
        logger.warning("captions_extraction_failed", url=video_url, exc_info=True)
        return None
