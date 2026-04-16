"""Unit tests for retry logic: calculate_backoff, should_retry, get_max_attempts.

Pure functions with no I/O -- fast unit tests.
"""

from datetime import timedelta

import pytest

from thinktank.queue.retry import (
    MAX_ATTEMPTS_BY_TYPE,
    calculate_backoff,
    get_max_attempts,
    should_retry,
)


class TestCalculateBackoff:
    """calculate_backoff returns 2^attempts minutes, capped at 60."""

    def test_attempt_0(self):
        assert calculate_backoff(0) == timedelta(minutes=1)

    def test_attempt_1(self):
        assert calculate_backoff(1) == timedelta(minutes=2)

    def test_attempt_2(self):
        assert calculate_backoff(2) == timedelta(minutes=4)

    def test_attempt_3(self):
        assert calculate_backoff(3) == timedelta(minutes=8)

    def test_attempt_4(self):
        assert calculate_backoff(4) == timedelta(minutes=16)

    def test_attempt_5(self):
        assert calculate_backoff(5) == timedelta(minutes=32)

    def test_cap_at_60_minutes(self):
        """Backoff must not exceed 60 minutes."""
        assert calculate_backoff(6) == timedelta(minutes=60)

    def test_cap_at_60_minutes_high_attempt(self):
        assert calculate_backoff(10) == timedelta(minutes=60)

    def test_returns_timedelta(self):
        result = calculate_backoff(1)
        assert isinstance(result, timedelta)


class TestShouldRetry:
    """should_retry returns True when attempts < max_attempts."""

    def test_zero_attempts_can_retry(self):
        assert should_retry(0, 3) is True

    def test_one_attempt_can_retry(self):
        assert should_retry(1, 3) is True

    def test_two_attempts_can_retry(self):
        assert should_retry(2, 3) is True

    def test_at_max_cannot_retry(self):
        assert should_retry(3, 3) is False

    def test_over_max_cannot_retry(self):
        assert should_retry(4, 3) is False

    def test_max_attempts_one(self):
        assert should_retry(0, 1) is True
        assert should_retry(1, 1) is False


class TestGetMaxAttempts:
    """get_max_attempts returns per-type limits or default of 3."""

    def test_process_content_returns_2(self):
        assert get_max_attempts("process_content") == 2

    def test_fetch_podcast_feed_returns_4(self):
        assert get_max_attempts("fetch_podcast_feed") == 4

    def test_fetch_youtube_channel_returns_4(self):
        assert get_max_attempts("fetch_youtube_channel") == 4

    def test_scrape_substack_returns_4(self):
        assert get_max_attempts("scrape_substack") == 4

    def test_fetch_guest_feed_returns_4(self):
        assert get_max_attempts("fetch_guest_feed") == 4

    def test_unknown_type_returns_default_3(self):
        assert get_max_attempts("some_unknown_type") == 3

    def test_discover_thinker_returns_default_3(self):
        assert get_max_attempts("discover_thinker") == 3

    def test_max_attempts_dict_exists(self):
        assert isinstance(MAX_ATTEMPTS_BY_TYPE, dict)
        assert len(MAX_ATTEMPTS_BY_TYPE) > 0
