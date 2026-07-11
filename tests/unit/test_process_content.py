"""Unit tests for process_content handler (three-pass transcription orchestrator).

Tests each pass independently and the full fallback chain.
All external dependencies mocked -- no database, no network.
"""

import uuid
from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest

from tests.factories import make_content, make_job, make_source


@pytest.fixture
def content_id():
    return uuid.uuid4()


@pytest.fixture
def source(content_id):
    return make_source(source_type="youtube_channel")


@pytest.fixture
def content(content_id, source):
    return make_content(id=content_id, source_id=source.id, status="pending", url="https://youtube.com/watch?v=test123")


@pytest.fixture
def job(content_id):
    return make_job(job_type="process_content", payload={"content_id": str(content_id)})


@pytest.fixture
def mock_session(content, source):
    """Mock AsyncSession that returns content and source on get()."""
    session = AsyncMock()

    async def mock_get(model_cls, model_id):
        from thinktank.models.content import Content
        from thinktank.models.source import Source

        if model_cls is Content:
            return content
        if model_cls is Source:
            return source
        return None

    session.get = AsyncMock(side_effect=mock_get)
    session.commit = AsyncMock()
    return session


@pytest.fixture(autouse=True)
def mock_api_enabled():
    """Pass 2.5 (AssemblyAI) is opt-in; keep it OFF for pre-existing tests.

    A raw AsyncMock session would coerce to enabled=True and the handler
    would attempt a real API call. Tests that exercise the pass set
    ``mock_api_enabled.return_value = True`` and patch the client.
    """
    with patch("thinktank.handlers.process_content.is_transcription_api_enabled", new_callable=AsyncMock) as enabled:
        enabled.return_value = False
        yield enabled


LONG_TRANSCRIPT = " ".join(f"word{i}" for i in range(200))


@patch("thinktank.handlers.process_content.extract_youtube_captions", new_callable=AsyncMock)
async def test_pass1_youtube_captions(mock_captions, mock_session, job, content, source):
    """Pass 1: YouTube captions succeed -> method='youtube_captions'."""
    mock_captions.return_value = LONG_TRANSCRIPT

    from thinktank.handlers.process_content import handle_process_content

    await handle_process_content(mock_session, job)

    assert content.status == "done"
    assert content.transcription_method == "youtube_captions"
    assert content.body_text == LONG_TRANSCRIPT
    assert content.word_count == 200
    assert content.processed_at is not None
    mock_session.commit.assert_awaited_once()


@patch("thinktank.handlers.process_content.fetch_existing_transcript", new_callable=AsyncMock)
@patch("thinktank.handlers.process_content.extract_youtube_captions", new_callable=AsyncMock)
async def test_pass2_existing_transcript(mock_captions, mock_existing, mock_session, job, content, source):
    """Pass 2: Captions fail, existing transcript succeeds -> method='existing_transcript'."""
    mock_captions.return_value = None
    source.config = {"transcript_url_pattern": "https://example.com/transcripts/{slug}"}
    mock_existing.return_value = LONG_TRANSCRIPT

    from thinktank.handlers.process_content import handle_process_content

    await handle_process_content(mock_session, job)

    assert content.transcription_method == "existing_transcript"
    assert content.status == "done"
    assert content.body_text == LONG_TRANSCRIPT


@patch("thinktank.handlers.process_content.transcribe_via_gpu", new_callable=AsyncMock)
@patch("thinktank.handlers.process_content.fetch_existing_transcript", new_callable=AsyncMock)
@patch("thinktank.handlers.process_content.extract_youtube_captions", new_callable=AsyncMock)
async def test_pass3_parakeet_gpu(mock_captions, mock_existing, mock_gpu, mock_session, job, content, source):
    """Pass 3: Captions + existing fail, GPU succeeds -> method='parakeet'."""
    mock_captions.return_value = None
    mock_existing.return_value = None
    mock_gpu.return_value = LONG_TRANSCRIPT

    from thinktank.handlers.process_content import handle_process_content

    await handle_process_content(mock_session, job)

    assert content.transcription_method == "parakeet"
    assert content.status == "done"
    assert content.body_text == LONG_TRANSCRIPT


