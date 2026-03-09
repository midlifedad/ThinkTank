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
from sqlalchemy.ext.asyncio import AsyncSession

from src.thinktank.models.content import Content
from src.thinktank.models.source import Source
from src.thinktank.queue.claim import _now
from src.thinktank.transcription.audio import transcribe_via_gpu
from src.thinktank.transcription.captions import extract_youtube_captions
from src.thinktank.transcription.existing import fetch_existing_transcript
from src.thinktank.transcription.gpu_client import transcribe_with_chunking

logger = structlog.get_logger(__name__)


async def handle_process_content(session: AsyncSession, job: "Job") -> None:  # noqa: F821
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

    # Load source
    source = await session.get(Source, content.source_id)

    transcript: str | None = None
    method: str | None = None

    # Pass 1: YouTube captions (only for youtube_channel sources)
    if source.source_type == "youtube_channel":
        transcript = extract_youtube_captions(content.url)
        if transcript and len(transcript.split()) >= 100:
            method = "youtube_captions"
        else:
            transcript = None  # Reset if too short or None

    # Pass 2: Existing transcript (if source has transcript_url_pattern)
    if transcript is None:
        pattern = source.config.get("transcript_url_pattern")
        if pattern:
            transcript = await fetch_existing_transcript(content.url, pattern)
            if transcript:
                method = "existing_transcript"

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
    await session.commit()

    logger.info(
        "content_transcribed",
        content_id=str(content_id),
        method=method,
        word_count=content.word_count,
    )
