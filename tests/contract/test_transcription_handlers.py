"""Contract test for process_content handler.

Verifies the complete side-effect contract: given input state
(pending content + source + job), handler produces expected
output state (body_text, word_count, transcription_method,
status='done', processed_at).
"""

from unittest.mock import AsyncMock, patch

from tests.factories import create_content, create_job, create_source, create_thinker
from thinktank.handlers.process_content import handle_process_content

LONG_TRANSCRIPT = " ".join(f"word{i}" for i in range(250))


@patch("thinktank.handlers.process_content.transcribe_via_gpu", new_callable=AsyncMock)
@patch("thinktank.handlers.process_content.fetch_existing_transcript", new_callable=AsyncMock)
@patch("thinktank.handlers.process_content.extract_youtube_captions", new_callable=AsyncMock)
async def test_process_content_contract(mock_captions, mock_existing, mock_gpu, session):
    """Contract: pending content -> done content with all fields populated.

    Input state:
        - Content with status='pending', no body_text, no word_count
        - Source with youtube_channel type
        - Job with payload containing content_id

    Expected output state (side effects):
        - content.body_text: non-empty string (the transcript)
        - content.word_count: positive integer matching actual word count
        - content.transcription_method: one of 'youtube_captions', 'existing_transcript', 'parakeet'
        - content.status: 'done'
        - content.processed_at: non-null datetime
    """
    # Setup: create complete object graph in DB
    thinker = await create_thinker(session)
    source = await create_source(session, thinker_id=thinker.id, source_type="youtube_channel")
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

    # Pre-conditions
    assert content.status == "pending"
    assert content.body_text is None
    assert content.word_count is None
    assert content.transcription_method is None
    assert content.processed_at is None

    # Mock: captions succeed
    mock_captions.return_value = LONG_TRANSCRIPT

    # Execute
    await handle_process_content(session, job)

    # Verify complete side-effect contract
    await session.refresh(content)

    # body_text: non-empty transcript string
    assert content.body_text is not None
    assert len(content.body_text) > 0
    assert content.body_text == LONG_TRANSCRIPT

    # word_count: positive integer matching actual word count
    assert content.word_count is not None
    assert content.word_count > 0
    assert content.word_count == len(LONG_TRANSCRIPT.split())

    # transcription_method: valid method string
    assert content.transcription_method in ("youtube_captions", "existing_transcript", "parakeet")
    assert content.transcription_method == "youtube_captions"

    # status: transitioned from 'pending' to 'done'
    assert content.status == "done"

    # processed_at: non-null datetime
    assert content.processed_at is not None
