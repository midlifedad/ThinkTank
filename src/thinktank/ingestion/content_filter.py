"""Content filtering logic per spec Section 5.7.

Pure functions -- no I/O, no database, no async.

Duration filter: skip episodes shorter than min_duration.
Title filter: skip episodes matching skip title patterns (case-insensitive substring).
"""


def should_skip_by_duration(
    duration_seconds: int | None,
    min_duration: int,
) -> bool:
    """Return True if episode should be skipped due to short duration.

    Episodes with no duration are NOT skipped (conservative -- assume long-form).

    Args:
        duration_seconds: Episode duration in seconds. None means unknown.
        min_duration: Minimum duration threshold in seconds.

    Returns:
        True if episode should be skipped.
    """
    if duration_seconds is None:
        return False
    return duration_seconds < min_duration


def should_skip_by_title(
    title: str,
    skip_patterns: list[str],
) -> bool:
    """Return True if title matches any skip pattern (case-insensitive).

    Uses substring matching per spec Section 5.7.

    Args:
        title: Episode title.
        skip_patterns: List of substrings to match against.

    Returns:
        True if title matches any pattern.
    """
    title_lower = title.lower()
    return any(pattern.lower() in title_lower for pattern in skip_patterns)
