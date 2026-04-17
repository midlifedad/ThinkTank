"""HTTP response helpers that honor upstream rate-limit signals.

Third-party APIs (Podcast Index, YouTube caption CDN, Apple transcript
hosts) signal rate limiting via a 429 status + a Retry-After header.
Our default httpx.raise_for_status treats that as a generic HTTPStatusError
and the worker then schedules the job with our own exponential backoff —
which can retry sooner than the upstream asked us to, burning quota.

`raise_for_status_with_backoff` parses the Retry-After header and raises
`RateLimitedError` carrying `retry_after_seconds`. The worker's failure
path (queue/claim.fail_job) honors that hint when scheduling the retry,
respecting the upstream's requested delay.

Source: INTEGRATIONS-REVIEW M-02.
"""

from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import httpx

# Ceiling so a misbehaving upstream (e.g. Retry-After: 86400) can't stall
# a worker past our operational SLO. Callers can still retry on the next
# tick if the server keeps sending 429s.
_MAX_RETRY_AFTER_SECONDS = 300


class RateLimitedError(Exception):
    """Raised when an upstream API returns HTTP 429.

    Attributes:
        retry_after_seconds: Hint from the Retry-After header, clamped to
            [0, _MAX_RETRY_AFTER_SECONDS]. None if no header was present.
        url: The URL that was rate-limited (for logs).
    """

    def __init__(
        self,
        message: str,
        retry_after_seconds: int | None,
        url: str | None = None,
    ) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds
        self.url = url


def _parse_retry_after(raw: str | None) -> int | None:
    """Parse a Retry-After header value into seconds.

    RFC 7231 allows two forms:
      - Integer seconds: "120"
      - HTTP-date: "Wed, 21 Oct 2015 07:28:00 GMT"

    Returns None if the header is missing or unparseable.
    The value is clamped to [0, _MAX_RETRY_AFTER_SECONDS].
    """
    if raw is None:
        return None

    raw = raw.strip()
    if not raw:
        return None

    # Form 1: plain integer
    try:
        seconds = int(raw)
    except ValueError:
        seconds = None

    if seconds is None:
        # Form 2: HTTP-date
        try:
            when = parsedate_to_datetime(raw)
        except (TypeError, ValueError):
            return None
        if when is None:
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        seconds = int((when - now).total_seconds())

    if seconds < 0:
        return 0
    if seconds > _MAX_RETRY_AFTER_SECONDS:
        return _MAX_RETRY_AFTER_SECONDS
    return seconds


def raise_for_status_with_backoff(response: httpx.Response) -> None:
    """Drop-in replacement for response.raise_for_status().

    On 429, parses Retry-After and raises RateLimitedError.
    On any other 4xx/5xx, raises httpx.HTTPStatusError (same as the default).
    On 2xx/3xx, returns silently.
    """
    if response.status_code == 429:
        retry_after = _parse_retry_after(response.headers.get("Retry-After"))
        raise RateLimitedError(
            f"HTTP 429 rate-limited by upstream ({response.request.url})",
            retry_after_seconds=retry_after,
            url=str(response.request.url),
        )
    response.raise_for_status()
