"""URL normalization to canonical form per spec Section 5.5 Layer 1.

Pure function -- no I/O, no database, no async.

Normalization rules:
1. Strip podcast-tracker wrappers (chartable.com/track/*, op3.dev/e/*,
   pdst.fm/e/*) before any other processing  -- DATA-REVIEW H1
2. Force https://
3. Strip www., normalize m./music.youtube.com -> youtube.com
4. Drop URL fragment (#t=... deep links are never dedup-relevant)
5. Strip tracking parameters (utm_*, ref, fbclid, gclid)
6. YouTube: extract video ID, canonicalize to https://youtube.com/watch?v={id}
7. Strip trailing slash
8. Lowercase netloc (preserve path case)
9. Sort remaining query params for deterministic output

The function is idempotent: ``normalize_url(normalize_url(x)) == normalize_url(x)``
for every input. Migration 008 relies on this to safely renormalize
existing rows.
"""

import re
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

# Tracking parameters to strip (spec Section 5.5)
_TRACKING_PARAMS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "ref",
    "fbclid",
    "gclid",
}

_YOUTUBE_VIDEO_RE = re.compile(r"(?:youtube\.com/watch\?.*v=|youtu\.be/|youtube\.com/embed/)([a-zA-Z0-9_-]{11})")

# Podcast tracker prefixes. Each entry matches a scheme+host+path prefix and
# strips it, leaving the inner URL. The inner URL may itself be
# scheme-qualified (e.g. "...track/ABC/https://traffic.libsyn.com/ep.mp3") or
# scheme-less ("...track/ABC/traffic.libsyn.com/ep.mp3") -- both handled by
# _strip_tracker_prefix.
_TRACKER_PREFIX_RE = re.compile(
    r"^https?://(?:www\.)?(?:"
    r"chartable\.com/track/[^/]+/"
    r"|op3\.dev/e/"
    r"|pdst\.fm/e/"
    r")",
    flags=re.IGNORECASE,
)

# YouTube host variants that should all collapse to plain "youtube.com".
_YOUTUBE_HOST_VARIANTS = {"m.youtube.com", "music.youtube.com"}


def _strip_tracker_prefix(url: str) -> str:
    """Strip a podcast ad-tracker wrapper if present.

    Trackers wrap the real episode URL behind a redirect:
        https://chartable.com/track/ABC/traffic.libsyn.com/ep.mp3
    The real URL is the suffix after the tracker's path. We re-qualify
    a scheme if the inner URL was stored scheme-less.
    """
    match = _TRACKER_PREFIX_RE.match(url)
    if not match:
        return url
    remainder = url[match.end() :]
    # Re-add scheme if the wrapped URL was stored scheme-less.
    if not remainder.startswith(("http://", "https://")):
        remainder = "https://" + remainder
    # Recurse: a tracker may wrap another tracker.
    return _strip_tracker_prefix(remainder)


def normalize_url(url: str) -> str:
    """Normalize a URL to canonical form. Idempotent.

    Args:
        url: Any HTTP/HTTPS URL.

    Returns:
        Canonical URL string.
    """
    # Step 1: Strip tracker prefix BEFORE any other parsing. After this
    # the URL refers to the actual target resource.
    url = _strip_tracker_prefix(url)

    parsed = urlparse(url)

    # Force HTTPS
    scheme = "https"

    # Lowercase netloc, strip www., collapse YouTube host variants.
    netloc = parsed.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    if netloc in _YOUTUBE_HOST_VARIANTS:
        netloc = "youtube.com"

    # YouTube canonicalization -- must check against the (possibly rewritten)
    # URL so m./music. variants are caught after host normalization.
    rewritten = urlunparse((scheme, netloc, parsed.path, parsed.params, parsed.query, ""))
    yt_match = _YOUTUBE_VIDEO_RE.search(rewritten)
    if yt_match:
        return f"https://youtube.com/watch?v={yt_match.group(1)}"

    # Strip tracking params, sort remaining
    query_params = parse_qs(parsed.query, keep_blank_values=False)
    filtered = {k: v for k, v in query_params.items() if k.lower() not in _TRACKING_PARAMS}
    # Sort params for deterministic output
    new_query = urlencode(sorted(filtered.items()), doseq=True)

    # Strip trailing slash from path
    path = parsed.path.rstrip("/")

    # Drop fragment unconditionally (empty 6th tuple element).
    return urlunparse((scheme, netloc, path, "", new_query, ""))
