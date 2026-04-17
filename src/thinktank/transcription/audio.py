"""Audio download and ffmpeg conversion with guaranteed cleanup.

Spec reference: Sections 7.3, TRANS-03, TRANS-05.
- download_audio: Sync function using yt-dlp Python API.
- convert_to_wav: Async function using ffmpeg subprocess (16kHz mono WAV).
- transcribe_via_gpu: Orchestrates download + convert + GPU call with
  guaranteed cleanup in finally block.
"""

import asyncio
import os
import tempfile
from collections.abc import Callable
from uuid import uuid4

import structlog
from yt_dlp import YoutubeDL

logger = structlog.get_logger(__name__)

# Timeout for ffmpeg conversion (5 minutes)
_FFMPEG_TIMEOUT_SECONDS = 300


def download_audio(url: str, tmp_dir: str) -> str:
    """Download audio from URL via yt-dlp.

    Args:
        url: Content URL (YouTube, podcast, etc.).
        tmp_dir: Directory for temporary audio files.

    Returns:
        Path to the downloaded audio file.

    Raises:
        RuntimeError: If audio download fails.
    """
    file_id = uuid4().hex[:12]
    output_template = os.path.join(tmp_dir, f"audio_{file_id}.%(ext)s")

    opts = {
        "format": "bestaudio/best",
        "outtmpl": output_template,
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "wav",
            }
        ],
        "quiet": True,
        "no_warnings": True,
    }

    try:
        with YoutubeDL(opts) as ydl:
            ydl.download([url])
    except Exception as exc:
        raise RuntimeError(f"audio download failed for {url}: {exc}") from exc

    # yt-dlp may change extension; find the actual output file
    base_path = os.path.join(tmp_dir, f"audio_{file_id}")
    for ext in ["wav", "opus", "m4a", "mp3", "webm", "ogg", "aac"]:
        candidate = f"{base_path}.{ext}"
        if os.path.exists(candidate):
            logger.info("audio_downloaded", url=url, path=candidate)
            return candidate

    raise RuntimeError(f"audio download produced no file for {url}")


async def convert_to_wav(input_path: str, tmp_dir: str) -> str:
    """Convert audio to 16kHz mono WAV for Parakeet.

    Uses asyncio subprocess to avoid blocking the event loop.

    Args:
        input_path: Path to the input audio file.
        tmp_dir: Directory for temporary files.

    Returns:
        Path to the converted WAV file.

    Raises:
        RuntimeError: If conversion fails or times out.
    """
    file_id = uuid4().hex[:12]
    output_path = os.path.join(tmp_dir, f"converted_{file_id}.wav")

    proc = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-i",
        input_path,
        "-ar",
        "16000",
        "-ac",
        "1",
        "-acodec",
        "pcm_s16le",
        "-y",
        output_path,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )

    try:
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=_FFMPEG_TIMEOUT_SECONDS)
    except TimeoutError as err:
        proc.kill()
        raise RuntimeError(f"ffmpeg conversion timeout after {_FFMPEG_TIMEOUT_SECONDS}s for {input_path}") from err

    if proc.returncode != 0:
        error_msg = stderr.decode()[:500] if stderr else "unknown error"
        raise RuntimeError(f"ffmpeg conversion failed: {error_msg}")

    logger.info("audio_converted", input=input_path, output=output_path)
    return output_path


async def transcribe_via_gpu(
    content_url: str,
    tmp_dir: str | None,
    gpu_client_fn: Callable,
) -> str:
    """Orchestrate audio download, conversion, and GPU transcription.

    Guarantees cleanup of all temp files in finally block (TRANS-05).

    Args:
        content_url: URL of the content to transcribe.
        tmp_dir: Directory for temporary files. Defaults to system temp dir.
        gpu_client_fn: Callable that accepts a WAV path and returns transcript text.

    Returns:
        Transcript text from GPU service.

    Raises:
        RuntimeError: If any step fails. Temp files are still cleaned up.
    """
    if tmp_dir is None:
        tmp_dir = os.environ.get("AUDIO_TMP_DIR", tempfile.gettempdir())

    audio_path: str | None = None
    wav_path: str | None = None

    try:
        # Step 1: Download audio via yt-dlp
        audio_path = download_audio(content_url, tmp_dir)

        # Step 2: Convert to 16kHz mono WAV via ffmpeg
        wav_path = await convert_to_wav(audio_path, tmp_dir)

        # Step 3: Send to GPU worker
        transcript = await gpu_client_fn(wav_path)

        logger.info(
            "gpu_transcription_complete",
            url=content_url,
            word_count=len(transcript.split()) if transcript else 0,
        )
        return transcript

    finally:
        # TRANS-05: Audio never persisted -- cleanup on both success and failure
        for path in [audio_path, wav_path]:
            if path and os.path.exists(path):
                try:
                    os.unlink(path)
                    logger.debug("temp_file_cleaned", path=path)
                except OSError:
                    logger.warning("temp_file_cleanup_failed", path=path, exc_info=True)
