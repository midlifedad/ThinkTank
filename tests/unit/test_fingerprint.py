"""Unit tests for content fingerprint computation.

Tests spec Section 5.5 Layer 2: sha256(lowercase(title) || date || duration).
"""

from datetime import datetime

from src.thinktank.ingestion.fingerprint import compute_fingerprint


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
