"""Parakeet TDT 1.1B model singleton loader for GPU worker.

Loads the model once into VRAM at startup and reuses across all
transcription requests. The model takes 2-5 minutes to load, so
singleton pattern is critical.

Spec reference: Section 7.3 (TRANS-02).
"""

import time

import structlog

logger = structlog.get_logger(__name__)

# Module-level singleton -- loaded once, persisted in VRAM
_model = None


def load_model():
    """Load Parakeet TDT 1.1B model (singleton).

    Uses lazy import of nemo to avoid import errors on CPU machines.
    The model is set to eval mode for inference.

    Returns:
        The loaded NeMo ASR model.
    """
    global _model
    if _model is not None:
        return _model

    start = time.monotonic()
    logger.info("loading_parakeet_model", model="nvidia/parakeet-tdt-1.1b")

    # Lazy import to avoid errors on CPU-only machines
    import nemo.collections.asr as nemo_asr

    _model = nemo_asr.models.EncDecRNNTBPEModel.from_pretrained(model_name="nvidia/parakeet-tdt-1.1b")
    _model.eval()

    elapsed = time.monotonic() - start
    logger.info("parakeet_model_loaded", elapsed_seconds=round(elapsed, 2))
    return _model


def transcribe_audio(wav_path: str) -> str:
    """Transcribe a 16kHz mono WAV file using the Parakeet model.

    Args:
        wav_path: Path to the WAV file.

    Returns:
        Transcribed text.
    """
    start = time.monotonic()
    model = load_model()
    output = model.transcribe([wav_path])
    text = output[0].text

    elapsed = time.monotonic() - start
    logger.info(
        "audio_transcribed",
        wav_path=wav_path,
        word_count=len(text.split()),
        elapsed_seconds=round(elapsed, 2),
    )
    return text
