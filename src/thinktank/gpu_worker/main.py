"""GPU worker FastAPI service with /transcribe and /health endpoints.

Loads the Parakeet TDT 1.1B model at startup (via lifespan) and
serves transcription requests via multipart WAV upload.

Spec reference: Section 7.3 (TRANS-02).

Usage:
    uvicorn thinktank.gpu_worker.main:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import os
import tempfile
from contextlib import asynccontextmanager
from uuid import uuid4

import structlog
from fastapi import FastAPI, File, UploadFile
from fastapi.responses import JSONResponse

from thinktank.gpu_worker import model as _model_module
from thinktank.gpu_worker.model import load_model, transcribe_audio

logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Preload Parakeet model into VRAM on startup."""
    logger.info("gpu_worker_starting", event="model_preload")
    load_model()
    logger.info("gpu_worker_ready")
    yield
    logger.info("gpu_worker_shutting_down")


app = FastAPI(title="ThinkTank GPU Worker", lifespan=lifespan)


@app.post("/transcribe")
async def transcribe(file: UploadFile = File(...)) -> dict:
    """Transcribe a WAV file using Parakeet TDT 1.1B.

    Accepts multipart file upload. Saves to temp file, transcribes,
    cleans up, and returns the transcript text.

    Args:
        file: WAV file upload.

    Returns:
        {"text": "transcribed text"}
    """
    tmp_path = None
    try:
        # Save uploaded file to temp WAV
        file_id = uuid4().hex[:12]
        tmp_dir = os.environ.get("AUDIO_TMP_DIR", tempfile.gettempdir())
        tmp_path = os.path.join(tmp_dir, f"gpu_upload_{file_id}.wav")

        content = await file.read()
        with open(tmp_path, "wb") as f:
            f.write(content)

        logger.info("transcribe_request", filename=file.filename, size_bytes=len(content))

        # Transcribe
        text = transcribe_audio(tmp_path)
        return {"text": text}

    except Exception as exc:
        logger.error("transcribe_failed", error=str(exc), exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"error": str(exc)},
        )

    finally:
        # Clean up temp file
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                logger.warning("temp_cleanup_failed", path=tmp_path, exc_info=True)


@app.get("/health")
async def health() -> dict:
    """Health check endpoint.

    Returns model load status and GPU availability.
    """
    gpu_available = False
    try:
        import torch

        gpu_available = torch.cuda.is_available()
    except ImportError:
        pass

    return {
        "status": "ok",
        "model_loaded": _model_module._model is not None,
        "gpu_available": gpu_available,
    }
