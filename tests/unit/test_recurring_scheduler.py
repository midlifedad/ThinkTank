"""Unit tests for the recurring-task executor's due-decision logic.

Source: ARCH-REVIEW 2026-05-28 (A1). The executor must honor exactly the
schedule semantics the Phase 11 admin UI writes: frequency_hours, enabled,
next_run_at (ISO strings in JSONB).
"""

from datetime import UTC, datetime, timedelta

from thinktank.worker.recurring import _is_due, _parse_iso

NOW = datetime(2026, 5, 28, 12, 0, 0, tzinfo=UTC)


class TestParseIso:
    def test_valid_aware_string(self):
        assert _parse_iso("2026-05-28T12:00:00+00:00") == NOW

    def test_naive_string_assumed_utc(self):
        parsed = _parse_iso("2026-05-28T12:00:00")
        assert parsed == NOW
        assert parsed.tzinfo is not None

    def test_invalid_string_returns_none(self):
        assert _parse_iso("not-a-date") is None

    def test_non_string_returns_none(self):
        assert _parse_iso(None) is None
        assert _parse_iso(12345) is None


class TestIsDue:
    def test_missing_config_is_due_immediately(self):
        """No scheduler_<key> row yet: default-enabled, runs on first tick."""
        assert _is_due(None, NOW) is True

    def test_non_dict_config_is_due(self):
        """Malformed JSONB (raw string/int) falls back to due."""
        assert _is_due("garbage", NOW) is True

    def test_disabled_task_never_due(self):
        config = {"enabled": False, "next_run_at": (NOW - timedelta(hours=5)).isoformat()}
        assert _is_due(config, NOW) is False

    def test_future_next_run_not_due(self):
        config = {"enabled": True, "next_run_at": (NOW + timedelta(hours=1)).isoformat()}
        assert _is_due(config, NOW) is False

    def test_past_next_run_is_due(self):
        config = {"enabled": True, "next_run_at": (NOW - timedelta(minutes=1)).isoformat()}
        assert _is_due(config, NOW) is True

    def test_exactly_now_is_due(self):
        config = {"enabled": True, "next_run_at": NOW.isoformat()}
        assert _is_due(config, NOW) is True

    def test_missing_next_run_is_due(self):
        """Config saved by the UI before any run: next_run_at absent -> due."""
        assert _is_due({"enabled": True, "frequency_hours": 4}, NOW) is True

    def test_invalid_next_run_is_due(self):
        assert _is_due({"enabled": True, "next_run_at": "garbage"}, NOW) is True
