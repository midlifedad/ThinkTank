"""Handler: ingest_owned_text_source -- website/Substack text into the corpus.

Web-Lane Hardening W3.2b, completing W3. An approved owned text source
(the website/Substack rows W3.1 discovered) becomes authored corpus
content:

    substack -> fetch the RSS archive ({url}/feed), then pull each post's
                full text via the W1 fetch chain (Exa/Jina/bs4).
    website  -> Exa search for the expert's writing, kept to the source's
                own domain (text + date arrive inline, no re-fetch).

Each article lands as Content(status='done', role='author') via
create_author_content, so the embed sweep chunks + embeds it like a
transcript. Bounded by ARTICLE_LIMIT so a prolific blog can't flood.

Job payload schema: {"source_id": "uuid-str"}
"""

from __future__ import annotations

import uuid
from urllib.parse import urlparse

import httpx
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from thinktank.discovery.exa_client import exa_search
from thinktank.ingestion.feed_parser import parse_feed
from thinktank.ingestion.text_content import create_author_content
from thinktank.ingestion.web_fetch import fetch_document
from thinktank.models.job import Job
from thinktank.models.source import Source, SourceThinker
from thinktank.models.thinker import Thinker

logger = structlog.get_logger(__name__)

_TIMEOUT = 30.0
ARTICLE_LIMIT = 20


async def handle_ingest_owned_text_source(session: AsyncSession, job: Job) -> None:
    """Ingest an owned website/Substack source's articles as authored content."""
    source_id = job.payload.get("source_id")
    if not source_id:
        raise ValueError("source_id missing from ingest_owned_text_source payload")
    source = await session.get(Source, uuid.UUID(source_id) if isinstance(source_id, str) else source_id)
    if source is None:
        logger.warning("ingest_owned_text_source_not_found", source_id=str(source_id))
        return

    thinker = await _owning_thinker(session, source)
    if thinker is None:
        logger.warning("ingest_owned_text_source_no_owner", source_id=str(source_id))
        return

    log = logger.bind(job_id=str(job.id), source_type=source.source_type, thinker=thinker.slug)
    if source.source_type == "substack":
        created = await _ingest_substack(session, source, thinker)
    elif source.source_type == "website":
        created = await _ingest_website(session, source, thinker)
    else:
        log.info("ingest_owned_text_source_unsupported_type")
        return

    await session.commit()
    log.info("ingest_owned_text_source_complete", created=created)


async def _owning_thinker(session: AsyncSession, source: Source) -> Thinker | None:
    thinker_id = await session.scalar(
        select(SourceThinker.thinker_id).where(
            SourceThinker.source_id == source.id, SourceThinker.relationship_type == "owns"
        )
    )
    return await session.get(Thinker, thinker_id) if thinker_id else None


async def _ingest_substack(session: AsyncSession, source: Source, thinker: Thinker) -> int:
    feed_url = source.url.rstrip("/") + "/feed"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
            resp = await client.get(feed_url)
            resp.raise_for_status()
        entries = parse_feed(resp.text)
    except Exception:
        logger.warning("substack_feed_failed", url=feed_url, exc_info=True)
        return 0

    created = 0
    for entry in entries[:ARTICLE_LIMIT]:
        if not entry.url:
            continue
        document = await fetch_document(session, entry.url, found_via="owned_substack")
        if document is None or not document.text_content:
            continue
        if await create_author_content(
            session,
            thinker=thinker,
            source_id=source.id,
            content_type="article",
            title=entry.title or "Untitled",
            url=entry.url,
            body_text=document.text_content,
            published_at=entry.published_at or document.published_at,
        ):
            created += 1
    return created


async def _ingest_website(session: AsyncSession, source: Source, thinker: Thinker) -> int:
    domain = (urlparse(source.url).netloc or "").lower().removeprefix("www.")
    results = await exa_search(session, f"{thinker.name} articles essays blog posts writing", ARTICLE_LIMIT)
    created = 0
    for r in results:
        r_domain = (urlparse(r.url).netloc or "").lower().removeprefix("www.")
        # Only ingest content ON the expert's OWN domain -- else it's a
        # third-party piece (enriched-receipt tier), not authored corpus.
        if r_domain != domain or not r.text:
            continue
        if await create_author_content(
            session,
            thinker=thinker,
            source_id=source.id,
            content_type="article",
            title=r.title or "Untitled",
            url=r.url,
            body_text=r.text,
            published_at=r.published_at,
        ):
            created += 1
    return created
