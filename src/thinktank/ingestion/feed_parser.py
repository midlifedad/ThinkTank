"""RSS/Atom feed parsing wrapper around feedparser.

Pure function (no async, no DB). Calls parse_duration() for itunes_duration.
Converts published_parsed to timezone-naive datetime.
Extracts URL from enclosure href if present, falls back to entry.link.
Raises ValueError for truly broken feeds (SAXParseException bozo).
Ignores benign bozo exceptions (CharacterEncodingOverride).
"""

from dataclasses import dataclass
from datetime import datetime
from xml.sax import SAXParseException

import feedparser

from thinktank.ingestion.duration import parse_duration


@dataclass
class FeedEntry:
    """Structured episode data extracted from an RSS feed entry."""

    title: str
    url: str
    published_at: datetime | None
    duration_seconds: int | None
    show_name: str | None
    description: str | None


def parse_feed(xml_content: str) -> list[FeedEntry]:
    """Parse RSS/Atom feed XML into structured FeedEntry objects.

    Args:
        xml_content: Raw XML string of the feed.

    Returns:
        List of FeedEntry dataclasses.

    Raises:
        ValueError: If the feed XML is truly broken (SAXParseException).
    """
    feed = feedparser.parse(xml_content)

    # Check for truly broken feeds
    if feed.bozo:
        exc = feed.bozo_exception
        if isinstance(exc, SAXParseException):
            raise ValueError(f"Feed XML is broken: {exc}")
        # Benign bozo exceptions (e.g. CharacterEncodingOverride) are ignored

    show_name = feed.feed.get("title")

    entries: list[FeedEntry] = []
    for entry in feed.entries:
        title = entry.get("title", "")

        # URL: prefer enclosure href, fall back to link
        url = entry.get("link", "")
        enclosures = entry.get("enclosures", [])
        if enclosures:
            url = enclosures[0].get("href", url)

        # Published date: convert time.struct_time to timezone-naive datetime
        published_at: datetime | None = None
        if entry.get("published_parsed"):
            try:
                published_at = datetime(*entry.published_parsed[:6])
            except (TypeError, ValueError):
                published_at = None

        # Duration: parse itunes_duration
        raw_duration = entry.get("itunes_duration")
        duration_seconds = parse_duration(raw_duration)

        # Description
        description = entry.get("summary") or entry.get("description")

        entries.append(
            FeedEntry(
                title=title,
                url=url,
                published_at=published_at,
                duration_seconds=duration_seconds,
                show_name=show_name,
                description=description,
            )
        )

    return entries
