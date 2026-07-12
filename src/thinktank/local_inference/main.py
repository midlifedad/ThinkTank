"""Local inference FastAPI service: /transcribe and /health.

Same HTTP contract as gpu_worker/main.py (multipart WAV in, transcript
text out), so transcription/gpu_client.py talks to either service
unchanged -- the Mac worker just points GPU_WORKER_URL at this one.
Unlike the Railway service, output is DIARIZED (speaker-prefixed lines).

Usage:
    uvicorn thinktank.local_inference.main:app --host 127.0.0.1 --port 8765
"""

from __future__ import annotations

import os
import tempfile
from contextlib import asynccontextmanager
from uuid import uuid4

import anyio
import structlog
from fastapi import FastAPI, File, UploadFile
from fastapi.responses import JSONResponse

from thinktank.local_inference import engine

logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Preload both models so /health only goes green when serving-ready."""
    logger.info("local_inference_starting", stage="model_preload")
    engine.load_models()
    logger.info("local_inference_ready")
    yield
    logger.info("local_inference_shutting_down")


app = FastAPI(title="ThinkTank Local Inference (Apple Silicon)", lifespan=lifespan)


@app.post("/transcribe")
async def transcribe(file: UploadFile = File(...)) -> JSONResponse:
    """Transcribe an uploaded WAV with diarization.

    The multipart field is named ``file`` to match gpu_client's request
    (and gpu_worker's signature). Response shape mirrors gpu_worker:
    {"text": ...} on success, {"error": ...} with 500 on failure.
    """
    tmp_path = os.path.join(tempfile.gettempdir(), f"local-inference-{uuid4().hex}.wav")
    try:
        with open(tmp_path, "wb") as tmp:
            while chunk := await file.read(1024 * 1024):
                tmp.write(chunk)

        # Inference is synchronous (MLX/torch); run it off the event loop so
        # /health stays responsive during multi-minute episodes.
        text = await anyio.to_thread.run_sync(engine.transcribe_diarized, tmp_path)
        return JSONResponse({"text": text})
    except Exception as exc:
        logger.exception("local_transcription_failed")
        return JSONResponse({"error": str(exc)}, status_code=500)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "models_loaded": engine.models_loaded(), "backend": "parakeet-mlx+pyannote"}
