"""Content fingerprinting via SHA-256 per spec Section 5.5 Layer 2.

Pure function -- no I/O, no database, no async.

fingerprint = sha256(lowercase(title) || date_trunc('day', published_at) || coalesce(duration_seconds, 0))
Returns None if title is empty/None (no fingerprint possible).
"""

import hashlib
from datetime import datetime


def compute_fingerprint(
    title: str | None,
    published_at: datetime | None,
    duration_seconds: int | None,
) -> str | None:
    """Compute content fingerprint for deduplication.

    Args:
        title: Content title. If empty or None, returns None.
        published_at: Publication date (timezone-naive). Only date portion used.
        duration_seconds: Content duration in seconds. None treated as 0.

    Returns:
        SHA-256 hex digest string, or None if title is empty/None.
    """
    if not title:
        return None

    date_str = published_at.strftime("%Y-%m-%d") if published_at else ""
    duration = str(duration_seconds or 0)

    payload = f"{title.lower()}{date_str}{duration}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
