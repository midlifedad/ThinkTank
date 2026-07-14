"""Persist author-written text as corpus content (Web-Lane Hardening W3.2).

The shared landing point for the W3.2 ingestion paths (papers, website,
Substack). Text authored BY an expert becomes a Content row with
status='done' and body_text set -- no transcription needed, the text IS
the body -- attributed via ContentThinker(role='author'), and an
embed_content job is enqueued IN THE SAME TRANSACTION so it chunks +
embeds within seconds, exactly like a fresh transcript (process_content).
The embed_pending_content sweep remains the reconciling safety net.

Enqueuing the embed in the content's own transaction is deliberately
stronger than the transcript path (which enqueues in a separate commit,
leaving a crash-window the sweep exists to cover): here content row and
embed job commit together, so there is no window at all.

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
from thinktank.models.job import Job
from thinktank.models.thinker import Thinker
from thinktank.queue.retry import get_max_attempts

logger = structlog.get_logger(__name__)

# Below transcription (5) so text embeds never starve the latency-
# sensitive transcription path -- matches process_content's embed jobs.
_EMBED_PRIORITY = 6


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
    # Immediate embed enqueue (same transaction as the content row) so
    # authored text is searchable within seconds, not on the next hourly
    # sweep. Idempotent downstream: embed_content skips already-chunked
    # content, and the sweep skips content with an in-flight embed job.
    session.add(
        Job(
            id=uuid.uuid4(),
            job_type="embed_content",
            payload={"content_id": str(inserted_id)},
            priority=_EMBED_PRIORITY,
            status="pending",
            attempts=0,
            max_attempts=get_max_attempts("embed_content"),
        )
    )
    return True
