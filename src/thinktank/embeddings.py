"""Client for the local inference service's /embed endpoint.

Embeddings run on the Mac Studio's native service (same host:port as
/transcribe -- GPU_WORKER_URL), so embedding-dependent jobs are routed to
the Mac worker via WORKER_JOB_TYPES exactly like transcription. Zero
marginal cost, no external vendor.
"""

from __future__ import annotations

import os

import httpx
import structlog

from thinktank.http_utils import raise_for_status_with_backoff

logger = structlog.get_logger(__name__)

_DEFAULT_URL = "http://worker-gpu.railway.internal:8000"
_TIMEOUT = 300.0
# Server caps at 256; stay under it and keep request bodies modest.
BATCH_SIZE = 128


async def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed texts via the inference service, batching as needed.

    Returns vectors in input order.

    Raises:
        RuntimeError: On HTTP or shape errors (callers treat embedding
        as required -- an embed job should retry, not silently store
        chunks without vectors).
    """
    base = os.environ.get("GPU_WORKER_URL", _DEFAULT_URL).rstrip("/")
    vectors: list[list[float]] = []
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        for i in range(0, len(texts), BATCH_SIZE):
            batch = texts[i : i + BATCH_SIZE]
            resp = await client.post(f"{base}/embed", json={"texts": batch})
            raise_for_status_with_backoff(resp)
            body = resp.json()
            got = body.get("embeddings")
            if not isinstance(got, list) or len(got) != len(batch):
                raise RuntimeError(f"embed service returned {len(got or [])} vectors for {len(batch)} texts")
            vectors.extend(got)
    return vectors
