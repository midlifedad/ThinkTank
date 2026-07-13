"""Web document fetcher for the inquiry web lane.

Fetches a cited page, extracts readable text, and stores it as a
Document row -- the provenance record for web-lane observations. Text is
stored at fetch time so grounding stays verifiable if the page dies
(same reasoning as transcript body_text).

Dedupe is by exact URL (documents.url is unique): a page cited by many
inquiries is fetched once and reused.
"""

from __future__ import annotations

import uuid
from urllib.parse import urlparse

import httpx
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from thinktank.models.claim import Document

logger = structlog.get_logger(__name__)

_TIMEOUT = 30.0
_MAX_BYTES = 3_000_000
# Article text beyond this is truncated before storage/extraction --
# keeps LLM extraction prompts bounded.
MAX_TEXT_CHARS = 60_000
_USER_AGENT = "ThinkTankBot/1.0 (research corpus; contact: admin@thinktank.local)"


def extract_text(html: str) -> tuple[str, str | None]:
    """Extract readable text and title from an HTML page.

    Drops script/style/nav/header/footer/aside chrome; collapses
    whitespace per block so quotes ground against stable text.
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.get_text(strip=True) if soup.title else None
    for tag in soup(["script", "style", "noscript", "nav", "header", "footer", "aside", "form", "iframe"]):
        tag.decompose()
    blocks = [" ".join(chunk.split()) for chunk in soup.stripped_strings]
    return "\n".join(b for b in blocks if b), title


async def fetch_document(
    session: AsyncSession,
    url: str,
    found_via: str,
    search_query: str | None = None,
) -> Document | None:
    """Fetch-or-reuse a Document for a URL. None when unfetchable.

    Failures are recorded as fetch_status='failed' rows (so repeated
    inquiries don't re-hammer dead links) and None is returned -- the
    web lane degrades per-citation, never fails the inquiry.
    """
    existing = await session.scalar(select(Document).where(Document.url == url))
    if existing is not None:
        return existing if existing.fetch_status == "fetched" and existing.text_content else None

    log = logger.bind(url=url[:120])
    text: str | None = None
    title: str | None = None
    status = "failed"
    try:
        async with httpx.AsyncClient(
            timeout=_TIMEOUT, follow_redirects=True, headers={"User-Agent": _USER_AGENT}
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
        content_type = resp.headers.get("content-type", "")
        if "html" in content_type or "text" in content_type:
            text, title = extract_text(resp.text[:_MAX_BYTES])
            text = text[:MAX_TEXT_CHARS]
            if text.strip():
                status = "fetched"
        else:
            log.info("document_skipped_content_type", content_type=content_type)
    except Exception:
        log.warning("document_fetch_failed", exc_info=True)

    doc = Document(
        id=uuid.uuid4(),
        url=url,
        domain=urlparse(url).netloc or None,
        title=title,
        text_content=text if status == "fetched" else None,
        fetch_status=status,
        found_via=found_via,
        search_query=search_query,
    )
    session.add(doc)
    await session.flush()
    return doc if status == "fetched" else None
