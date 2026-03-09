"""Unit tests for GPU worker HTTP client.

Spec reference: Section 7.3 (Parakeet via GPU worker).
All httpx calls and file I/O mocked -- no external services.
"""

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest


class TestSendToGpu:
    """Tests for send_to_gpu function."""

    @pytest.mark.asyncio
    @patch("src.thinktank.transcription.gpu_client.httpx.AsyncClient")
    async def test_transcribe_success(self, mock_client_cls, tmp_path):
        """Successful transcription returns text from GPU response."""
        from src.thinktank.transcription.gpu_client import send_to_gpu

        # Create a fake WAV file
        wav_path = str(tmp_path / "test.wav")
        with open(wav_path, "wb") as f:
            f.write(b"fake wav data")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"text": "hello world this is a transcript"}
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        result = await send_to_gpu(wav_path, gpu_url="http://gpu-worker:8000")

        assert result == "hello world this is a transcript"

    @pytest.mark.asyncio
    @patch("src.thinktank.transcription.gpu_client.httpx.AsyncClient")
    async def test_transcribe_timeout(self, mock_client_cls, tmp_path):
        """Raises RuntimeError on timeout."""
        from src.thinktank.transcription.gpu_client import send_to_gpu

        wav_path = str(tmp_path / "test.wav")
        with open(wav_path, "wb") as f:
            f.write(b"fake wav data")

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=httpx.TimeoutException("timed out"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        with pytest.raises(RuntimeError, match="GPU"):
            await send_to_gpu(wav_path, gpu_url="http://gpu-worker:8000")

    @pytest.mark.asyncio
    @patch("src.thinktank.transcription.gpu_client.httpx.AsyncClient")
    async def test_transcribe_gpu_error(self, mock_client_cls, tmp_path):
        """Raises RuntimeError on 500 server error."""
        from src.thinktank.transcription.gpu_client import send_to_gpu

        wav_path = str(tmp_path / "test.wav")
        with open(wav_path, "wb") as f:
            f.write(b"fake wav data")

        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.raise_for_status = MagicMock(
            side_effect=httpx.HTTPStatusError(
                "Server Error", request=MagicMock(), response=mock_response
            )
        )

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        with pytest.raises(RuntimeError, match="GPU"):
            await send_to_gpu(wav_path, gpu_url="http://gpu-worker:8000")

    @pytest.mark.asyncio
    @patch("src.thinktank.transcription.gpu_client.httpx.AsyncClient")
    async def test_transcribe_sends_multipart(self, mock_client_cls, tmp_path):
        """Verify the POST sends multipart file upload with WAV data."""
        from src.thinktank.transcription.gpu_client import send_to_gpu

        wav_path = str(tmp_path / "test.wav")
        with open(wav_path, "wb") as f:
            f.write(b"RIFF fake wav data")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"text": "transcript"}
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        await send_to_gpu(wav_path, gpu_url="http://gpu-worker:8000")

        # Verify multipart file upload
        call_kwargs = mock_client.post.call_args
        assert call_kwargs is not None
        # Should have called with files= or data containing the file
        assert "files" in call_kwargs.kwargs or "files" in (call_kwargs.args[1] if len(call_kwargs.args) > 1 else {})


class TestTranscribeWithChunking:
    """Tests for transcribe_with_chunking (long audio > 60min)."""

    @pytest.mark.asyncio
    @patch("src.thinktank.transcription.gpu_client.send_to_gpu")
    @patch("src.thinktank.transcription.gpu_client.asyncio.create_subprocess_exec")
    async def test_long_audio_chunking(self, mock_create_proc, mock_send, tmp_path):
        """Audio > 60 min is split into 45-min segments and results concatenated."""
        from src.thinktank.transcription.gpu_client import transcribe_with_chunking

        wav_path = str(tmp_path / "long.wav")
        with open(wav_path, "wb") as f:
            f.write(b"fake long wav data")

        # Simulate ffmpeg split producing chunk files
        chunk_0 = str(tmp_path / "long_chunk_000.wav")
        chunk_1 = str(tmp_path / "long_chunk_001.wav")

        def create_chunk_files(*args, **kwargs):
            # Create the chunk files that ffmpeg would produce
            for p in [chunk_0, chunk_1]:
                with open(p, "wb") as f:
                    f.write(b"chunk data")
            mock_proc = AsyncMock()
            mock_proc.communicate = AsyncMock(return_value=(b"", b""))
            mock_proc.returncode = 0
            return mock_proc

        mock_create_proc.side_effect = create_chunk_files

        # Mock send_to_gpu to return different text for each chunk
        mock_send.side_effect = [
            "first chunk transcript",
            "second chunk transcript",
        ]

        result = await transcribe_with_chunking(
            wav_path,
            duration_seconds=4200,  # 70 minutes > 60 min threshold
            gpu_url="http://gpu-worker:8000",
        )

        # Results should be concatenated
        assert "first chunk transcript" in result
        assert "second chunk transcript" in result
        assert mock_send.call_count == 2

    @pytest.mark.asyncio
    @patch("src.thinktank.transcription.gpu_client.send_to_gpu")
    async def test_short_audio_no_chunking(self, mock_send, tmp_path):
        """Audio <= 60 min is sent directly without chunking."""
        from src.thinktank.transcription.gpu_client import transcribe_with_chunking

        wav_path = str(tmp_path / "short.wav")
        with open(wav_path, "wb") as f:
            f.write(b"fake short wav data")

        mock_send.return_value = "short transcript"

        result = await transcribe_with_chunking(
            wav_path,
            duration_seconds=1800,  # 30 minutes <= 60 min
            gpu_url="http://gpu-worker:8000",
        )

        assert result == "short transcript"
        assert mock_send.call_count == 1
