"""Unit tests for ErrorCategory enum and categorize_error().

Tests the closed set of 19 error categories and the exception-to-category
mapping function, including anthropic SDK and httpx exception handling.
"""

from enum import StrEnum

import anthropic
import pydantic
import pytest
from thinktank.queue.errors import ErrorCategory, categorize_error


class TestErrorCategoryEnum:
    """ErrorCategory must be a StrEnum with exactly 18 members."""

    def test_is_str_enum(self):
        assert issubclass(ErrorCategory, StrEnum)

    def test_has_exactly_18_members(self):
        assert len(ErrorCategory) == 18

    def test_all_expected_members_exist(self):
        expected = [
            "rss_parse",
            "http_timeout",
            "http_error",
            "rate_limited",
            "youtube_rate_limit",
            "api_error",
            "podcastindex_error",
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


class TestCategorizeErrorAnthropic:
    """categorize_error handles anthropic SDK exceptions correctly."""

    def test_rate_limit_error_returns_llm_api_error(self):
        """anthropic.RateLimitError maps to LLM_API_ERROR."""
        exc = anthropic.RateLimitError(
            message="Rate limited",
            response=_mock_httpx_response(429),
            body=None,
        )
        assert categorize_error(exc) == ErrorCategory.LLM_API_ERROR

    def test_api_connection_error_returns_llm_timeout(self):
        """anthropic.APIConnectionError maps to LLM_TIMEOUT."""
        exc = anthropic.APIConnectionError(request=_mock_httpx_request())
        assert categorize_error(exc) == ErrorCategory.LLM_TIMEOUT

    def test_api_timeout_error_returns_llm_timeout(self):
        """anthropic.APITimeoutError maps to LLM_TIMEOUT."""
        exc = anthropic.APITimeoutError(request=_mock_httpx_request())
        assert categorize_error(exc) == ErrorCategory.LLM_TIMEOUT

    def test_api_status_error_returns_llm_api_error(self):
        """anthropic.APIStatusError maps to LLM_API_ERROR."""
        exc = anthropic.APIStatusError(
            message="Server error",
            response=_mock_httpx_response(500),
            body=None,
        )
        assert categorize_error(exc) == ErrorCategory.LLM_API_ERROR

    def test_pydantic_validation_error_returns_llm_parse_error(self):
        """pydantic.ValidationError maps to LLM_PARSE_ERROR."""
        from thinktank.llm.schemas import ThinkerApprovalResponse

        try:
            ThinkerApprovalResponse.model_validate({"bad_field": 123})
        except pydantic.ValidationError as e:
            assert categorize_error(e) == ErrorCategory.LLM_PARSE_ERROR
        else:
            pytest.fail("Expected pydantic.ValidationError was not raised")

    def test_existing_behavior_unchanged_for_timeout_error(self):
        """Existing TimeoutError still maps to HTTP_TIMEOUT."""
        assert categorize_error(TimeoutError("timed out")) == ErrorCategory.HTTP_TIMEOUT

    def test_existing_behavior_unchanged_for_connection_error(self):
        """Existing ConnectionError still maps to HTTP_ERROR."""
        assert categorize_error(ConnectionError("refused")) == ErrorCategory.HTTP_ERROR

    def test_existing_behavior_unchanged_for_value_error(self):
        """Existing ValueError still maps to PAYLOAD_INVALID."""
        assert categorize_error(ValueError("bad")) == ErrorCategory.PAYLOAD_INVALID


class TestCategorizeErrorHttpx:
    """categorize_error handles httpx.HTTPStatusError exceptions."""

    def test_httpx_429_returns_rate_limited(self):
        """httpx.HTTPStatusError with 429 maps to RATE_LIMITED."""
        import httpx

        response = httpx.Response(
            status_code=429,
            request=httpx.Request("GET", "https://api.example.com/search"),
        )
        exc = httpx.HTTPStatusError(
            message="429 Too Many Requests",
            request=response.request,
            response=response,
        )
        assert categorize_error(exc) == ErrorCategory.RATE_LIMITED

    def test_httpx_500_returns_http_error(self):
        """httpx.HTTPStatusError with non-429 maps to HTTP_ERROR."""
        import httpx

        response = httpx.Response(
            status_code=500,
            request=httpx.Request("GET", "https://api.example.com/search"),
        )
        exc = httpx.HTTPStatusError(
            message="500 Server Error",
            request=response.request,
            response=response,
        )
        assert categorize_error(exc) == ErrorCategory.HTTP_ERROR

    def test_httpx_403_returns_http_error(self):
        """httpx.HTTPStatusError with 403 maps to HTTP_ERROR."""
        import httpx

        response = httpx.Response(
            status_code=403,
            request=httpx.Request("GET", "https://api.example.com/search"),
        )
        exc = httpx.HTTPStatusError(
            message="403 Forbidden",
            request=response.request,
            response=response,
        )
        assert categorize_error(exc) == ErrorCategory.HTTP_ERROR

    def test_podcastindex_error_member_exists(self):
        """PODCASTINDEX_ERROR member exists in ErrorCategory."""
        assert ErrorCategory.PODCASTINDEX_ERROR == "podcastindex_error"


class TestCategorizeErrorDatabase:
    """categorize_error handles SQLAlchemy + asyncpg integrity/connection errors.

    DATA-REVIEW finding: when an ingestion job raced another job and tripped
    the ``canonical_url`` or ``content_fingerprint`` unique constraints, the
    resulting IntegrityError bubbled up as ``ErrorCategory.UNKNOWN``, which
    triggered the fallback "something is broken, alert humans" retry path.
    Unique-constraint violations are expected at the dedupe layer -- they
    should classify as ``DATABASE_ERROR`` so the ingest pipeline retries
    idempotently instead of paging.
    """

    def test_sqlalchemy_integrity_error_maps_to_database_error(self):
        """SQLAlchemy IntegrityError (unique violation) -> DATABASE_ERROR."""
        from sqlalchemy.exc import IntegrityError

        # The statement/params are irrelevant for classification; orig=None
        # is valid per the SQLAlchemy API for synthetic errors.
        exc = IntegrityError(
            statement="INSERT INTO content ...",
            params=None,
            orig=Exception("duplicate key value violates unique constraint"),
        )
        assert categorize_error(exc) == ErrorCategory.DATABASE_ERROR

    def test_asyncpg_unique_violation_maps_to_database_error(self):
        """Raw asyncpg.UniqueViolationError -> DATABASE_ERROR."""
        from asyncpg.exceptions import UniqueViolationError

        exc = UniqueViolationError("duplicate key value violates unique constraint")
        assert categorize_error(exc) == ErrorCategory.DATABASE_ERROR

    def test_sqlalchemy_operational_error_maps_to_database_error(self):
        """SQLAlchemy OperationalError (connection drop, etc.) -> DATABASE_ERROR."""
        from sqlalchemy.exc import OperationalError

        exc = OperationalError(
            statement="SELECT 1",
            params=None,
            orig=Exception("server closed the connection unexpectedly"),
        )
        assert categorize_error(exc) == ErrorCategory.DATABASE_ERROR


def _mock_httpx_response(status_code: int):
    """Create a minimal mock httpx response for anthropic exceptions."""
    import httpx

    return httpx.Response(
        status_code=status_code,
        request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"),
    )


def _mock_httpx_request():
    """Create a minimal mock httpx request for anthropic exceptions."""
    import httpx

    return httpx.Request("POST", "https://api.anthropic.com/v1/messages")
