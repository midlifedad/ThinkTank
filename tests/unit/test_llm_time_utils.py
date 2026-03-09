"""Unit tests for LLM time utilities.

Tests seconds_until_next_utc_hour and seconds_until_next_monday_utc
with frozen time to verify correct computation across various
days and times.
"""

from datetime import UTC, datetime
from unittest.mock import patch

from src.thinktank.llm.time_utils import (
    seconds_until_next_monday_utc,
    seconds_until_next_utc_hour,
)


class TestSecondsUntilNextUtcHour:
    """Tests for seconds_until_next_utc_hour."""

    def test_before_target_hour_same_day(self):
        """When current time is before target hour, returns seconds until that hour today."""
        # 2026-03-09 03:30:00 UTC (Monday) -> target 07:00 = 3.5 hours away
        fake_now = datetime(2026, 3, 9, 3, 30, 0, tzinfo=UTC)
        with patch("src.thinktank.llm.time_utils._utc_now", return_value=fake_now):
            result = seconds_until_next_utc_hour(7)
        assert result == 3.5 * 3600  # 12600 seconds

    def test_after_target_hour_wraps_to_next_day(self):
        """When current time is after target hour, returns seconds until next day."""
        # 2026-03-09 08:00:00 UTC -> target 07:00 = 23 hours away
        fake_now = datetime(2026, 3, 9, 8, 0, 0, tzinfo=UTC)
        with patch("src.thinktank.llm.time_utils._utc_now", return_value=fake_now):
            result = seconds_until_next_utc_hour(7)
        assert result == 23 * 3600  # 82800 seconds

    def test_exactly_at_target_hour_wraps_to_next_day(self):
        """When current time is exactly at target hour, returns 24 hours."""
        # 2026-03-09 07:00:00 UTC -> target 07:00 = 24 hours away
        fake_now = datetime(2026, 3, 9, 7, 0, 0, tzinfo=UTC)
        with patch("src.thinktank.llm.time_utils._utc_now", return_value=fake_now):
            result = seconds_until_next_utc_hour(7)
        assert result == 24 * 3600  # 86400 seconds

    def test_minutes_before_target(self):
        """When current time is minutes before target, returns correct seconds."""
        # 2026-03-09 06:45:00 UTC -> target 07:00 = 15 minutes away
        fake_now = datetime(2026, 3, 9, 6, 45, 0, tzinfo=UTC)
        with patch("src.thinktank.llm.time_utils._utc_now", return_value=fake_now):
            result = seconds_until_next_utc_hour(7)
        assert result == 15 * 60  # 900 seconds

    def test_midnight_target(self):
        """Target hour 0 (midnight) works correctly."""
        # 2026-03-09 23:00:00 UTC -> target 00:00 = 1 hour away
        fake_now = datetime(2026, 3, 9, 23, 0, 0, tzinfo=UTC)
        with patch("src.thinktank.llm.time_utils._utc_now", return_value=fake_now):
            result = seconds_until_next_utc_hour(0)
        assert result == 1 * 3600  # 3600 seconds

    def test_result_is_positive_float(self):
        """Return value is always a positive float."""
        fake_now = datetime(2026, 3, 9, 12, 0, 0, tzinfo=UTC)
        with patch("src.thinktank.llm.time_utils._utc_now", return_value=fake_now):
            result = seconds_until_next_utc_hour(7)
        assert isinstance(result, float)
        assert result > 0


class TestSecondsUntilNextMondayUtc:
    """Tests for seconds_until_next_monday_utc."""

    def test_tuesday_to_monday(self):
        """From Tuesday, returns seconds until next Monday at target hour."""
        # 2026-03-10 12:00:00 UTC (Tuesday) -> next Monday 2026-03-16 07:00
        fake_now = datetime(2026, 3, 10, 12, 0, 0, tzinfo=UTC)
        with patch("src.thinktank.llm.time_utils._utc_now", return_value=fake_now):
            result = seconds_until_next_monday_utc(7)
        # 6 days minus 5 hours = 5 days 19 hours = 5*86400 + 19*3600
        expected = 5 * 86400 + 19 * 3600
        assert result == expected

    def test_monday_before_target_hour(self):
        """On Monday before target hour, returns seconds until that hour today."""
        # 2026-03-09 03:00:00 UTC (Monday) -> Monday 07:00 = 4 hours away
        fake_now = datetime(2026, 3, 9, 3, 0, 0, tzinfo=UTC)
        with patch("src.thinktank.llm.time_utils._utc_now", return_value=fake_now):
            result = seconds_until_next_monday_utc(7)
        assert result == 4 * 3600

    def test_monday_after_target_hour_wraps_to_next_week(self):
        """On Monday after target hour, returns 7 days until next Monday."""
        # 2026-03-09 08:00:00 UTC (Monday) -> next Monday 07:00 = 6d 23h
        fake_now = datetime(2026, 3, 9, 8, 0, 0, tzinfo=UTC)
        with patch("src.thinktank.llm.time_utils._utc_now", return_value=fake_now):
            result = seconds_until_next_monday_utc(7)
        expected = 6 * 86400 + 23 * 3600
        assert result == expected

    def test_monday_exactly_at_target_wraps_to_next_week(self):
        """On Monday at exactly target hour, returns 7 full days."""
        # 2026-03-09 07:00:00 UTC (Monday) -> next Monday 07:00 = 7 days
        fake_now = datetime(2026, 3, 9, 7, 0, 0, tzinfo=UTC)
        with patch("src.thinktank.llm.time_utils._utc_now", return_value=fake_now):
            result = seconds_until_next_monday_utc(7)
        assert result == 7 * 86400

    def test_sunday_to_monday(self):
        """From Sunday, returns seconds until Monday at target hour."""
        # 2026-03-15 20:00:00 UTC (Sunday) -> Monday 07:00 = 11 hours
        fake_now = datetime(2026, 3, 15, 20, 0, 0, tzinfo=UTC)
        with patch("src.thinktank.llm.time_utils._utc_now", return_value=fake_now):
            result = seconds_until_next_monday_utc(7)
        assert result == 11 * 3600

    def test_friday_to_monday(self):
        """From Friday, returns seconds until Monday at target hour."""
        # 2026-03-13 10:00:00 UTC (Friday) -> Monday 07:00 = 2d 21h
        fake_now = datetime(2026, 3, 13, 10, 0, 0, tzinfo=UTC)
        with patch("src.thinktank.llm.time_utils._utc_now", return_value=fake_now):
            result = seconds_until_next_monday_utc(7)
        expected = 2 * 86400 + 21 * 3600
        assert result == expected

    def test_result_is_positive_float(self):
        """Return value is always a positive float."""
        fake_now = datetime(2026, 3, 11, 12, 0, 0, tzinfo=UTC)
        with patch("src.thinktank.llm.time_utils._utc_now", return_value=fake_now):
            result = seconds_until_next_monday_utc(7)
        assert isinstance(result, float)
        assert result > 0
