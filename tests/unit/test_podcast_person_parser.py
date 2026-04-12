"""Unit tests for podcast:person XML tag extraction.

Pure logic tests -- no database, no async.
"""

from pathlib import Path

from src.thinktank.ingestion.podcast_person_parser import extract_podcast_persons

FIXTURES = Path(__file__).parent.parent / "fixtures" / "rss"


class TestExtractPodcastPersons:
    """Tests for extract_podcast_persons function."""

    def test_extract_persons_with_tags(self):
        """Fixture has 3 items: ep-001 (2 persons), ep-002 (1 person), ep-003 (0 persons)."""
        xml = (FIXTURES / "podcast_person.xml").read_text()
        result = extract_podcast_persons(xml)

        assert "ep-001" in result
        assert len(result["ep-001"]) == 2

        assert "ep-002" in result
        assert len(result["ep-002"]) == 1

        # ep-003 has no podcast:person tags -- should not appear in result
        assert "ep-003" not in result

    def test_extract_persons_names_and_roles(self):
        """Verify specific name and role extraction for ep-001 guest."""
        xml = (FIXTURES / "podcast_person.xml").read_text()
        result = extract_podcast_persons(xml)

        persons_ep1 = result["ep-001"]
        guest = [p for p in persons_ep1 if p["name"] == "Sam Harris"]
        assert len(guest) == 1
        assert guest[0]["role"] == "guest"
        assert guest[0]["group"] == "cast"
        assert guest[0]["href"] == "https://example.com/guest"

        host = [p for p in persons_ep1 if p["name"] == "Joe Rogan"]
        assert len(host) == 1
        assert host[0]["role"] == "host"

    def test_extract_persons_no_namespace(self):
        """Feed with no podcast namespace returns empty dict."""
        xml = """<?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0">
          <channel>
            <item>
              <title>No Namespace Episode</title>
              <guid>ep-100</guid>
            </item>
          </channel>
        </rss>"""
        result = extract_podcast_persons(xml)
        assert result == {}

    def test_extract_persons_empty_xml(self):
        """Empty/minimal RSS returns empty dict."""
        result = extract_podcast_persons("")
        assert result == {}

        result = extract_podcast_persons("   ")
        assert result == {}

        # Minimal valid RSS but no items
        xml = """<?xml version="1.0"?><rss version="2.0"><channel></channel></rss>"""
        result = extract_podcast_persons(xml)
        assert result == {}

    def test_extract_persons_oversized_xml(self):
        """XML > 10MB returns empty dict."""
        # Create XML string just over 10MB
        oversized = "x" * (10_000_001)
        result = extract_podcast_persons(oversized)
        assert result == {}

    def test_extract_persons_default_role_and_group(self):
        """Person tags without role/group attributes default to host/cast."""
        xml = """<?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0" xmlns:podcast="https://podcastindex.org/namespace/1.0">
          <channel>
            <item>
              <guid>ep-defaults</guid>
              <title>Defaults Test</title>
              <podcast:person>Default Person</podcast:person>
            </item>
          </channel>
        </rss>"""
        result = extract_podcast_persons(xml)
        assert "ep-defaults" in result
        person = result["ep-defaults"][0]
        assert person["name"] == "Default Person"
        assert person["role"] == "host"
        assert person["group"] == "cast"
        assert person["href"] is None
        assert person["img"] is None

    def test_extract_persons_invalid_xml(self):
        """Broken XML returns empty dict (graceful failure)."""
        result = extract_podcast_persons("<rss><channel><item><not-closed>")
        assert result == {}
