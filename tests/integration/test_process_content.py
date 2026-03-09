"""Integration tests for process_content handler with real DB, mocked external services.

Tests verify the handler correctly orchestrates transcription passes and
updates database state. External services (yt-dlp, httpx, ffmpeg, GPU)
are mocked at the CALL SITE (handler module namespace) to correctly
intercept imported names.
"""

from unittest.mock import AsyncMock, patch

import pytest

from src.thinktank.handlers.process_content import handle_process_content
from tests.factories import create_content, create_job, create_source, create_thinker

LONG_TRANSCRIPT = " ".join(f"word{i}" for i in range(200))


@patch("src.thinktank.handlers.process_content.transcribe_via_gpu", new_callable=AsyncMock)
@patch("src.thinktank.handlers.process_content.fetch_existing_transcript", new_callable=AsyncMock)
@patch("src.thinktank.handlers.process_content.extract_youtube_captions")
async def test_full_pipeline_youtube_captions(
    mock_captions, mock_existing, mock_gpu, session
):
    """YouTube source: captions succeed -> content updated with youtube_captions method."""
    thinker = await create_thinker(session)
    source = await create_source(
        session, thinker_id=thinker.id, source_type="youtube_channel"
    )
    content = await create_content(
        session,
        source_id=source.id,
        source_owner_id=thinker.id,
        status="pending",
        url="https://youtube.com/watch?v=test123",
    )
    job = await create_job(
        session,
        job_type="process_content",
        payload={"content_id": str(content.id)},
    )
    await session.commit()

    mock_captions.return_value = LONG_TRANSCRIPT

    await handle_process_content(session, job)

    await session.refresh(content)
    assert content.status == "done"
    assert content.transcription_method == "youtube_captions"
    assert content.body_text == LONG_TRANSCRIPT
    assert content.word_count == 200
    assert content.processed_at is not None


@patch("src.thinktank.handlers.process_content.transcribe_via_gpu", new_callable=AsyncMock)
@patch("src.thinktank.handlers.process_content.fetch_existing_transcript", new_callable=AsyncMock)
@patch("src.thinktank.handlers.process_content.extract_youtube_captions")
async def test_full_pipeline_parakeet_fallback(
    mock_captions, mock_existing, mock_gpu, session
):
    """Podcast source: captions N/A, existing=None -> GPU fallback with parakeet method."""
    thinker = await create_thinker(session)
    source = await create_source(
        session, thinker_id=thinker.id, source_type="podcast_rss"
    )
    content = await create_content(
        session,
        source_id=source.id,
        source_owner_id=thinker.id,
        status="pending",
    )
    job = await create_job(
        session,
        job_type="process_content",
        payload={"content_id": str(content.id)},
    )
    await session.commit()

    # Captions not attempted (not youtube), existing returns None, GPU returns text
    mock_existing.return_value = None
    mock_gpu.return_value = LONG_TRANSCRIPT

    await handle_process_content(session, job)

    await session.refresh(content)
    assert content.status == "done"
    assert content.transcription_method == "parakeet"
    assert content.body_text is not None
    assert content.word_count > 0
    assert content.processed_at is not None
    # Captions never called for non-YouTube
    mock_captions.assert_not_called()


@patch("src.thinktank.handlers.process_content.transcribe_via_gpu", new_callable=AsyncMock)
@patch("src.thinktank.handlers.process_content.fetch_existing_transcript", new_callable=AsyncMock)
@patch("src.thinktank.handlers.process_content.extract_youtube_captions")
async def test_full_pipeline_existing_transcript(
    mock_captions, mock_existing, mock_gpu, session
):
    """Source with transcript_url_pattern: captions=None -> existing transcript used."""
    thinker = await create_thinker(session)
    source = await create_source(
        session,
        thinker_id=thinker.id,
        source_type="youtube_channel",
        config={"transcript_url_pattern": "https://example.com/transcripts/{slug}"},
    )
    content = await create_content(
        session,
        source_id=source.id,
        source_owner_id=thinker.id,
        status="pending",
    )
    job = await create_job(
        session,
        job_type="process_content",
        payload={"content_id": str(content.id)},
    )
    await session.commit()

    mock_captions.return_value = None
    mock_existing.return_value = LONG_TRANSCRIPT

    await handle_process_content(session, job)

    await session.refresh(content)
    assert content.status == "done"
    assert content.transcription_method == "existing_transcript"
    assert content.body_text == LONG_TRANSCRIPT


@patch("src.thinktank.handlers.process_content.transcribe_via_gpu", new_callable=AsyncMock)
@patch("src.thinktank.handlers.process_content.fetch_existing_transcript", new_callable=AsyncMock)
@patch("src.thinktank.handlers.process_content.extract_youtube_captions")
async def test_content_status_done_after_transcription(
    mock_captions, mock_existing, mock_gpu, session
):
    """After handler, content status changed from 'pending' to 'done' in DB."""
    thinker = await create_thinker(session)
    source = await create_source(
        session, thinker_id=thinker.id, source_type="youtube_channel"
    )
    content = await create_content(
        session,
        source_id=source.id,
        source_owner_id=thinker.id,
        status="pending",
    )
    job = await create_job(
        session,
        job_type="process_content",
        payload={"content_id": str(content.id)},
    )
    await session.commit()

    assert content.status == "pending"

    mock_captions.return_value = LONG_TRANSCRIPT

    await handle_process_content(session, job)

    await session.refresh(content)
    assert content.status == "done"


@patch("src.thinktank.handlers.process_content.transcribe_via_gpu", new_callable=AsyncMock)
@patch("src.thinktank.handlers.process_content.fetch_existing_transcript", new_callable=AsyncMock)
@patch("src.thinktank.handlers.process_content.extract_youtube_captions")
async def test_word_count_calculated(
    mock_captions, mock_existing, mock_gpu, session
):
    """After handler, word_count matches actual word count of transcript."""
    thinker = await create_thinker(session)
    source = await create_source(
        session, thinker_id=thinker.id, source_type="youtube_channel"
    )
    content = await create_content(
        session,
        source_id=source.id,
        source_owner_id=thinker.id,
        status="pending",
    )
    job = await create_job(
        session,
        job_type="process_content",
        payload={"content_id": str(content.id)},
    )
    await session.commit()

    transcript = "The quick brown fox jumped over the lazy sleeping dog"
    mock_captions.return_value = " ".join([transcript] * 20)  # Ensure > 100 words

    await handle_process_content(session, job)

    await session.refresh(content)
    expected_count = len(content.body_text.split())
    assert content.word_count == expected_count


@patch("src.thinktank.handlers.process_content.transcribe_via_gpu", new_callable=AsyncMock)
@patch("src.thinktank.handlers.process_content.fetch_existing_transcript", new_callable=AsyncMock)
@patch("src.thinktank.handlers.process_content.extract_youtube_captions")
async def test_all_passes_fail_raises(
    mock_captions, mock_existing, mock_gpu, session
):
    """All passes fail -> handler raises RuntimeError."""
    thinker = await create_thinker(session)
    source = await create_source(
        session, thinker_id=thinker.id, source_type="youtube_channel"
    )
    content = await create_content(
        session,
        source_id=source.id,
        source_owner_id=thinker.id,
        status="pending",
    )
    job = await create_job(
        session,
        job_type="process_content",
        payload={"content_id": str(content.id)},
    )
    await session.commit()

    mock_captions.return_value = None
    mock_existing.return_value = None
    mock_gpu.return_value = None

    with pytest.raises(RuntimeError, match="All transcription passes failed"):
        await handle_process_content(session, job)
