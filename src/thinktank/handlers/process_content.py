"""Three-pass transcription orchestrator handler (process_content).

Spec reference: Section 7 (Transcription Pipeline).
Implements the three-pass fallback chain:
  Pass 1: YouTube captions (free, fast) -- only for youtube_channel sources
  Pass 2: Existing transcript (per-source URL pattern)
  Pass 3: Parakeet GPU inference (download + convert + GPU service)

Updates content row with body_text, word_count, transcription_method,
status='done', and processed_at on success. Raises RuntimeError if
all passes fail (worker loop categorizes as TRANSCRIPTION_FAILED).
"""

from __future__ import annotations

import os
import tempfile
import uuid

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from thinktank.models.content import Content, ContentThinker
from thinktank.models.job import Job
from thinktank.models.source import Source
from thinktank.models.thinker import Thinker
from thinktank.queue.claim import _now
from thinktank.queue.retry import get_max_attempts
from thinktank.transcription.assemblyai import is_transcription_api_enabled, transcribe_via_assemblyai
from thinktank.transcription.audio import transcribe_via_gpu
from thinktank.transcription.captions import extract_youtube_captions
from thinktank.transcription.existing import fetch_existing_transcript
from thinktank.transcription.gpu_client import transcribe_with_chunking

logger = structlog.get_logger(__name__)


async def handle_process_content(session: AsyncSession, job: Job) -> None:  # noqa: F821
    """Orchestrate three-pass transcription for a content item.

    Args:
        session: Active database session.
        job: The process_content job with payload containing content_id.

    Raises:
        ValueError: If content_id missing from payload or content not found.
        RuntimeError: If all three transcription passes fail.
    """
    # Extract content_id from job payload
    raw_content_id = job.payload.get("content_id")
    if not raw_content_id:
        raise ValueError("content_id missing from job payload")
    content_id = uuid.UUID(raw_content_id)

    # Load content
    content = await session.get(Content, content_id)
    if content is None:
        raise ValueError(f"Content {content_id} not found")

    # Load source. If the Source row was deleted between content
    # insertion and transcription, raise ValueError -- this maps to
    # ErrorCategory.PAYLOAD_INVALID (terminal) in categorize_error,
    # so the job fails permanently instead of looping through retries
    # with an opaque AttributeError.
    source = await session.get(Source, content.source_id)
    if source is None:
        raise ValueError(f"Source {content.source_id} missing for content {content_id}")

    transcript: str | None = None
    method: str | None = None

    # Pass 1: YouTube captions (only for youtube_channel sources)
    if source.source_type == "youtube_channel":
        transcript = await extract_youtube_captions(content.url)
        if transcript and len(transcript.split()) >= 100:
            method = "youtube_captions"
        else:
            transcript = None  # Reset if too short or None

    # Pass 2: Existing transcript (if source has transcript_url_pattern)
    if transcript is None:
        config = source.config or {}
        pattern = config.get("transcript_url_pattern")
        if pattern:
            transcript = await fetch_existing_transcript(content.url, pattern)
            if transcript:
                method = "existing_transcript"

    # Pass 2.5: AssemblyAI batch API with speaker diarization (optional,
    # config-gated via transcription_api_enabled -- Amir-approved batch
    # processor 2026-07-11). Podcast enclosure URLs are publicly fetchable
    # so AssemblyAI pulls the audio itself; YouTube page URLs are not
    # direct audio, so youtube_channel sources skip straight to the GPU
    # pass (captions usually got them in pass 1 anyway). Any failure falls
    # through -- this pass can only add capacity, never block.
    if transcript is None and source.source_type != "youtube_channel" and await is_transcription_api_enabled(session):
        thinker_names_result = await session.execute(
            select(Thinker.name)
            .join(ContentThinker, ContentThinker.thinker_id == Thinker.id)
            .where(ContentThinker.content_id == content_id)
        )
        keyterms = [name for (name,) in thinker_names_result.all()]
        transcript = await transcribe_via_assemblyai(session, content.url, keyterms=keyterms)
        if transcript:
            method = "assemblyai"

    # Pass 3: Parakeet GPU inference
    if transcript is None:
        tmp_dir = os.environ.get("AUDIO_TMP_DIR", tempfile.gettempdir())

        async def _gpu_fn(wav_path: str) -> str:
            return await transcribe_with_chunking(wav_path, content.duration_seconds)

        try:
            result = await transcribe_via_gpu(content.url, tmp_dir, _gpu_fn)
            if result:
                transcript = result
                method = "parakeet"
        except Exception:
            logger.warning(
                "gpu_transcription_failed",
                content_id=str(content_id),
                exc_info=True,
            )

    # All passes failed
    if transcript is None:
        raise RuntimeError(f"All transcription passes failed for content {content_id}")

    # Update content with transcript
    content.body_text = transcript
    content.word_count = len(transcript.split())
    content.transcription_method = method
    content.status = "done"
    content.processed_at = _now()

    # Claims v2: chunk+embed the fresh transcript (Mac-routed job). The
    # embed_pending_content sweep covers backlog/desyncs.
    session.add(
        Job(
            id=uuid.uuid4(),
            job_type="embed_content",
            payload={"content_id": str(content_id)},
            priority=6,
            status="pending",
            attempts=0,
            max_attempts=get_max_attempts("embed_content"),
            created_at=_now(),
        )
    )
    await session.commit()

    logger.info(
        "content_transcribed",
        content_id=str(content_id),
        method=method,
        word_count=content.word_count,
    )
