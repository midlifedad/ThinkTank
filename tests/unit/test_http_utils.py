"""Unit tests for http_utils.raise_for_status_with_backoff.

Verifies that 429 responses raise RateLimitedError with the Retry-After
hint parsed correctly, and that non-429 errors still raise HTTPStatusError.

Source: INTEGRATIONS-REVIEW M-02.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import httpx
import pytest

from thinktank.http_utils import (
    _MAX_RETRY_AFTER_SECONDS,
    RateLimitedError,
    _parse_retry_after,
    raise_for_status_with_backoff,
)


def _make_response(status_code: int, headers: dict[str, str] | None = None) -> httpx.Response:
    """Build an httpx.Response tied to a request for raise_for_status to work."""
    request = httpx.Request("GET", "https://example.com/api")
    return httpx.Response(
        status_code,
        headers=headers or {},
        request=request,
        content=b"",
    )


class TestParseRetryAfter:
    def test_returns_none_when_header_missing(self) -> None:
        assert _parse_retry_after(None) is None

    def test_returns_none_for_blank_header(self) -> None:
        assert _parse_retry_after("") is None
        assert _parse_retry_after("   ") is None

    def test_parses_integer_seconds(self) -> None:
        assert _parse_retry_after("42") == 42

    def test_strips_whitespace(self) -> None:
        assert _parse_retry_after("  7  ") == 7

    def test_clamps_negative_to_zero(self) -> None:
        assert _parse_retry_after("-5") == 0

    def test_clamps_above_max(self) -> None:
        assert _parse_retry_after(str(_MAX_RETRY_AFTER_SECONDS + 999)) == _MAX_RETRY_AFTER_SECONDS

    def test_parses_http_date(self) -> None:
        future = datetime.now(UTC) + timedelta(seconds=60)
        header = format_datetime(future, usegmt=True)
        parsed = _parse_retry_after(header)
        # Allow a few seconds of drift from the now() in _parse_retry_after.
        assert parsed is not None
        assert 55 <= parsed <= 65

    def test_past_http_date_clamps_to_zero(self) -> None:
        past = datetime.now(UTC) - timedelta(hours=1)
        header = format_datetime(past, usegmt=True)
        assert _parse_retry_after(header) == 0

    def test_garbage_returns_none(self) -> None:
        assert _parse_retry_after("not-a-number-or-date") is None


class TestRaiseForStatusWithBackoff:
    def test_ok_response_returns_silently(self) -> None:
        raise_for_status_with_backoff(_make_response(200))

    def test_404_raises_httpstatuserror_not_ratelimited(self) -> None:
        with pytest.raises(httpx.HTTPStatusError):
            raise_for_status_with_backoff(_make_response(404))

    def test_500_raises_httpstatuserror_not_ratelimited(self) -> None:
        with pytest.raises(httpx.HTTPStatusError):
            raise_for_status_with_backoff(_make_response(500))

    def test_429_raises_rate_limited_error_with_hint(self) -> None:
        response = _make_response(429, headers={"Retry-After": "30"})
        with pytest.raises(RateLimitedError) as exc_info:
            raise_for_status_with_backoff(response)
        assert exc_info.value.retry_after_seconds == 30
        assert exc_info.value.url == "https://example.com/api"

    def test_429_without_retry_after_header_has_none_hint(self) -> None:
        with pytest.raises(RateLimitedError) as exc_info:
            raise_for_status_with_backoff(_make_response(429))
        assert exc_info.value.retry_after_seconds is None

    def test_429_with_garbage_retry_after_has_none_hint(self) -> None:
        response = _make_response(429, headers={"Retry-After": "garbage"})
        with pytest.raises(RateLimitedError) as exc_info:
            raise_for_status_with_backoff(response)
        assert exc_info.value.retry_after_seconds is None


class TestCategorizeError:
    """RateLimitedError must map to ErrorCategory.RATE_LIMITED."""

    def test_rate_limited_error_categorized(self) -> None:
        from thinktank.queue.errors import ErrorCategory, categorize_error

        exc = RateLimitedError("throttled", retry_after_seconds=10)
        assert categorize_error(exc) == ErrorCategory.RATE_LIMITED
