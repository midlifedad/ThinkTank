"""Unit tests for web document text extraction + date parsing (W1)."""

import pytest

from thinktank.ingestion.web_fetch import extract_text, parse_published_at

pytestmark = pytest.mark.anyio

HTML = """
<html>
  <head>
    <title>Rapamycin and Longevity</title>
    <style>body { color: red; }</style>
    <script>trackEverything();</script>
  </head>
  <body>
    <nav><a href="/">Home</a> <a href="/about">About</a></nav>
    <article>
      <h1>Rapamycin and Longevity</h1>
      <p>Dr. Test said rapamycin   extends
         lifespan in mice.</p>
      <p>The mechanism involves mTOR inhibition.</p>
    </article>
    <footer>Copyright 2026</footer>
  </body>
</html>
"""


class TestExtractText:
    def test_extracts_title_and_body(self):
        text, title = extract_text(HTML)
        assert title == "Rapamycin and Longevity"
        assert "rapamycin extends lifespan in mice" in text
        assert "mTOR inhibition" in text

    def test_drops_chrome_and_scripts(self):
        text, _ = extract_text(HTML)
        assert "trackEverything" not in text
        assert "color: red" not in text
        assert "Home" not in text
        assert "Copyright" not in text

    def test_whitespace_collapsed_within_blocks(self):
        """Grounding depends on stable text: intra-block runs of
        whitespace collapse to single spaces."""
        text, _ = extract_text(HTML)
        assert "rapamycin extends lifespan" in text
        assert "  " not in text.replace("\n", " ")

    def test_empty_html(self):
        text, title = extract_text("<html><body></body></html>")
        assert text == ""
        assert title is None


class TestParsePublishedAt:
    def test_article_meta(self):
        html = '<meta property="article:published_time" content="2023-05-01T00:00:00Z">'
        assert parse_published_at(html).year == 2023

    def test_json_ld(self):
        html = '<script type="application/ld+json">{"datePublished": "2022-11-30"}</script>'
        dt = parse_published_at(html)
        assert dt.year == 2022 and dt.tzinfo is not None

    def test_time_tag(self):
        assert parse_published_at('<time datetime="2021-01-15">Jan 15</time>').year == 2021

    def test_none_when_absent(self):
        assert parse_published_at("<html><body>no date</body></html>") is None
