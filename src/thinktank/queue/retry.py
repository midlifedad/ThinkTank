"""Retry logic: exponential backoff and per-type max attempts.

Spec reference: Section 6.2 (retry policy).
Backoff formula: min(2^attempts, 60) minutes.
"""

from datetime import timedelta

# Per-type max attempt overrides.
# Default is 3 for any job type not listed here.
MAX_ATTEMPTS_BY_TYPE: dict[str, int] = {
    "process_content": 2,
    "fetch_podcast_feed": 4,
    "fetch_youtube_channel": 4,
    "scrape_substack": 4,
    "fetch_guest_feed": 4,
}

_DEFAULT_MAX_ATTEMPTS = 3


def get_max_attempts(job_type: str) -> int:
    """Return the max retry attempts for a given job type.

    Returns the per-type limit from MAX_ATTEMPTS_BY_TYPE,
    or the default of 3 for unlisted types.
    """
    return MAX_ATTEMPTS_BY_TYPE.get(job_type, _DEFAULT_MAX_ATTEMPTS)


def calculate_backoff(attempts: int) -> timedelta:
    """Calculate exponential backoff delay for retry scheduling.

    Returns timedelta of min(2^attempts, 60) minutes.
    Examples: attempt 0 -> 1min, 1 -> 2min, 2 -> 4min, 3 -> 8min.
    Capped at 60 minutes.
    """
    minutes = min(2**attempts, 60)
    return timedelta(minutes=minutes)


def should_retry(attempts: int, max_attempts: int) -> bool:
    """Determine whether a job should be retried.

    Returns True when attempts < max_attempts, False otherwise.
    """
    return attempts < max_attempts
