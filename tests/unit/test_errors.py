"""Unit tests for ErrorCategory enum and categorize_error().

Tests the closed set of 17 error categories and the exception-to-category
mapping function.
"""

from enum import StrEnum

import pytest

from src.thinktank.queue.errors import ErrorCategory, categorize_error


class TestErrorCategoryEnum:
    """ErrorCategory must be a StrEnum with exactly 17 members."""

    def test_is_str_enum(self):
        assert issubclass(ErrorCategory, StrEnum)

    def test_has_exactly_17_members(self):
        assert len(ErrorCategory) == 17

    def test_all_expected_members_exist(self):
        expected = [
            "rss_parse",
            "http_timeout",
            "http_error",
            "rate_limited",
            "youtube_rate_limit",
            "api_error",
            "transcription_failed",
            "audio_download_failed",
            "audio_conversion_failed",
            "llm_api_error",
            "llm_timeout",
            "llm_parse_error",
            "worker_timeout",
            "database_error",
            "payload_invalid",
            "handler_not_found",
            "unknown",
        ]
        actual_values = [member.value for member in ErrorCategory]
        assert sorted(actual_values) == sorted(expected)

    def test_string_comparison(self):
        """StrEnum members compare equal to their string value."""
        assert ErrorCategory.HTTP_TIMEOUT == "http_timeout"
        assert ErrorCategory.UNKNOWN == "unknown"

    def test_member_values_are_lowercase_snake_case(self):
        for member in ErrorCategory:
            assert member.value == member.value.lower()
            assert " " not in member.value


class TestCategorizeError:
    """categorize_error maps exception types to ErrorCategory values."""

    def test_connection_error(self):
        assert categorize_error(ConnectionError("refused")) == ErrorCategory.HTTP_ERROR

    def test_timeout_error(self):
        assert categorize_error(TimeoutError("timed out")) == ErrorCategory.HTTP_TIMEOUT

    def test_value_error(self):
        assert categorize_error(ValueError("bad payload")) == ErrorCategory.PAYLOAD_INVALID

    def test_key_error(self):
        assert categorize_error(KeyError("missing_field")) == ErrorCategory.PAYLOAD_INVALID

    def test_unknown_exception(self):
        """Unrecognized exception types map to 'unknown'."""
        assert categorize_error(RuntimeError("something")) == ErrorCategory.UNKNOWN

    def test_os_error_maps_to_http_error(self):
        """OSError (parent of ConnectionError) maps to http_error."""
        assert categorize_error(OSError("network issue")) == ErrorCategory.HTTP_ERROR

    def test_returns_error_category_type(self):
        result = categorize_error(Exception("anything"))
        assert isinstance(result, ErrorCategory)
