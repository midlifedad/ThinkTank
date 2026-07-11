"""Unit tests for the AssemblyAI batch transcription pass.

All httpx calls mocked -- no external I/O. Polling sleeps are patched out.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from thinktank.transcription.assemblyai import (
    _format_utterances,
    is_transcription_api_enabled,
    transcribe_via_assemblyai,
)


def _session_with_config_value(value):
    """Mock session whose system_config lookup returns ``value``."""
    session = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=value)
    session.execute = AsyncMock(return_value=result)
    return session


class TestEnabledFlag:
    @pytest.mark.asyncio
    async def test_missing_config_defaults_off(self):
        """Opt-in: no config row means the paid API never fires."""
        assert await is_transcription_api_enabled(_session_with_config_value(None)) is False

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            (True, True),
            (False, False),
            ({"value": True}, True),
            ({"value": False}, False),
            ("true", True),
            ("on", True),
            ("false", False),
            ("0", False),
        ],
    )
    async def test_jsonb_shape_coercion(self, raw, expected):
        assert await is_transcription_api_enabled(_session_with_config_value(raw)) is expected


class TestFormatUtterances:
    def test_speaker_prefixed_lines(self):
        utterances = [
            {"speaker": "A", "text": "Welcome to the show."},
            {"speaker": "B", "text": "Thanks for having me."},
            {"speaker": "A", "text": ""},  # empty text dropped
        ]
        out = _format_utterances(utterances)
        assert out == "Speaker A: Welcome to the show.\nSpeaker B: Thanks for having me."


def _mock_httpx(submit_json, poll_jsons):
    """Patch httpx.AsyncClient: one POST response, sequential GET responses."""

    def _resp(payload):
        r = MagicMock()
        r.status_code = 200
        r.json = MagicMock(return_value=payload)
        r.headers = {}
        return r

    client = AsyncMock()
    client.post = AsyncMock(return_value=_resp(submit_json))
    client.get = AsyncMock(side_effect=[_resp(p) for p in poll_jsons])
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    cls = MagicMock(return_value=client)
    return cls, client


COMPLETED = {
    "status": "completed",
    "audio_duration": 3600,
    "text": "plain fallback text",
    "utterances": [
        {"speaker": "A", "text": "Hello world."},
        {"speaker": "B", "text": "Hi there."},
    ],
}


class TestTranscribeViaAssemblyai:
    @pytest.mark.asyncio
    async def test_happy_path_diarized(self):
        session = AsyncMock()
        session.add = MagicMock()
        cls, client = _mock_httpx({"id": "t1"}, [{"status": "processing"}, COMPLETED])

        with (
            patch("thinktank.transcription.assemblyai.httpx.AsyncClient", cls),
            patch("thinktank.transcription.assemblyai.get_secret", new_callable=AsyncMock, return_value="key123"),
            patch("thinktank.transcription.assemblyai.asyncio.sleep", new_callable=AsyncMock),
        ):
            text = await transcribe_via_assemblyai(session, "https://cdn.pod/ep.mp3", keyterms=["Jane Doe"])

        assert text == "Speaker A: Hello world.\nSpeaker B: Hi there."

        # Raw key, no Bearer prefix (AssemblyAI 401s on Bearer).
        submit_kwargs = client.post.call_args.kwargs
        assert submit_kwargs["headers"] == {"authorization": "key123"}
        # Explicit flagship-first fallback list + diarization + keyterms.
        assert submit_kwargs["json"]["speech_models"] == ["universal-3-5-pro", "universal-2"]
        assert submit_kwargs["json"]["speaker_labels"] is True
        assert submit_kwargs["json"]["keyterms_prompt"] == ["Jane Doe"]

        # A2 cost accounting: 1 audio-hour at the configured rate.
        usage_row = session.add.call_args.args[0]
        assert usage_row.api_name == "assemblyai"
        assert usage_row.units_consumed == 3600
        assert float(usage_row.estimated_cost_usd) == pytest.approx(0.23)

    @pytest.mark.asyncio
    async def test_error_status_returns_none(self):
        session = AsyncMock()
        session.add = MagicMock()
        cls, _ = _mock_httpx({"id": "t1"}, [{"status": "error", "error": "download failed"}])

        with (
            patch("thinktank.transcription.assemblyai.httpx.AsyncClient", cls),
            patch("thinktank.transcription.assemblyai.get_secret", new_callable=AsyncMock, return_value="key123"),
            patch("thinktank.transcription.assemblyai.asyncio.sleep", new_callable=AsyncMock),
        ):
            assert await transcribe_via_assemblyai(session, "https://cdn.pod/ep.mp3") is None
        session.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_missing_key_returns_none(self):
        session = AsyncMock()
        with patch("thinktank.transcription.assemblyai.get_secret", new_callable=AsyncMock, return_value=None):
            assert await transcribe_via_assemblyai(session, "https://cdn.pod/ep.mp3") is None

    @pytest.mark.asyncio
    async def test_network_error_returns_none(self):
        session = AsyncMock()
        session.add = MagicMock()
        client = AsyncMock()
        client.post = AsyncMock(side_effect=RuntimeError("connection refused"))
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("thinktank.transcription.assemblyai.httpx.AsyncClient", MagicMock(return_value=client)),
            patch("thinktank.transcription.assemblyai.get_secret", new_callable=AsyncMock, return_value="key123"),
        ):
            assert await transcribe_via_assemblyai(session, "https://cdn.pod/ep.mp3") is None

    @pytest.mark.asyncio
    async def test_plain_text_fallback_without_utterances(self):
        session = AsyncMock()
        session.add = MagicMock()
        completed = dict(COMPLETED, utterances=[])
        cls, _ = _mock_httpx({"id": "t1"}, [completed])

        with (
            patch("thinktank.transcription.assemblyai.httpx.AsyncClient", cls),
            patch("thinktank.transcription.assemblyai.get_secret", new_callable=AsyncMock, return_value="key123"),
            patch("thinktank.transcription.assemblyai.asyncio.sleep", new_callable=AsyncMock),
        ):
            text = await transcribe_via_assemblyai(session, "https://cdn.pod/ep.mp3")

        assert text == "plain fallback text"
