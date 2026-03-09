"""Unit tests for duration string parsing.

Tests all formats from itunes:duration: HH:MM:SS, MM:SS, raw seconds.
"""

from src.thinktank.ingestion.duration import parse_duration


class TestValidFormats:
    def test_hms_format(self):
        assert parse_duration("01:30:00") == 5400

    def test_ms_format(self):
        assert parse_duration("90:00") == 5400

    def test_raw_seconds(self):
        assert parse_duration("5400") == 5400

    def test_short_format(self):
        assert parse_duration("3:00") == 180


class TestEdgeCases:
    def test_none_returns_none(self):
        assert parse_duration(None) is None

    def test_invalid_returns_none(self):
        assert parse_duration("abc") is None

    def test_whitespace_stripped(self):
        assert parse_duration("  01:30:00  ") == 5400

    def test_empty_string_returns_none(self):
        assert parse_duration("") is None
