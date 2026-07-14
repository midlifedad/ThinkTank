"""Fetch + clean open-access paper full text (Web-Lane Hardening W3.3).

Given an OA URL (PDF or landing page from OpenAlex), pull the article
text via Jina Reader (which extracts PDFs to markdown -- no PDF
dependency), strip reference/boilerplate tails, and return it for
appending after the abstract. Returns None when the fetch fails or the
result isn't materially richer than the abstract (a landing page that is
really just the abstract again), so the caller keeps the abstract-only
row -- full text can only ADD grounding material, never subtract.
"""

from __future__ import annotations

import re

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from thinktank.ingestion.web_fetch import MAX_TEXT_CHARS, fetch_via_jina

logger = structlog.get_logger(__name__)

# Full text must beat the abstract by this factor to be worth keeping --
# else the "full text" is just a landing page echoing the abstract.
_MIN_GAIN_FACTOR = 2.0

# Everything from a references/bibliography heading onward is citation
# noise, not the author's prose.
_REFERENCES_HEADING = re.compile(r"(?im)^\s{0,3}#{0,3}\s*(references|bibliography|works cited|literature cited)\s*$")
# Jina's own leading metadata block ("Title:/URL Source:/Markdown Content:").
_JINA_HEADER = re.compile(r"(?is)^\s*title:.*?markdown content:\s*", re.MULTILINE)


def strip_boilerplate(text: str) -> str:
    """Drop Jina's header and the reference/bibliography tail."""
    text = _JINA_HEADER.sub("", text, count=1)
    m = _REFERENCES_HEADING.search(text)
    if m:
        text = text[: m.start()]
    return text.strip()


async def fetch_paper_fulltext(session: AsyncSession, oa_url: str, abstract: str) -> str | None:
    """Fetch + clean OA full text. None on failure or no material gain."""
    if not oa_url:
        return None
    result = await fetch_via_jina(session, oa_url)
    if result is None:
        return None
    raw, _ = result
    cleaned = strip_boilerplate(raw)[:MAX_TEXT_CHARS]
    if len(cleaned) < len(abstract) * _MIN_GAIN_FACTOR:
        # Landing page that just re-states the abstract -- no real full text.
        logger.info("fulltext_no_material_gain", url=oa_url[:120], chars=len(cleaned), abstract_chars=len(abstract))
        return None
    logger.info("fulltext_fetched", url=oa_url[:120], chars=len(cleaned))
    return cleaned
