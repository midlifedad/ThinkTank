"""Unit tests for OA full-text fetch + cleaning (W3.3)."""

from unittest.mock import AsyncMock, patch

import pytest

from thinktank.ingestion.fulltext import fetch_paper_fulltext, strip_boilerplate

pytestmark = pytest.mark.anyio


class TestStripBoilerplate:
    def test_drops_jina_header(self):
        raw = "Title: A Paper\nURL Source: https://x\nMarkdown Content:\nActual body here."
        assert strip_boilerplate(raw) == "Actual body here."

    def test_cuts_references_tail(self):
        raw = "Body of the paper.\n\n## References\n\n1. Smith et al.\n2. Jones et al."
        assert strip_boilerplate(raw) == "Body of the paper."

    def test_no_boilerplate_unchanged(self):
        raw = "Just clean prose about rapamycin and mTOR."
        assert strip_boilerplate(raw) == raw


class TestFetchPaperFulltext:
    async def test_returns_cleaned_fulltext(self, monkeypatch=None):
        abstract = "Short abstract."
        body = "Full paper body. " * 20  # comfortably > 2x abstract
        with patch(
            "thinktank.ingestion.fulltext.fetch_via_jina",
            new=AsyncMock(return_value=(f"Title: P\nMarkdown Content:\n{body}\n\n## References\n1. x", None)),
        ):
            out = await fetch_paper_fulltext(None, "https://oa.example/paper.pdf", abstract)
        assert out and out.startswith("Full paper body.")
        assert "References" not in out

    async def test_no_material_gain_returns_none(self):
        """A landing page that just echoes the abstract -> keep abstract only."""
        abstract = "This is the abstract text, fairly long, stating the finding."
        with patch(
            "thinktank.ingestion.fulltext.fetch_via_jina",
            new=AsyncMock(return_value=("Markdown Content:\n" + abstract, None)),
        ):
            assert await fetch_paper_fulltext(None, "https://oa.example/landing", abstract) is None

    async def test_fetch_failure_returns_none(self):
        with patch("thinktank.ingestion.fulltext.fetch_via_jina", new=AsyncMock(return_value=None)):
            assert await fetch_paper_fulltext(None, "https://oa.example/x", "abstract") is None

    async def test_no_url_returns_none(self):
        assert await fetch_paper_fulltext(None, "", "abstract") is None
