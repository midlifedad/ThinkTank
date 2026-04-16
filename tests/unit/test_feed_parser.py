"""Unit tests for RSS feed parser wrapper.

Uses inline XML strings for unit tests. Integration fixture files
created in Plan 02.
"""

from datetime import datetime

import pytest

from thinktank.ingestion.feed_parser import FeedEntry, parse_feed

# Minimal valid RSS feed with one episode
BASIC_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
  <channel>
    <title>Test Podcast</title>
    <item>
      <title>Episode 1: Introduction</title>
      <link>https://example.com/ep1</link>
      <pubDate>Mon, 15 Jan 2025 12:00:00 GMT</pubDate>
      <itunes:duration>01:30:00</itunes:duration>
      <description>The first episode of our show.</description>
    </item>
  </channel>
</rss>"""

# Feed with itunes:duration in multiple formats
ITUNES_DURATION_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
  <channel>
    <title>Duration Test</title>
    <item>
      <title>HMS Format</title>
      <link>https://example.com/ep1</link>
      <itunes:duration>01:30:00</itunes:duration>
    </item>
    <item>
      <title>MS Format</title>
      <link>https://example.com/ep2</link>
      <itunes:duration>90:00</itunes:duration>
    </item>
    <item>
      <title>Seconds Format</title>
      <link>https://example.com/ep3</link>
      <itunes:duration>5400</itunes:duration>
    </item>
  </channel>
</rss>"""

# Feed with enclosure URL
ENCLOSURE_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
  <channel>
    <title>Enclosure Test</title>
    <item>
      <title>Episode with Enclosure</title>
      <link>https://example.com/ep1</link>
      <enclosure url="https://cdn.example.com/ep1.mp3" length="12345678" type="audio/mpeg"/>
    </item>
  </channel>
</rss>"""

# Feed with no duration
NO_DURATION_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
  <channel>
    <title>No Duration</title>
    <item>
      <title>No Duration Episode</title>
      <link>https://example.com/ep1</link>
    </item>
  </channel>
</rss>"""

# Empty feed (valid XML, no items)
EMPTY_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Empty Feed</title>
  </channel>
</rss>"""

# Truly broken XML
BROKEN_XML = """<?xml version="1.0"?>
<rss><not-closed"""


class TestBasicParsing:
    def test_parse_basic_feed(self):
        entries = parse_feed(BASIC_RSS)
        assert len(entries) == 1
        entry = entries[0]
        assert isinstance(entry, FeedEntry)
        assert entry.title == "Episode 1: Introduction"
        assert entry.url == "https://example.com/ep1"
        assert entry.duration_seconds == 5400
        assert entry.show_name == "Test Podcast"
        assert entry.description == "The first episode of our show."

    def test_parse_published_date(self):
        entries = parse_feed(BASIC_RSS)
        entry = entries[0]
        assert isinstance(entry.published_at, datetime)
        # After DATA-REVIEW H4 / migration 007 the parser returns aware UTC.
        assert entry.published_at.tzinfo is not None
        assert entry.published_at.utcoffset().total_seconds() == 0
        assert entry.published_at.year == 2025
        assert entry.published_at.month == 1
        assert entry.published_at.day == 15


class TestDurationParsing:
    def test_parse_itunes_duration(self):
        entries = parse_feed(ITUNES_DURATION_RSS)
        assert len(entries) == 3
        # All three formats should parse to 5400 seconds
        assert entries[0].duration_seconds == 5400  # HH:MM:SS
        assert entries[1].duration_seconds == 5400  # MM:SS
        assert entries[2].duration_seconds == 5400  # raw seconds


class TestEnclosure:
    def test_parse_enclosure_url(self):
        entries = parse_feed(ENCLOSURE_RSS)
        assert len(entries) == 1
        assert entries[0].url == "https://cdn.example.com/ep1.mp3"


class TestEdgeCases:
    def test_parse_no_duration(self):
        entries = parse_feed(NO_DURATION_RSS)
        assert len(entries) == 1
        assert entries[0].duration_seconds is None

    def test_empty_feed(self):
        entries = parse_feed(EMPTY_RSS)
        assert entries == []

    def test_bozo_feed_raises(self):
        with pytest.raises(ValueError, match="[Ff]eed"):
            parse_feed(BROKEN_XML)


class TestShowName:
    def test_show_name_extracted(self):
        entries = parse_feed(BASIC_RSS)
        assert entries[0].show_name == "Test Podcast"
