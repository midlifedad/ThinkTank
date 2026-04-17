"""HTTP client for GPU worker transcription service.

Sends WAV files to the GPU worker's /transcribe endpoint via multipart POST.
Handles chunking for audio files > 60 minutes (splits into 45-min segments).

Spec reference: Section 7.3 (Parakeet via GPU worker).
"""

import asyncio
import os

import httpx
import structlog

logger = structlog.get_logger(__name__)

# Default GPU worker URL (Railway internal networking)
_DEFAULT_GPU_URL = "http://worker-gpu.railway.internal:8000"

# Timeout for GPU transcription (10 minutes for long audio)
_GPU_TIMEOUT_SECONDS = 600

# Chunking thresholds
_LONG_AUDIO_THRESHOLD_SECONDS = 3600  # 60 minutes
_CHUNK_DURATION_SECONDS = 2700  # 45 minutes


async def send_to_gpu(
    wav_path: str,
    gpu_url: str | None = None,
) -> str:
    """Send WAV file to GPU worker for transcription.

    Args:
        wav_path: Path to the 16kHz mono WAV file.
        gpu_url: GPU worker URL. Defaults to env var or internal Railway URL.

    Returns:
        Transcript text from GPU service.

    Raises:
        RuntimeError: On timeout, HTTP error, or invalid response.
    """
    if gpu_url is None:
        gpu_url = os.environ.get("GPU_WORKER_URL", _DEFAULT_GPU_URL)

    transcribe_url = f"{gpu_url.rstrip('/')}/transcribe"

    try:
        async with httpx.AsyncClient(timeout=_GPU_TIMEOUT_SECONDS, follow_redirects=True) as client:
            with open(wav_path, "rb") as f:
                response = await client.post(
                    transcribe_url,
                    files={"file": (os.path.basename(wav_path), f, "audio/wav")},
                )
            response.raise_for_status()
            data = response.json()
            text = data.get("text", "")
            logger.info(
                "gpu_transcription_received",
                wav_path=wav_path,
                word_count=len(text.split()),
            )
            return text

    except httpx.TimeoutException as exc:
        raise RuntimeError(f"GPU transcription timeout for {wav_path}: {exc}") from exc
    except httpx.HTTPStatusError as exc:
        raise RuntimeError(f"GPU transcription HTTP error for {wav_path}: {exc.response.status_code}") from exc
    except Exception as exc:
        raise RuntimeError(f"GPU transcription failed for {wav_path}: {exc}") from exc


async def transcribe_with_chunking(
    wav_path: str,
    duration_seconds: int | None,
    gpu_url: str | None = None,
) -> str:
    """Transcribe audio, chunking if > 60 minutes.

    Spec: "Files > 60 min chunked into 45-min segments."

    Args:
        wav_path: Path to the WAV file.
        duration_seconds: Duration of the audio in seconds.
        gpu_url: GPU worker URL.

    Returns:
        Concatenated transcript text.
    """
    if gpu_url is None:
        gpu_url = os.environ.get("GPU_WORKER_URL", _DEFAULT_GPU_URL)

    # Short audio: send directly
    if not duration_seconds or duration_seconds <= _LONG_AUDIO_THRESHOLD_SECONDS:
        return await send_to_gpu(wav_path, gpu_url)

    # Long audio: split into chunks
    logger.info(
        "chunking_long_audio",
        wav_path=wav_path,
        duration_seconds=duration_seconds,
        chunk_duration=_CHUNK_DURATION_SECONDS,
    )

    chunk_paths: list[str] = []
    try:
        chunk_paths = await _split_audio(wav_path, _CHUNK_DURATION_SECONDS)

        # Transcribe each chunk
        transcripts: list[str] = []
        for i, chunk_path in enumerate(chunk_paths):
            logger.info("transcribing_chunk", chunk=i, total=len(chunk_paths))
            text = await send_to_gpu(chunk_path, gpu_url)
            transcripts.append(text)

        result = " ".join(transcripts)
        logger.info(
            "chunked_transcription_complete",
            chunks=len(chunk_paths),
            total_words=len(result.split()),
        )
        return result

    finally:
        # Clean up chunk files
        for path in chunk_paths:
            if path and os.path.exists(path):
                try:
                    os.unlink(path)
                except OSError:
                    logger.warning("chunk_cleanup_failed", path=path, exc_info=True)


async def _split_audio(wav_path: str, chunk_seconds: int) -> list[str]:
    """Split a WAV file into chunks of the specified duration using ffmpeg.

    Args:
        wav_path: Path to the input WAV file.
        chunk_seconds: Duration of each chunk in seconds.

    Returns:
        List of paths to chunk files.
    """
    base_name = os.path.splitext(wav_path)[0]
    output_pattern = f"{base_name}_chunk_%03d.wav"

    proc = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-i",
        wav_path,
        "-f",
        "segment",
        "-segment_time",
        str(chunk_seconds),
        "-c",
        "copy",
        "-y",
        output_pattern,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )

    _, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)

    if proc.returncode != 0:
        error_msg = stderr.decode()[:500] if stderr else "unknown error"
        raise RuntimeError(f"ffmpeg split failed: {error_msg}")

    # Collect chunk files (sorted)
    chunk_dir = os.path.dirname(wav_path)
    chunk_base = os.path.basename(base_name)
    chunks = sorted(
        os.path.join(chunk_dir, f)
        for f in os.listdir(chunk_dir)
        if f.startswith(f"{chunk_base}_chunk_") and f.endswith(".wav")
    )

    logger.info("audio_split_complete", chunks=len(chunks))
    return chunks
