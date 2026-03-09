"""Existing transcript fetch (Pass 2).

Spec reference: Section 7.2.
Fetches existing transcripts from a URL derived from content URL + pattern.
Returns plain text or None on failure.
"""

import re
from urllib.parse import urlparse

import httpx
import structlog

logger = structlog.get_logger(__name__)


def _derive_transcript_url(content_url: str, pattern: str) -> str:
    """Derive transcript URL from content URL using the pattern.

    The pattern is a format string where {slug} is replaced with the
    last path segment of the content URL (e.g., episode slug).

    Args:
        content_url: Original content URL.
        pattern: URL pattern with {slug} placeholder.

    Returns:
        The derived transcript URL.
    """
    parsed = urlparse(content_url)
    # Extract the last non-empty path segment as the slug
    path_parts = [p for p in parsed.path.strip("/").split("/") if p]
    slug = path_parts[-1] if path_parts else ""
    return pattern.format(slug=slug, episode_id=slug)


def _strip_html(html: str) -> str:
    """Strip HTML tags to extract plain text.

    Args:
        html: Raw HTML content.

    Returns:
        Plain text with HTML tags removed.
    """
    return re.sub(r"<[^>]+>", "", html).strip()


async def fetch_existing_transcript(
    content_url: str,
    transcript_url_pattern: str | None,
) -> str | None:
    """Fetch an existing transcript from a derived URL.

    Args:
        content_url: The original content URL.
        transcript_url_pattern: URL pattern with {slug} placeholder.
            If None, returns None immediately.

    Returns:
        Plain text transcript or None on failure.
    """
    if not transcript_url_pattern:
        return None

    try:
        transcript_url = _derive_transcript_url(content_url, transcript_url_pattern)
        logger.info(
            "fetching_existing_transcript",
            content_url=content_url,
            transcript_url=transcript_url,
        )

        async with httpx.AsyncClient() as client:
            response = await client.get(transcript_url, timeout=30.0)
            response.raise_for_status()

        text = _strip_html(response.text)
        if text:
            logger.info(
                "existing_transcript_found",
                content_url=content_url,
                word_count=len(text.split()),
            )
            return text

        logger.info("existing_transcript_empty", content_url=content_url)
        return None

    except Exception:
        logger.warning(
            "existing_transcript_fetch_failed",
            content_url=content_url,
            exc_info=True,
        )
        return None
