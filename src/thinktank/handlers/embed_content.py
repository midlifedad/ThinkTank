"""Handler: embed_content -- chunk + embed one transcript into content_chunks.

Claims v2 PR 2. Runs ONLY on the Mac worker (WORKER_JOB_TYPES routing):
the /embed endpoint lives on the local inference service, unreachable
from Railway. Idempotent: content with existing chunks is skipped unless
the job carries force=true (which re-chunks from scratch -- used when the
chunker or embedding model changes).

Job payload schema: {"content_id": "uuid-str", "force": bool?}
"""

from __future__ import annotations

import uuid

import structlog
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from thinktank.embeddings import embed_texts
from thinktank.ingestion.chunker import chunk_document, chunk_transcript
from thinktank.models.claim import ContentChunk
from thinktank.models.content import Content
from thinktank.models.job import Job

logger = structlog.get_logger(__name__)


async def handle_embed_content(session: AsyncSession, job: Job) -> None:
    """Chunk a done transcript and store embedded content_chunks rows."""
    content_id_str = job.payload.get("content_id")
    if not content_id_str:
        raise ValueError("content_id missing from embed_content payload")
    content_id = uuid.UUID(content_id_str)
    force = bool(job.payload.get("force"))

    content = await session.get(Content, content_id)
    if content is None:
        raise ValueError(f"Content {content_id} not found")

    log = logger.bind(job_id=str(job.id), content_id=content_id_str)

    if not content.body_text or content.status != "done":
        log.info("embed_content_skipped", reason="no transcript", status=content.status)
        return

    existing = await session.scalar(
        select(func.count()).select_from(ContentChunk).where(ContentChunk.content_id == content_id)
    )
    if existing and not force:
        log.info("embed_content_skipped", reason="already chunked", chunks=existing)
        return
    if existing and force:
        await session.execute(delete(ContentChunk).where(ContentChunk.content_id == content_id))

    # Prose (papers, articles) needs the document chunker -- it splits
    # within long paragraphs that the transcript chunker would leave as
    # over-budget, embed-truncated chunks.
    if content.content_type in ("paper", "article"):
        chunks = chunk_document(content.body_text)
    else:
        chunks = chunk_transcript(content.body_text)
    if not chunks:
        log.warning("embed_content_empty_chunks")
        return

    vectors = await embed_texts([c.text for c in chunks])

    for chunk, vector in zip(chunks, vectors, strict=True):
        session.add(
            ContentChunk(
                id=uuid.uuid4(),
                content_id=content_id,
                chunk_index=chunk.index,
                speaker_label=chunk.speaker_label,
                text=chunk.text,
                char_start=chunk.char_start,
                char_end=chunk.char_end,
                embedding=vector,
            )
        )
    await session.commit()

    log.info("embed_content_complete", chunks=len(chunks), words=len(content.body_text.split()))
