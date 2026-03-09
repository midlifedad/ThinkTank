"""URL normalization to canonical form per spec Section 5.5 Layer 1.

Pure function -- no I/O, no database, no async.

Normalization rules:
1. Force https://
2. Strip www.
3. Strip tracking parameters (utm_*, ref, fbclid, gclid)
4. YouTube: extract video ID, canonicalize to https://youtube.com/watch?v={id}
5. Strip trailing slash
6. Lowercase netloc (preserve path case)
7. Sort remaining query params for deterministic output
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

_YOUTUBE_VIDEO_RE = re.compile(
    r"(?:youtube\.com/watch\?.*v=|youtu\.be/|youtube\.com/embed/)([a-zA-Z0-9_-]{11})"
)


def normalize_url(url: str) -> str:
    """Normalize a URL to canonical form.

    Args:
        url: Any HTTP/HTTPS URL.

    Returns:
        Canonical URL string.
    """
    parsed = urlparse(url)

    # Force HTTPS
    scheme = "https"

    # Lowercase netloc, strip www.
    netloc = parsed.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]

    # YouTube canonicalization -- must check against original URL
    # to handle youtu.be and embed formats
    yt_match = _YOUTUBE_VIDEO_RE.search(url)
    if yt_match:
        return f"https://youtube.com/watch?v={yt_match.group(1)}"

    # Strip tracking params, sort remaining
    query_params = parse_qs(parsed.query, keep_blank_values=False)
    filtered = {
        k: v
        for k, v in query_params.items()
        if k.lower() not in _TRACKING_PARAMS
    }
    # Sort params for deterministic output
    new_query = urlencode(sorted(filtered.items()), doseq=True)

    # Strip trailing slash from path
    path = parsed.path.rstrip("/")

    return urlunparse((scheme, netloc, path, "", new_query, ""))
