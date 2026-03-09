"""Unit tests for content filtering logic.

Tests duration-based and title-pattern-based skip rules per spec Section 5.7.
"""

from src.thinktank.ingestion.content_filter import (
    should_skip_by_duration,
    should_skip_by_title,
)


class TestDurationFilter:
    def test_skip_short_episode(self):
        assert should_skip_by_duration(300, 600) is True

    def test_keep_long_episode(self):
        assert should_skip_by_duration(3600, 600) is False

    def test_none_duration_not_skipped(self):
        """Episodes with unknown duration are NOT skipped (conservative)."""
        assert should_skip_by_duration(None, 600) is False

    def test_exact_min_duration_not_skipped(self):
        assert should_skip_by_duration(600, 600) is False


class TestTitleFilter:
    def test_skip_trailer_title(self):
        assert should_skip_by_title("Season 2 Trailer", ["trailer"]) is True

    def test_skip_best_of_title(self):
        assert should_skip_by_title("Best of 2025", ["best of"]) is True

    def test_case_insensitive_skip(self):
        assert should_skip_by_title("TRAILER Episode", ["trailer"]) is True

    def test_no_match_keeps(self):
        assert should_skip_by_title("Great Interview", ["trailer", "teaser"]) is False

    def test_empty_patterns_keeps(self):
        assert should_skip_by_title("Any Title Here", []) is False
