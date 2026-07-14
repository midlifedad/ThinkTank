"""Web document fetcher (Web-Lane Hardening W1).

Stores a fetched page as a Document row -- the provenance record for
web-lane observations. Text is stored at fetch time so grounding stays
verifiable if the page dies (same reasoning as transcript body_text).

Fetch is a fallback chain, best extractor first:
    1. Exa /contents  -- clean text + publishedDate + author, handles
                         JS-rendered and bot-gated pages (PMC, YouTube).
    2. Jina Reader    -- cheap markdown extractor for arbitrary URLs.
    3. httpx + bs4    -- the original extractor, with meta-tag date
                         parsing, as a last resort.

Publication dates flow onto documents.published_at and, from there, onto
each web observation's asserted_at -- closing the "all web observations
undated" gap from the first live inquiry.

Dedupe is by exact URL (documents.url is unique): a page cited by many
inquiries is fetched once and reused.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from urllib.parse import urlparse

import httpx
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from thinktank.discovery.exa_client import ExaResult, exa_contents
from thinktank.models.claim import Document
from thinktank.secrets import get_secret

logger = structlog.get_logger(__name__)

_TIMEOUT = 30.0
_MAX_BYTES = 3_000_000
MAX_TEXT_CHARS = 60_000
_USER_AGENT = "ThinkTankBot/1.0 (research corpus; contact: admin@thinktank.local)"
_JINA_ENDPOINT = "https://r.jina.ai/"


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


_META_DATE_PATTERNS = (
    r'<meta[^>]+property=["\']article:published_time["\'][^>]+content=["\']([^"\']+)["\']',
    r'<meta[^>]+name=["\'](?:date|pubdate|publishdate|dc\.date)["\'][^>]+content=["\']([^"\']+)["\']',
    r'<time[^>]+datetime=["\']([^"\']+)["\']',
    r'"datePublished"\s*:\s*"([^"]+)"',
)


def parse_published_at(html: str) -> datetime | None:
    """Best-effort publication date from page meta tags / JSON-LD."""
    for pattern in _META_DATE_PATTERNS:
        m = re.search(pattern, html, re.IGNORECASE)
        if not m:
            continue
        try:
            dt = datetime.fromisoformat(m.group(1).strip().replace("Z", "+00:00"))
        except (ValueError, TypeError):
            continue
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    return None


async def fetch_via_jina(session: AsyncSession, url: str) -> tuple[str, str | None] | None:
    """Jina Reader: URL -> markdown. (text, title) or None. No reliable date.

    Handles PDFs and JS-rendered pages -- reused by W3.3 full-text paper
    ingestion (Jina extracts OA PDFs to clean markdown, no PDF dep).
    """
    api_key = await get_secret(session, "jina_api_key")
    headers = {"X-Return-Format": "markdown", "Accept": "text/plain"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
            resp = await client.get(_JINA_ENDPOINT + url, headers=headers)
            resp.raise_for_status()
        text = resp.text.strip()
    except Exception:
        logger.warning("jina_fetch_failed", url=url[:120], exc_info=True)
        return None
    if not text:
        return None
    # Jina prepends a "Title: ...\nURL Source: ...\nMarkdown Content:" header.
    title = None
    m = re.match(r"Title:\s*(.+)", text)
    if m:
        title = m.group(1).strip()
    return text[:MAX_TEXT_CHARS], title


async def _upsert(
    session: AsyncSession,
    *,
    url: str,
    text: str | None,
    title: str | None,
    published_at: datetime | None,
    author: str | None,
    found_via: str,
    search_query: str | None,
) -> Document | None:
    status = "fetched" if text and text.strip() else "failed"
    doc = Document(
        id=uuid.uuid4(),
        url=url,
        domain=urlparse(url).netloc or None,
        title=title,
        author=author,
        published_at=published_at,
        text_content=text if status == "fetched" else None,
        fetch_status=status,
        found_via=found_via,
        search_query=search_query,
    )
    session.add(doc)
    await session.flush()
    return doc if status == "fetched" else None


async def store_exa_result(
    session: AsyncSession,
    result: ExaResult,
    *,
    found_via: str,
    search_query: str | None = None,
) -> Document | None:
    """Persist an ExaResult (from exa_search) directly as a Document.

    The inquiry web lane gets text + date inline from search, so it need
    not re-fetch each URL. Dedupes by URL like fetch_document.
    """
    existing = await session.scalar(select(Document).where(Document.url == result.url))
    if existing is not None:
        return existing if existing.fetch_status == "fetched" and existing.text_content else None
    return await _upsert(
        session,
        url=result.url,
        text=result.text,
        title=result.title,
        published_at=result.published_at,
        author=result.author,
        found_via=found_via,
        search_query=search_query,
    )


async def fetch_document(
    session: AsyncSession,
    url: str,
    found_via: str,
    search_query: str | None = None,
) -> Document | None:
    """Fetch-or-reuse a Document for a URL via the fallback chain.

    None when unfetchable. Failures are recorded as fetch_status='failed'
    rows (so repeated inquiries don't re-hammer dead links); the web lane
    degrades per-URL, never fails the inquiry.
    """
    existing = await session.scalar(select(Document).where(Document.url == url))
    if existing is not None:
        return existing if existing.fetch_status == "fetched" and existing.text_content else None

    log = logger.bind(url=url[:120])
    text: str | None = None
    title: str | None = None
    published_at: datetime | None = None
    author: str | None = None

    # 1. Exa contents (best extractor + date + author).
    exa = await exa_contents(session, url)
    if exa is not None:
        text, title, published_at, author = exa.text, exa.title, exa.published_at, exa.author

    # 2. Jina Reader (cheap markdown fallback).
    if not text:
        jina = await fetch_via_jina(session, url)
        if jina is not None:
            text, title = jina

    # 3. httpx + bs4 (original extractor, with meta-tag date parsing).
    if not text:
        try:
            async with httpx.AsyncClient(
                timeout=_TIMEOUT, follow_redirects=True, headers={"User-Agent": _USER_AGENT}
            ) as client:
                resp = await client.get(url)
                resp.raise_for_status()
            content_type = resp.headers.get("content-type", "")
            if "html" in content_type or "text" in content_type:
                raw = resp.text[:_MAX_BYTES]
                text, title = extract_text(raw)
                text = text[:MAX_TEXT_CHARS]
                published_at = parse_published_at(raw)
            else:
                log.info("document_skipped_content_type", content_type=content_type)
        except Exception:
            log.warning("document_fetch_failed", exc_info=True)

    return await _upsert(
        session,
        url=url,
        text=text,
        title=title,
        published_at=published_at,
        author=author,
        found_via=found_via,
        search_query=search_query,
    )
