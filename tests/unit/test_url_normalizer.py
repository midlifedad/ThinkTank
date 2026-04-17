"""Unit tests for URL normalization pure logic.

Tests spec Section 5.5 Layer 1 requirements: force HTTPS, strip www,
strip tracking params, YouTube canonicalization, trailing slash removal,
lowercase netloc, deterministic query param ordering.

DATA-REVIEW H1 extensions: strip podcast-tracker URL prefixes
(chartable.com/track/..., op3.dev/e/..., pdst.fm/e/...), drop URL
fragments, and canonicalize m./music.youtube.com -> youtube.com.
"""

import pytest

from thinktank.ingestion.url_normalizer import normalize_url


class TestForceHttps:
    def test_force_https(self):
        assert normalize_url("http://example.com") == "https://example.com"

    def test_already_https(self):
        assert normalize_url("https://example.com") == "https://example.com"


class TestStripWww:
    def test_strip_www(self):
        assert normalize_url("https://www.example.com/path") == "https://example.com/path"


class TestStripTrackingParams:
    def test_strip_utm_params(self):
        url = "https://example.com/page?utm_source=twitter&utm_medium=social&utm_campaign=launch&keep=1"
        result = normalize_url(url)
        assert "utm_source" not in result
        assert "utm_medium" not in result
        assert "utm_campaign" not in result
        assert "keep=1" in result

    def test_strip_fbclid_gclid(self):
        url = "https://example.com/page?fbclid=abc123&gclid=def456&ref=home&real=param"
        result = normalize_url(url)
        assert "fbclid" not in result
        assert "gclid" not in result
        assert "ref" not in result
        assert "real=param" in result

    def test_keep_non_tracking_params(self):
        url = "https://example.com/search?q=hello&page=2"
        result = normalize_url(url)
        assert "q=hello" in result
        assert "page=2" in result


class TestYouTubeCanonicalization:
    def test_youtube_video_watch(self):
        url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ&utm_source=x"
        assert normalize_url(url) == "https://youtube.com/watch?v=dQw4w9WgXcQ"

    def test_youtube_short_url(self):
        url = "https://youtu.be/dQw4w9WgXcQ"
        assert normalize_url(url) == "https://youtube.com/watch?v=dQw4w9WgXcQ"

    def test_youtube_embed(self):
        url = "https://youtube.com/embed/dQw4w9WgXcQ"
        assert normalize_url(url) == "https://youtube.com/watch?v=dQw4w9WgXcQ"


class TestMiscNormalization:
    def test_strip_trailing_slash(self):
        assert normalize_url("https://example.com/path/") == "https://example.com/path"

    def test_lowercase_netloc(self):
        result = normalize_url("HTTPS://Example.COM/Path")
        assert result == "https://example.com/Path"

    def test_sort_remaining_query_params(self):
        url = "https://example.com/page?z=1&a=2&m=3"
        result = normalize_url(url)
        # Params should be in sorted order for deterministic output
        assert result == "https://example.com/page?a=2&m=3&z=1"


# DATA-REVIEW H1: Podcast tracker prefixes, fragments, YouTube host
# canonicalization.


class TestTrackerPrefixes:
    """Podcast ad-networks wrap the real audio URL behind a tracker prefix.

    Deduplication misses when the same episode is discovered via two
    different trackers, so we strip the prefix before canonicalizing.
    """

    @pytest.mark.parametrize(
        "raw, expected",
        [
            (
                "https://chartable.com/track/ABC/traffic.libsyn.com/ep.mp3",
                "https://traffic.libsyn.com/ep.mp3",
            ),
            (
                "https://op3.dev/e/feeds.example.com/ep.mp3",
                "https://feeds.example.com/ep.mp3",
            ),
            (
                "https://pdst.fm/e/traffic.simplecastaudio.com/ep.mp3",
                "https://traffic.simplecastaudio.com/ep.mp3",
            ),
            # Scheme-qualified inner URLs (some trackers keep http:// or https://
            # prefix on the wrapped URL).
            (
                "https://chartable.com/track/ABC/https://traffic.libsyn.com/ep.mp3",
                "https://traffic.libsyn.com/ep.mp3",
            ),
            # Tracker prefix with trailing query params on the wrapped URL
            (
                "https://op3.dev/e/feeds.example.com/ep.mp3?ref=home",
                "https://feeds.example.com/ep.mp3",
            ),
        ],
    )
    def test_strip_tracker_prefixes(self, raw, expected):
        assert normalize_url(raw) == expected


class TestDropFragment:
    """Deep-link fragments (`#t=42`) are never relevant for dedup."""

    def test_drops_timestamp_fragment(self):
        assert normalize_url("https://example.com/ep.mp3#t=10") == "https://example.com/ep.mp3"

    def test_drops_fragment_with_query(self):
        assert normalize_url("https://example.com/page?x=1#section") == "https://example.com/page?x=1"


class TestYouTubeHostCanonicalization:
    """m.youtube.com and music.youtube.com collapse to youtube.com."""

    @pytest.mark.parametrize(
        "raw, expected",
        [
            (
                "https://m.youtube.com/watch?v=dQw4w9WgXcQ",
                "https://youtube.com/watch?v=dQw4w9WgXcQ",
            ),
            (
                "https://music.youtube.com/watch?v=dQw4w9WgXcQ",
                "https://youtube.com/watch?v=dQw4w9WgXcQ",
            ),
        ],
    )
    def test_canonicalizes_mobile_and_music_hosts(self, raw, expected):
        assert normalize_url(raw) == expected


class TestIdempotence:
    """Running normalize_url twice must be a no-op (required for safe
    data-backfill via migration 008)."""

    @pytest.mark.parametrize(
        "raw",
        [
            "https://chartable.com/track/ABC/traffic.libsyn.com/ep.mp3",
            "https://example.com/page?utm_source=x&real=1",
            "https://music.youtube.com/watch?v=dQw4w9WgXcQ",
            "https://example.com/ep.mp3#t=10",
            "https://EXAMPLE.COM/page/",
        ],
    )
    def test_normalize_is_idempotent(self, raw):
        once = normalize_url(raw)
        twice = normalize_url(once)
        assert once == twice