@patch("thinktank.handlers.process_content.transcribe_via_gpu", new_callable=AsyncMock)
@patch("thinktank.handlers.process_content.fetch_existing_transcript", new_callable=AsyncMock)
@patch("thinktank.handlers.process_content.extract_youtube_captions", new_callable=AsyncMock)
async def test_all_passes_fail(mock_captions, mock_existing, mock_gpu, mock_session, job, content, source):
    """All three passes return None -> raises RuntimeError."""
    mock_captions.return_value = None
    mock_existing.return_value = None
    mock_gpu.return_value = None

    from thinktank.handlers.process_content import handle_process_content

    with pytest.raises(RuntimeError, match="All transcription passes failed"):
        await handle_process_content(mock_session, job)


@patch("thinktank.handlers.process_content.transcribe_via_gpu", new_callable=AsyncMock)
@patch("thinktank.handlers.process_content.fetch_existing_transcript", new_callable=AsyncMock)
@patch("thinktank.handlers.process_content.extract_youtube_captions", new_callable=AsyncMock)
async def test_pass1_skipped_for_non_youtube(
    mock_captions, mock_existing, mock_gpu, mock_session, job, content, source
):
    """Source type='podcast_rss' -> Pass 1 not attempted, goes to Pass 2/3."""
    source.source_type = "podcast_rss"
    mock_existing.return_value = None
    mock_gpu.return_value = LONG_TRANSCRIPT

    from thinktank.handlers.process_content import handle_process_content

    await handle_process_content(mock_session, job)

    # Captions never called for non-YouTube sources
    mock_captions.assert_not_called()
    assert content.transcription_method == "parakeet"


@patch("thinktank.handlers.process_content.transcribe_via_gpu", new_callable=AsyncMock)
@patch("thinktank.handlers.process_content.fetch_existing_transcript", new_callable=AsyncMock)
@patch("thinktank.handlers.process_content.extract_youtube_captions", new_callable=AsyncMock)
async def test_pass2_skipped_no_pattern(mock_captions, mock_existing, mock_gpu, mock_session, job, content, source):
    """Source has no transcript_url_pattern -> Pass 2 skipped, goes to Pass 3."""
    mock_captions.return_value = None
    source.config = {}  # No transcript_url_pattern
    mock_gpu.return_value = LONG_TRANSCRIPT

    from thinktank.handlers.process_content import handle_process_content

    await handle_process_content(mock_session, job)

    # fetch_existing_transcript never called when no pattern
    mock_existing.assert_not_called()
    assert content.transcription_method == "parakeet"


@patch("thinktank.handlers.process_content.extract_youtube_captions", new_callable=AsyncMock)
async def test_content_fields_updated(mock_captions, mock_session, job, content, source):
    """After successful transcription, verify all content fields are set."""
    mock_captions.return_value = LONG_TRANSCRIPT

    from thinktank.handlers.process_content import handle_process_content

    await handle_process_content(mock_session, job)

    assert content.body_text == LONG_TRANSCRIPT
    assert content.word_count == len(LONG_TRANSCRIPT.split())
    assert content.transcription_method == "youtube_captions"
    assert content.status == "done"
    assert isinstance(content.processed_at, datetime)


def _thinker_rows(mock_session, names):
    """Make session.execute return thinker-name rows for the keyterms query."""
    from unittest.mock import MagicMock

    result = MagicMock()
    result.all = MagicMock(return_value=[(n,) for n in names])
    mock_session.execute = AsyncMock(return_value=result)


@patch("thinktank.handlers.process_content.transcribe_via_assemblyai", new_callable=AsyncMock)
@patch("thinktank.handlers.process_content.transcribe_via_gpu", new_callable=AsyncMock)
@patch("thinktank.handlers.process_content.fetch_existing_transcript", new_callable=AsyncMock)
async def test_pass25_assemblyai_success(
    mock_existing, mock_gpu, mock_aai, mock_api_enabled, mock_session, job, content, source
):
    """Pass 2.5: API enabled, AssemblyAI succeeds -> method='assemblyai', GPU never called."""
    source.source_type = "podcast_rss"
    source.config = {}
    mock_api_enabled.return_value = True
    mock_aai.return_value = LONG_TRANSCRIPT
    _thinker_rows(mock_session, ["Jane Doe", "John Smith"])

    from thinktank.handlers.process_content import handle_process_content

    await handle_process_content(mock_session, job)

    assert content.transcription_method == "assemblyai"
    assert content.status == "done"
    mock_gpu.assert_not_called()
    # Matched thinker names are passed as recognition keyterms.
    assert mock_aai.call_args.kwargs["keyterms"] == ["Jane Doe", "John Smith"]


