"""Handler: embed_pending_content -- backlog sweep for the embedding stage.

Claims v2 PR 2, applying the A1 lesson from day one: the direct enqueue
(process_content completion -> embed_content) covers new transcripts;
this sweep covers the pre-existing backlog (126+ done transcripts) and
any desyncs. Bounded per tick so a big backlog drains steadily without
monopolizing the Mac worker against transcription.

Job payload schema: {} (triggered_by optional).
"""

from __future__ import annotations

import uuid

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from thinktank.models.claim import ContentChunk
from thinktank.models.content import Content
from thinktank.models.job import Job
from thinktank.queue.claim import _now
from thinktank.queue.retry import get_max_attempts

logger = structlog.get_logger(__name__)

# Max embed jobs enqueued per sweep tick.
SWEEP_BUDGET = 25


async def handle_embed_pending_content(session: AsyncSession, job: Job) -> None:
    """Enqueue embed_content for done transcripts with no chunks and no
    in-flight embed job."""
    log = logger.bind(job_id=str(job.id))

    chunked = select(ContentChunk.content_id).distinct()
    covered_result = await session.execute(
        select(Job.payload["content_id"].astext).where(
            Job.job_type == "embed_content",
            Job.status.in_(["pending", "running", "retrying"]),
        )
    )
    covered_ids = {row for (row,) in covered_result.all() if row}

    pending_result = await session.execute(
        select(Content.id)
        .where(
            Content.status == "done",
            Content.body_text.is_not(None),
            Content.id.not_in(chunked),
        )
        .order_by(Content.processed_at)
    )
    todo = [cid for (cid,) in pending_result.all() if str(cid) not in covered_ids][:SWEEP_BUDGET]

    now = _now()
    for content_id in todo:
        session.add(
            Job(
                id=uuid.uuid4(),
                job_type="embed_content",
                payload={"content_id": str(content_id)},
                priority=6,  # below transcription -- embeddings are catch-up work
                status="pending",
                attempts=0,
                max_attempts=get_max_attempts("embed_content"),
                created_at=now,
            )
        )
    await session.commit()

    log.info("embed_sweep_complete", enqueued=len(todo))
