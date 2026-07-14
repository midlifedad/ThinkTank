"""Persist author-written text as corpus content (Web-Lane Hardening W3.2).

The shared landing point for the W3.2 ingestion paths (papers now;
website/Substack next). Text authored BY an expert becomes a Content row
with status='done' and body_text set -- no transcription needed, the text
IS the body -- attributed via ContentThinker(role='author'). The existing
embed sweep (embed_pending_content) then chunks + embeds any done content
with body_text, so authored text flows into the searchable corpus on the
same rails as transcripts.

Dedupe is by canonical_url (the Content unique key). role='author' is the
load-bearing distinction from the enriched-receipt tier: only content the
expert actually wrote is retrieved as their own words.
"""

from __future__ import annotations

import uuid

import structlog
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from thinktank.models.content import Content, ContentThinker
from thinktank.models.thinker import Thinker

logger = structlog.get_logger(__name__)


async def create_author_content(
    session: AsyncSession,
    *,
    thinker: Thinker,
    source_id: uuid.UUID,
    content_type: str,
    title: str,
    url: str,
    body_text: str,
    published_at=None,
) -> bool:
    """Create one authored Content row + author attribution. Idempotent.

    Returns True when a new row was created, False when canonical_url
    already existed (dedupe). Caller commits.
    """
    if not body_text or not body_text.strip():
        return False
    content_id = uuid.uuid4()
    inserted_id = await session.scalar(
        pg_insert(Content)
        .values(
            id=content_id,
            source_id=source_id,
            content_type=content_type,
            url=url,
            canonical_url=url,
            title=title[:500],
            body_text=body_text,
            published_at=published_at,
            status="done",  # text needs no transcription; ready to embed
        )
        .on_conflict_do_nothing(index_elements=["canonical_url"])
        .returning(Content.id)
    )
    if inserted_id is None:
        return False  # already ingested (canonical_url unique)

    session.add(
        ContentThinker(
            content_id=inserted_id,
            thinker_id=thinker.id,
            role="author",
            confidence=100,  # authorship is definitional here, not a fuzzy name-match
        )
    )
    return True
