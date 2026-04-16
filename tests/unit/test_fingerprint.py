"""Unit tests for content fingerprint computation.

Tests spec Section 5.5 Layer 2: sha256(lowercase(title) || date || duration).

DATA-REVIEW H2 extensions: duration must be bucketed to the nearest
10-second boundary (so 3601s and 3609s fingerprint identically) and
title whitespace must be collapsed (so "  My   Episode\t" matches
"My Episode") before hashing.
"""

from datetime import datetime

import pytest

from thinktank.ingestion.fingerprint import compute_fingerprint


class TestBasicFingerprint:
    def test_basic_fingerprint(self):
        result = compute_fingerprint("My Episode", datetime(2025, 6, 15), 3600)
        assert isinstance(result, str)
        assert len(result) == 64  # sha256 hex digest is 64 chars

    def test_case_insensitive(self):
        fp1 = compute_fingerprint("Episode ONE", datetime(2025, 1, 1), 100)
        fp2 = compute_fingerprint("episode one", datetime(2025, 1, 1), 100)
        assert fp1 == fp2

    def test_deterministic(self):
        fp1 = compute_fingerprint("Same Title", datetime(2025, 3, 10), 500)
        fp2 = compute_fingerprint("Same Title", datetime(2025, 3, 10), 500)
        assert fp1 == fp2


class TestEdgeCases:
    def test_no_title_returns_none(self):
        assert compute_fingerprint("", datetime(2025, 1, 1), 100) is None
        assert compute_fingerprint(None, datetime(2025, 1, 1), 100) is None

    def test_no_date_uses_empty(self):
        result = compute_fingerprint("My Episode", None, 3600)
        assert isinstance(result, str)
        assert len(result) == 64

    def test_no_duration_uses_zero(self):
        result = compute_fingerprint("My Episode", datetime(2025, 1, 1), None)
        assert isinstance(result, str)
        assert len(result) == 64

    def test_different_duration_different_fingerprint(self):
        fp1 = compute_fingerprint("Same Episode", datetime(2025, 1, 1), 3600)
        fp2 = compute_fingerprint("Same Episode", datetime(2025, 1, 1), 7200)
        assert fp1 != fp2


# DATA-REVIEW H2: robust fingerprint -- duration bucketing + whitespace.


class TestDurationBucketing:
    """Duration is bucketed to nearest 10s so transcode-level jitter
    (e.g. 3601s vs 3605s vs 3609s) still fingerprints identically."""

    @pytest.mark.parametrize(
        "d1, d2",
        [
            (3600, 3601),
            (3600, 3604),
            (3605, 3609),  # both bucket to 3610
            (0, 4),
            (7195, 7204),  # both bucket to 7200
        ],
    )
    def test_close_durations_same_fingerprint(self, d1, d2):
        fp1 = compute_fingerprint("Same Episode", datetime(2025, 1, 1), d1)
        fp2 = compute_fingerprint("Same Episode", datetime(2025, 1, 1), d2)
        assert fp1 == fp2, (
            f"Durations {d1} and {d2} should fingerprint identically"
        )

    def test_different_buckets_differ(self):
        """Durations that bucket to different 10s boundaries still differ."""
        fp1 = compute_fingerprint("Same Episode", datetime(2025, 1, 1), 3600)
        fp2 = compute_fingerprint("Same Episode", datetime(2025, 1, 1), 3615)
        assert fp1 != fp2


class TestTitleWhitespaceNormalization:
    """Collapse runs of whitespace and strip leading/trailing."""

    @pytest.mark.parametrize(
        "t1, t2",
        [
            ("My Episode", "  My Episode  "),
            ("My Episode", "My   Episode"),
            ("My Episode", "My\tEpisode"),
            ("My Episode", "My\n\nEpisode"),
            ("My Episode", "  my\tEPISODE  "),  # combined with case-fold
        ],
    )
    def test_whitespace_variants_same_fingerprint(self, t1, t2):
        fp1 = compute_fingerprint(t1, datetime(2025, 1, 1), 100)
        fp2 = compute_fingerprint(t2, datetime(2025, 1, 1), 100)
        assert fp1 == fp2, (
            f"Titles {t1!r} and {t2!r} should fingerprint identically"
        )

    def test_whitespace_only_title_returns_none(self):
        """A title that is only whitespace should be treated as empty."""
        assert compute_fingerprint("   \t\n  ", datetime(2025, 1, 1), 100) is None
