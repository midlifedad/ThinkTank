"""Content fingerprinting via SHA-256 per spec Section 5.5 Layer 2.

Pure function -- no I/O, no database, no async.

fingerprint = sha256(
    normalize_title(title)
    || date_trunc('day', published_at)
    || bucket_duration(duration_seconds)
)

Returns None if title is empty/None/whitespace-only (no fingerprint possible).

DATA-REVIEW H2 robustness rules:
* Title normalization collapses runs of whitespace to a single space, strips
  leading/trailing whitespace, and case-folds to lowercase. This ensures
  transcode-stage re-encoding artefacts (e.g. stray tabs/newlines in titles
  scraped from feeds) still fingerprint identically.
* Duration is bucketed to the nearest 10-second boundary so that transcode
  jitter (3601s vs 3605s vs 3609s on the same episode) still produces the
  same fingerprint. Different true durations (e.g. 3600s vs 3615s) still
  fingerprint differently.
"""

import hashlib
import re
from datetime import datetime

# 10-second bucket granularity. Duration values are rounded to the *nearest*
# multiple (half-up) before being folded into the hash payload. Round-to-
# nearest (rather than floor) is chosen so that values straddling a bucket
# boundary from either side still collapse together: e.g. 7195 and 7204 both
# represent "about 7200 seconds" and should fingerprint identically. Floor
# would split them into 7190/7200.
_DURATION_BUCKET_SECONDS = 10
_DURATION_BUCKET_HALF = _DURATION_BUCKET_SECONDS // 2

_WHITESPACE_RUN = re.compile(r"\s+")


def _normalize_title(title: str) -> str:
    """Collapse whitespace runs, strip, and lowercase."""
    return _WHITESPACE_RUN.sub(" ", title).strip().lower()


def compute_fingerprint(
    title: str | None,
    published_at: datetime | None,
    duration_seconds: int | None,
) -> str | None:
    """Compute content fingerprint for deduplication.

    Args:
        title: Content title. If empty, None, or whitespace-only, returns None.
        published_at: Publication date. Only the date portion is used.
        duration_seconds: Content duration in seconds. None treated as 0.
            Bucketed to the nearest 10s.

    Returns:
        SHA-256 hex digest string, or None if title is empty after
        whitespace normalization.
    """
    if not title:
        return None

    normalized_title = _normalize_title(title)
    if not normalized_title:
        # Title was all whitespace.
        return None

    date_str = published_at.strftime("%Y-%m-%d") if published_at else ""

    # Bucket duration to nearest 10s boundary (round half up, no floats).
    raw_duration = duration_seconds or 0
    bucketed = (
        (raw_duration + _DURATION_BUCKET_HALF) // _DURATION_BUCKET_SECONDS
    ) * _DURATION_BUCKET_SECONDS
    duration = str(bucketed)

    payload = f"{normalized_title}{date_str}{duration}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