@patch("thinktank.handlers.process_content.transcribe_via_assemblyai", new_callable=AsyncMock)
@patch("thinktank.handlers.process_content.transcribe_via_gpu", new_callable=AsyncMock)
@patch("thinktank.handlers.process_content.fetch_existing_transcript", new_callable=AsyncMock)
async def test_pass25_disabled_goes_to_gpu(mock_existing, mock_gpu, mock_aai, mock_session, job, content, source):
    """Config flag off (default) -> AssemblyAI never called, GPU pass runs."""
    source.source_type = "podcast_rss"
    source.config = {}
    mock_gpu.return_value = LONG_TRANSCRIPT

    from thinktank.handlers.process_content import handle_process_content

    await handle_process_content(mock_session, job)

    mock_aai.assert_not_called()
    assert content.transcription_method == "parakeet"


@patch("thinktank.handlers.process_content.transcribe_via_assemblyai", new_callable=AsyncMock)
@patch("thinktank.handlers.process_content.transcribe_via_gpu", new_callable=AsyncMock)
@patch("thinktank.handlers.process_content.extract_youtube_captions", new_callable=AsyncMock)
async def test_pass25_skipped_for_youtube(
    mock_captions, mock_gpu, mock_aai, mock_api_enabled, mock_session, job, content, source
):
    """YouTube page URLs aren't direct audio -> AssemblyAI skipped even when enabled."""
    mock_api_enabled.return_value = True
    mock_captions.return_value = None
    mock_gpu.return_value = LONG_TRANSCRIPT

    from thinktank.handlers.process_content import handle_process_content

    await handle_process_content(mock_session, job)

    mock_aai.assert_not_called()
    assert content.transcription_method == "parakeet"


@patch("thinktank.handlers.process_content.transcribe_via_assemblyai", new_callable=AsyncMock)
@patch("thinktank.handlers.process_content.transcribe_via_gpu", new_callable=AsyncMock)
@patch("thinktank.handlers.process_content.fetch_existing_transcript", new_callable=AsyncMock)
async def test_pass25_failure_falls_through_to_gpu(
    mock_existing, mock_gpu, mock_aai, mock_api_enabled, mock_session, job, content, source
):
    """AssemblyAI returns None -> GPU pass still runs (API can't block the chain)."""
    source.source_type = "podcast_rss"
    source.config = {}
    mock_api_enabled.return_value = True
    mock_aai.return_value = None
    mock_gpu.return_value = LONG_TRANSCRIPT
    _thinker_rows(mock_session, [])

    from thinktank.handlers.process_content import handle_process_content

    await handle_process_content(mock_session, job)

    mock_aai.assert_called_once()
    assert content.transcription_method == "parakeet"


async def test_content_not_found_raises(mock_session, job):
    """Job payload with nonexistent content_id -> raises ValueError."""
    # Override session.get to return None for Content
    mock_session.get = AsyncMock(return_value=None)

    from thinktank.handlers.process_content import handle_process_content

    with pytest.raises(ValueError, match="Content .* not found"):
        await handle_process_content(mock_session, job)


async def test_source_missing_raises_valueerror(content, job):
    """If the Source referenced by content was deleted, handler must raise
    a ValueError (which categorize_error maps to PAYLOAD_INVALID -> terminal)
    instead of dereferencing None and throwing AttributeError on an opaque
    retry loop.

    Source: HANDLERS-REVIEW CR-03.
    """
    from thinktank.handlers.process_content import handle_process_content
    from thinktank.models.content import Content
    from thinktank.models.source import Source

    session = AsyncMock()

    async def mock_get(model_cls, _model_id):
        if model_cls is Content:
            return content
        if model_cls is Source:
            return None
        return None

    session.get = AsyncMock(side_effect=mock_get)
    session.commit = AsyncMock()

    with pytest.raises(ValueError, match="Source .* missing"):
        await handle_process_content(session, job)
