"""Parakeet TDT 1.1B model singleton loader for the inference service.

Loads the model once at startup and reuses it across all transcription
requests. The model takes minutes to load, so the singleton pattern is
critical. Device selection is explicit: CUDA when a GPU runtime exists,
CPU otherwise -- Railway has no GPU offering (confirmed 2026-07-10), so
production currently runs CPU-mode (~real-time for the 1.1B transducer).

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

    # Lazy imports to avoid errors on machines without the NVIDIA stack
    import nemo.collections.asr as nemo_asr
    import torch

    # Explicit device selection. With the Railway CUDA stub, dlopen of
    # libcuda.so.1 succeeds (so transformer_engine imports) but cuInit
    # fails -- torch.cuda.is_available() is False and we map to CPU.
    device = "cuda" if torch.cuda.is_available() else "cpu"
    logger.info("loading_parakeet_model", model="nvidia/parakeet-tdt-1.1b", device=device)

    _model = nemo_asr.models.EncDecRNNTBPEModel.from_pretrained(
        model_name="nvidia/parakeet-tdt-1.1b",
        map_location=device,
    )
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
