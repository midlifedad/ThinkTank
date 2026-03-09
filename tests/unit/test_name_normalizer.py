"""Unit tests for candidate name normalization.

Tests per spec Section 5.5 Layer 3: lowercase, strip titles, unicode NFC, collapse whitespace.
"""

import unicodedata

from src.thinktank.ingestion.name_normalizer import normalize_name


class TestBasicNormalization:
    def test_lowercase(self):
        assert normalize_name("John Smith") == "john smith"

    def test_collapse_whitespace(self):
        assert normalize_name("  John   Smith  ") == "john smith"


class TestTitleStripping:
    def test_strip_dr(self):
        assert normalize_name("Dr. John Smith") == "john smith"

    def test_strip_prof(self):
        assert normalize_name("Prof. Jane Doe") == "jane doe"

    def test_strip_phd(self):
        assert normalize_name("John Smith Ph.D.") == "john smith"

    def test_strip_jr(self):
        assert normalize_name("John Smith Jr.") == "john smith"

    def test_strip_iii(self):
        assert normalize_name("John Smith III") == "john smith"

    def test_combined(self):
        assert normalize_name("Dr. John Smith Jr.") == "john smith"


class TestUnicode:
    def test_unicode_normalize(self):
        """Accented names should be NFC normalized and lowercased."""
        # e with combining acute accent (NFD form)
        name_nfd = "Ren\u0065\u0301 Descartes"
        result = normalize_name(name_nfd)
        # Should be NFC normalized
        assert result == unicodedata.normalize("NFC", "ren\u00e9 descartes")
