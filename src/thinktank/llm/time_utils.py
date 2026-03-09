"""Time computation utilities for scheduled LLM tasks.

Provides functions to calculate seconds until specific UTC hours
and specific weekday+hour targets. Used by the worker loop's
LLM digest and audit schedulers to avoid time drift.

Spec reference: Section 8.2 (scheduled check cadence).
"""

from datetime import UTC, datetime, timedelta


def _utc_now() -> datetime:
    """Return current UTC time as timezone-aware datetime.

    Separated for testability -- tests patch this to freeze time.
    """
    return datetime.now(UTC)


def seconds_until_next_utc_hour(target_hour: int) -> float:
    """Compute seconds until the next occurrence of target_hour UTC.

    If the current time is at or past target_hour today, returns
    the time until target_hour tomorrow.

    Args:
        target_hour: Hour of day in UTC (0-23).

    Returns:
        Seconds until next target_hour as a positive float.
    """
    now = _utc_now()
    target = now.replace(hour=target_hour, minute=0, second=0, microsecond=0)

    if target <= now:
        target += timedelta(days=1)

    return (target - now).total_seconds()


def seconds_until_next_monday_utc(target_hour: int) -> float:
    """Compute seconds until next Monday at target_hour UTC.

    If today is Monday and we haven't passed target_hour yet,
    returns seconds until target_hour today. Otherwise, returns
    seconds until the following Monday at target_hour.

    Args:
        target_hour: Hour of day in UTC (0-23).

    Returns:
        Seconds until next Monday at target_hour as a positive float.
    """
    now = _utc_now()
    # weekday(): Monday=0, Tuesday=1, ..., Sunday=6
    days_ahead = (0 - now.weekday()) % 7  # days until next Monday

    target = now.replace(hour=target_hour, minute=0, second=0, microsecond=0)
    target += timedelta(days=days_ahead)

    if target <= now:
        target += timedelta(weeks=1)

    return (target - now).total_seconds()
