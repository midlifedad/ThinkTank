"""AssemblyAI batch transcription with speaker diarization (optional pass).

Amir-approved 2026-07-11: AssemblyAI as the optional batch processor so the
backlog can transcribe in parallel instead of serializing through the
CPU-mode Parakeet service. Config-gated via system_config
``transcription_api_enabled`` (default OFF); when disabled or on any error
the caller falls through to the GPU pass, so this can never make things
worse than the status quo.

API contract (per AssemblyAI's official agent instructions, 2026-07):
- Auth header is the RAW key -- no ``Bearer`` prefix.
- ``speech_models`` is an ordered fallback list; pass it explicitly to get
  the flagship (``universal-3-5-pro`` -> ``universal-2``).
- Submit with a public ``audio_url`` (podcast enclosure URLs qualify; no
  upload step). Poll GET /v2/transcript/{id} until completed/error.
- ``speaker_labels: true`` returns ``utterances`` with per-speaker turns.
- ``keyterms_prompt`` biases recognition toward exact terms -- we pass the
  episode's matched thinker names, since name spelling is what downstream
  knowledge extraction keys on.

Cost accounting (A2): each completed transcript writes an api_usage row
(api_name='assemblyai', endpoint='transcript') priced from the returned
``audio_duration`` at ``Settings.assemblyai_cost_per_hour``.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

import httpx
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from thinktank.config import get_settings
from thinktank.http_utils import raise_for_status_with_backoff
from thinktank.models.api_usage import ApiUsage
from thinktank.models.config_table import SystemConfig
from thinktank.secrets import get_secret

logger = structlog.get_logger(__name__)

_BASE_URL = "https://api.assemblyai.com/v2"
# Ordered model-availability fallback (explicit, else the API defaults to
# the previous generation).
_SPEECH_MODELS = ["universal-3-5-pro", "universal-2"]
_POLL_INTERVAL_SECONDS = 10.0
# AssemblyAI turns long audio around at ~15-30x real-time; an hour covers
# even 10-hour uploads with margin. On timeout we return None and the GPU
# pass takes over (the remote job simply completes unobserved).
_MAX_POLL_SECONDS = 3600.0
_HTTP_TIMEOUT = 30.0
# Pre-recorded keyterms_prompt accepts up to 1,000 phrases of <=6 words.
_MAX_KEYTERMS = 1000


async def is_transcription_api_enabled(session: AsyncSession) -> bool:
    """Read the transcription_api_enabled flag from system_config.

    Default OFF (unlike the kill switch this is opt-in): a fresh deployment
    must never start spending on an external API silently. Handles the same
    JSONB shapes as the kill switch (raw bool, {"value": ...}, operator
    strings like "false").
    """
    stmt = select(SystemConfig.value).where(SystemConfig.key == "transcription_api_enabled")
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()

    if row is None:
        return False

    value = row.get("value", False) if isinstance(row, dict) else row
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes", "on")
    return bool(value)


def _format_utterances(utterances: list[dict]) -> str:
    """Render diarized utterances as speaker-prefixed lines.

    content.body_text is plain text, so speaker turns are serialized as
    "Speaker A: ..." lines -- lossy for structure but preserves attribution
    for downstream extraction. Structured storage is a v2 concern.
    """
    lines = []
    for utt in utterances:
        speaker = utt.get("speaker") or "?"
        text = (utt.get("text") or "").strip()
        if text:
            lines.append(f"Speaker {speaker}: {text}")
    return "\n".join(lines)


async def transcribe_via_assemblyai(
    session: AsyncSession,
    audio_url: str,
    keyterms: list[str] | None = None,
) -> str | None:
    """Submit a public audio URL for diarized batch transcription.

    Args:
        session: Database session (API key lookup + cost recording).
        audio_url: Publicly fetchable audio URL (podcast enclosure).
        keyterms: Exact terms to bias recognition toward (thinker names).

    Returns:
        Speaker-prefixed transcript text, or None on any failure (caller
        falls through to the next pass).
    """
    api_key = await get_secret(session, "assemblyai_api_key")
    if not api_key:
        logger.warning("assemblyai_key_missing", hint="seed secret_assemblyai_api_key in system_config")
        return None

    # Raw key -- AssemblyAI rejects a Bearer prefix with 401.
    headers = {"authorization": api_key}
    payload: dict = {
        "audio_url": audio_url,
        "speech_models": _SPEECH_MODELS,
        "speaker_labels": True,
    }
    if keyterms:
        payload["keyterms_prompt"] = keyterms[:_MAX_KEYTERMS]

    log = logger.bind(audio_url=audio_url)

    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=_HTTP_TIMEOUT) as client:
            submit = await client.post(f"{_BASE_URL}/transcript", headers=headers, json=payload)
            raise_for_status_with_backoff(submit)
            transcript_id = submit.json()["id"]
            log = log.bind(transcript_id=transcript_id)
            log.info("assemblyai_submitted")

            deadline = asyncio.get_event_loop().time() + _MAX_POLL_SECONDS
            while True:
                await asyncio.sleep(_POLL_INTERVAL_SECONDS)
                poll = await client.get(f"{_BASE_URL}/transcript/{transcript_id}", headers=headers)
                raise_for_status_with_backoff(poll)
                body = poll.json()
                status = body.get("status")

                if status == "completed":
                    break
                if status == "error":
                    log.warning("assemblyai_transcript_error", error=body.get("error"))
                    return None
                if asyncio.get_event_loop().time() > deadline:
                    log.warning("assemblyai_poll_timeout", last_status=status)
                    return None
    except Exception:
        # Same posture as the GPU pass: an external-API failure must never
        # fail the job outright while cheaper passes remain.
        log.warning("assemblyai_request_failed", exc_info=True)
        return None

    utterances = body.get("utterances") or []
    text = _format_utterances(utterances) if utterances else (body.get("text") or "")
    if not text:
        log.warning("assemblyai_empty_transcript")
        return None

    # A2 cost accounting: audio_duration is seconds of source audio.
    duration_seconds = body.get("audio_duration") or 0
    settings = get_settings()
    session.add(
        ApiUsage(
            id=uuid.uuid4(),
            api_name="assemblyai",
            endpoint="transcript",
            period_start=datetime.now(UTC),
            call_count=1,
            units_consumed=duration_seconds,
            estimated_cost_usd=(duration_seconds / 3600.0) * settings.assemblyai_cost_per_hour,
        )
    )

    log.info(
        "assemblyai_transcribed",
        audio_seconds=duration_seconds,
        speakers=len({u.get("speaker") for u in utterances}) if utterances else 0,
        word_count=len(text.split()),
    )
    return text
