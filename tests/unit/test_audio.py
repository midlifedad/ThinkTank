"""Unit tests for audio download and ffmpeg conversion.

Spec reference: Section 7.3, TRANS-03, TRANS-05.
yt-dlp, ffmpeg subprocess, and GPU client calls are mocked.
Cleanup tests create real temp files and verify deletion.
"""

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestDownloadAudio:
    """Tests for download_audio function."""

    @patch("thinktank.transcription.audio.YoutubeDL")
    def test_download_audio_success(self, mock_ydl_cls, tmp_path):
        """Successful download returns a path to the audio file."""
        from thinktank.transcription.audio import download_audio

        tmp_dir = str(tmp_path)

        # Capture the opts dict to know what file path to create
        captured_opts = {}

        def init_side_effect(opts):
            captured_opts.update(opts)
            return MagicMock()

        mock_ydl_cls.side_effect = init_side_effect

        # Set up context manager
        mock_instance = MagicMock()
        mock_ydl_cls.return_value = mock_instance
        mock_instance.__enter__ = MagicMock(return_value=mock_instance)
        mock_instance.__exit__ = MagicMock(return_value=False)

        def fake_download(urls):
            # Create the file yt-dlp would produce based on captured outtmpl
            outtmpl = captured_opts.get("outtmpl", "")
            if outtmpl:
                wav_path = outtmpl.replace("%(ext)s", "wav")
                open(wav_path, "w").close()

        # Wire up: init captures opts, enter returns instance, download creates file
        def full_init(opts):
            captured_opts.update(opts)
            inst = MagicMock()
            inst.__enter__ = MagicMock(return_value=inst)
            inst.__exit__ = MagicMock(return_value=False)
            inst.download = MagicMock(side_effect=fake_download)
            return inst

        mock_ydl_cls.side_effect = full_init

        result = download_audio("https://youtube.com/watch?v=test", tmp_dir)

        assert result is not None
        assert os.path.exists(result)
        assert result.endswith(".wav")

    @patch("thinktank.transcription.audio.YoutubeDL")
    def test_download_audio_failure(self, mock_ydl_cls, tmp_path):
        """Raises RuntimeError when yt-dlp raises DownloadError."""
        from thinktank.transcription.audio import download_audio

        def failing_init(opts):
            inst = MagicMock()
            inst.__enter__ = MagicMock(return_value=inst)
            inst.__exit__ = MagicMock(return_value=False)
            inst.download = MagicMock(side_effect=Exception("Download failed: video unavailable"))
            return inst

        mock_ydl_cls.side_effect = failing_init

        with pytest.raises(RuntimeError, match="audio download"):
            download_audio("https://youtube.com/watch?v=bad", str(tmp_path))


class TestConvertToWav:
    """Tests for convert_to_wav async function."""

    @pytest.mark.asyncio
    @patch("thinktank.transcription.audio.asyncio.create_subprocess_exec")
    async def test_convert_to_wav_success(self, mock_create_proc, tmp_path):
        """Successful conversion returns path to .wav file."""
        from thinktank.transcription.audio import convert_to_wav

        # Create a fake input file
        input_path = str(tmp_path / "input.m4a")
        open(input_path, "w").close()

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"", b""))
        mock_proc.returncode = 0
        mock_create_proc.return_value = mock_proc

        result = await convert_to_wav(input_path, str(tmp_path))

        assert result.endswith(".wav")
        # Verify ffmpeg was called with correct args
        call_args = mock_create_proc.call_args
        args = call_args[0]
        assert args[0] == "ffmpeg"
        assert "-ar" in args
        assert "16000" in args
        assert "-ac" in args
        assert "1" in args

    @pytest.mark.asyncio
    @patch("thinktank.transcription.audio.asyncio.create_subprocess_exec")
    async def test_convert_to_wav_failure(self, mock_create_proc, tmp_path):
        """Raises RuntimeError on non-zero ffmpeg return code."""
        from thinktank.transcription.audio import convert_to_wav

        input_path = str(tmp_path / "bad.m4a")
        open(input_path, "w").close()

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"", b"Error: invalid input"))
        mock_proc.returncode = 1
        mock_create_proc.return_value = mock_proc

        with pytest.raises(RuntimeError, match="ffmpeg"):
            await convert_to_wav(input_path, str(tmp_path))

    @pytest.mark.asyncio
    @patch("thinktank.transcription.audio.asyncio.create_subprocess_exec")
    async def test_convert_to_wav_timeout(self, mock_create_proc, tmp_path):
        """Raises RuntimeError on ffmpeg timeout."""
        from thinktank.transcription.audio import convert_to_wav

        input_path = str(tmp_path / "slow.m4a")
        open(input_path, "w").close()

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(side_effect=TimeoutError())
        mock_proc.kill = MagicMock()
        mock_create_proc.return_value = mock_proc

        with pytest.raises(RuntimeError, match="timeout"):
            await convert_to_wav(input_path, str(tmp_path))


class TestTempFileCleanup:
    """Tests for guaranteed temp file cleanup in transcribe_via_gpu."""

    @pytest.mark.asyncio
    async def test_cleanup_on_success(self, tmp_path):
        """Temp files are deleted after successful transcription."""
        from thinktank.transcription.audio import transcribe_via_gpu

        tmp_dir = str(tmp_path)

        # Create real temp files to verify cleanup
        audio_file = tmp_path / "audio_abc123.m4a"
        wav_file = tmp_path / "converted_abc123.wav"
        audio_file.write_text("fake audio")
        wav_file.write_text("fake wav")

        async def fake_gpu_fn(wav_path):
            return "This is the transcript text from GPU."

        with (
            patch("thinktank.transcription.audio.download_audio", return_value=str(audio_file)),
            patch("thinktank.transcription.audio.convert_to_wav", new_callable=AsyncMock, return_value=str(wav_file)),
        ):
            result = await transcribe_via_gpu("https://example.com/video", tmp_dir, fake_gpu_fn)

        assert result == "This is the transcript text from GPU."
        # Verify cleanup
        assert not audio_file.exists(), "Audio file should be deleted after success"
        assert not wav_file.exists(), "WAV file should be deleted after success"

    @pytest.mark.asyncio
    async def test_cleanup_on_failure(self, tmp_path):
        """Temp files are deleted even when GPU transcription fails."""
        from thinktank.transcription.audio import transcribe_via_gpu

        tmp_dir = str(tmp_path)

        audio_file = tmp_path / "audio_def456.m4a"
        wav_file = tmp_path / "converted_def456.wav"
        audio_file.write_text("fake audio")
        wav_file.write_text("fake wav")

        async def failing_gpu_fn(wav_path):
            raise RuntimeError("GPU service unavailable")

        with (
            patch("thinktank.transcription.audio.download_audio", return_value=str(audio_file)),
            patch("thinktank.transcription.audio.convert_to_wav", new_callable=AsyncMock, return_value=str(wav_file)),
            pytest.raises(RuntimeError, match="GPU service"),
        ):
            await transcribe_via_gpu("https://example.com/video", tmp_dir, failing_gpu_fn)

        # Verify cleanup happened even on failure
        assert not audio_file.exists(), "Audio file should be deleted after failure"
        assert not wav_file.exists(), "WAV file should be deleted after failure"
