"""Contract tests for the embedding stage (embed_content + sweep).

Embeddings mocked (the real /embed lives on the Mac service); the
contract under test is chunk persistence, offsets, idempotency, force
re-chunking, and sweep coverage rules.
"""

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.factories import create_content, create_content_chunk, create_job, create_source
from thinktank.handlers.embed_content import handle_embed_content
from thinktank.handlers.embed_pending_content import handle_embed_pending_content
from thinktank.models.claim import ContentChunk
from thinktank.models.job import Job

pytestmark = pytest.mark.anyio

BODY = (
    "Speaker A: " + " ".join(f"alpha{i}" for i in range(60)) + "\nSpeaker B: " + " ".join(f"beta{i}" for i in range(60))
)


def _fake_embed():
    async def _embed(texts):
        return [[0.1] * 768 for _ in texts]

    return patch("thinktank.handlers.embed_content.embed_texts", new=AsyncMock(side_effect=_embed))


async def _run(session: AsyncSession, content, force=False) -> None:
    payload = {"content_id": str(content.id)}
    if force:
        payload["force"] = True
    job = await create_job(session, job_type="embed_content", payload=payload)
    with _fake_embed():
        await handle_embed_content(session, job)


class TestEmbedContent:
    async def test_chunks_persisted_with_offsets_and_vectors(self, session: AsyncSession):
        source = await create_source(session)
        content = await create_content(session, source_id=source.id, status="done", body_text=BODY)

        await _run(session, content)

        chunks = (
            (await session.execute(select(ContentChunk).where(ContentChunk.content_id == content.id))).scalars().all()
        )
        assert len(chunks) == 2
        for c in sorted(chunks, key=lambda c: c.chunk_index):
            assert BODY[c.char_start : c.char_end] == c.text
            assert c.embedding is not None and len(c.embedding) == 768

    async def test_idempotent_without_force(self, session: AsyncSession):
        source = await create_source(session)
        content = await create_content(session, source_id=source.id, status="done", body_text=BODY)
        await create_content_chunk(session, content_id=content.id, chunk_index=0)

        await _run(session, content)

        count = len(
            (await session.execute(select(ContentChunk).where(ContentChunk.content_id == content.id))).scalars().all()
        )
        assert count == 1  # untouched

    async def test_force_rechunks(self, session: AsyncSession):
        source = await create_source(session)
        content = await create_content(session, source_id=source.id, status="done", body_text=BODY)
        await create_content_chunk(session, content_id=content.id, chunk_index=0, text="stale")

        await _run(session, content, force=True)

        chunks = (
            (await session.execute(select(ContentChunk).where(ContentChunk.content_id == content.id))).scalars().all()
        )
        assert len(chunks) == 2
        assert all(c.text != "stale" for c in chunks)

    async def test_untranscribed_content_skipped(self, session: AsyncSession):
        source = await create_source(session)
        content = await create_content(session, source_id=source.id, status="pending", body_text=None)
        await _run(session, content)
        count = len((await session.execute(select(ContentChunk))).scalars().all())
        assert count == 0


class TestEmbedSweep:
    async def test_sweep_enqueues_unchunked_done_content(self, session: AsyncSession):
        source = await create_source(session)
        done = await create_content(session, source_id=source.id, status="done", body_text=BODY)
        chunked = await create_content(session, source_id=source.id, status="done", body_text=BODY)
        await create_content_chunk(session, content_id=chunked.id, chunk_index=0)
        await create_content(session, source_id=source.id, status="pending", body_text=None)

        job = await create_job(session, job_type="embed_pending_content", payload={})
        await handle_embed_pending_content(session, job)

        embed_jobs = (await session.execute(select(Job).where(Job.job_type == "embed_content"))).scalars().all()
        assert [j.payload["content_id"] for j in embed_jobs] == [str(done.id)]

    async def test_sweep_skips_inflight(self, session: AsyncSession):
        source = await create_source(session)
        done = await create_content(session, source_id=source.id, status="done", body_text=BODY)
        await create_job(session, job_type="embed_content", payload={"content_id": str(done.id)}, status="pending")

        job = await create_job(session, job_type="embed_pending_content", payload={})
        await handle_embed_pending_content(session, job)

        embed_jobs = (await session.execute(select(Job).where(Job.job_type == "embed_content"))).scalars().all()
        assert len(embed_jobs) == 1  # only the pre-existing one
