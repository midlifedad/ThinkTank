"""Diarized transcription engine: parakeet-mlx ASR + pyannote speakers.

Model loading follows the gpu_worker singleton pattern (load once, reuse
across requests). Heavy imports are lazy so the module imports on machines
without the ML stack (tests mock the loaders).

Output format matches the AssemblyAI pass exactly -- "Speaker A: ..."
lines -- so downstream consumers of content.body_text see one shape
regardless of which backend transcribed the episode.

Env:
    HF_TOKEN                 HuggingFace token (pyannote's diarization
                             pipeline is a gated model).
    PARAKEET_MLX_MODEL       Override the ASR model id.
    PYANNOTE_PIPELINE        Override the diarization pipeline id.
    EMBEDDING_MODEL          Override the sentence-transformers model id.
"""

from __future__ import annotations

import os
import threading
import time

import structlog

logger = structlog.get_logger(__name__)

# MLX and the pyannote pipeline are not guaranteed thread-safe, and two
# concurrent transcriptions would fight over the same Metal device anyway.
# The FastAPI layer runs inference in worker threads (to keep /health
# responsive), so serialize actual inference explicitly: requests queue
# here and the worker's own concurrency just overlaps download with
# inference, which is the useful kind of parallelism.
_inference_lock = threading.Lock()
# Embeddings get their OWN lock: bge runs on CPU (see load_models), so it
# never contends with MLX/pyannote for Metal, and serializing it behind a
# ~6-minute transcription would just time out the /embed client.
_embedding_lock = threading.Lock()

_DEFAULT_ASR_MODEL = "mlx-community/parakeet-tdt-0.6b-v2"
_DEFAULT_DIARIZATION_PIPELINE = "pyannote/speaker-diarization-3.1"
# Must produce vectors matching models/claim.py EMBEDDING_DIM (768).
_DEFAULT_EMBEDDING_MODEL = "BAAI/bge-base-en-v1.5"

# Module-level singletons -- loaded once at service startup.
_asr_model = None
_diarization_pipeline = None
_embedding_model = None

_SPEAKER_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def load_models() -> None:
    """Load ASR + diarization + embedding models (singletons).

    Called from the FastAPI lifespan so the service only reports healthy
    once both models are resident.
    """
    global _asr_model, _diarization_pipeline, _embedding_model

    if _asr_model is None:
        from parakeet_mlx import from_pretrained

        model_id = os.environ.get("PARAKEET_MLX_MODEL", _DEFAULT_ASR_MODEL)
        start = time.monotonic()
        logger.info("loading_asr_model", model=model_id)
        _asr_model = from_pretrained(model_id)
        logger.info("asr_model_loaded", elapsed_seconds=round(time.monotonic() - start, 2))

    if _diarization_pipeline is None:
        import torch
        from pyannote.audio import Pipeline

        pipeline_id = os.environ.get("PYANNOTE_PIPELINE", _DEFAULT_DIARIZATION_PIPELINE)
        token = os.environ.get("HF_TOKEN")
        if not token:
            raise RuntimeError("HF_TOKEN is required: pyannote's diarization pipeline is a gated model")
        start = time.monotonic()
        logger.info("loading_diarization_pipeline", pipeline=pipeline_id)
        # pyannote.audio >=3.4/4.x renamed use_auth_token -> token (tracks
        # huggingface_hub). The old kwarg raises TypeError at startup.
        _diarization_pipeline = Pipeline.from_pretrained(pipeline_id, token=token)
        if torch.backends.mps.is_available():
            _diarization_pipeline.to(torch.device("mps"))
        logger.info(
            "diarization_pipeline_loaded",
            elapsed_seconds=round(time.monotonic() - start, 2),
            device="mps" if torch.backends.mps.is_available() else "cpu",
        )

    if _embedding_model is None:
        from sentence_transformers import SentenceTransformer

        model_id = os.environ.get("EMBEDDING_MODEL", _DEFAULT_EMBEDDING_MODEL)
        # CPU on purpose, not a fallback: bge-base encodes a transcript's
        # chunks in seconds on an M-series CPU, and keeping it off Metal
        # avoids contending with MLX ASR / pyannote MPS. The first live
        # /embed on this host WEDGED inside encode() on MPS, permanently
        # occupying the inference thread (2026-07-13).
        device = os.environ.get("EMBEDDING_DEVICE", "cpu")
        start = time.monotonic()
        logger.info("loading_embedding_model", model=model_id, device=device)
        _embedding_model = SentenceTransformer(model_id, device=device)
        # Warmup encode: a wedge or dtype/device fault must fail the BOOT
        # (KeepAlive relaunches, health stays down, install.sh screams)
        # rather than the first real request -- models_loaded alone proved
        # a liar here.
        _embedding_model.encode(["warmup"], normalize_embeddings=True, show_progress_bar=False)
        logger.info("embedding_model_loaded", elapsed_seconds=round(time.monotonic() - start, 2))


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed a batch of texts (768-dim, cosine-normalized).

    Runs on its own executor thread (main.py) with its own lock:
    CPU-only work that must not queue behind multi-minute Metal ASR.
    """
    if not models_loaded():
        load_models()
    with _embedding_lock:
        vectors = _embedding_model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
    return [v.tolist() for v in vectors]


def models_loaded() -> bool:
    return _asr_model is not None and _diarization_pipeline is not None and _embedding_model is not None


def merge_diarization(
    segments: list[tuple[float, float, str]],
    turns: list[tuple[float, float, str]],
) -> str:
    """Assign speakers to ASR segments by maximal temporal overlap.

    Pure function -- the entire correctness burden of diarized output
    lives here, so it is unit-tested without any ML stack.

    Args:
        segments: ASR output as (start, end, text), time-ordered-ish.
        turns: diarization output as (start, end, raw_speaker_label).

    Returns:
        Speaker-prefixed transcript ("Speaker A: ..." lines). Raw
        diarization labels (SPEAKER_00...) are renamed A, B, C... by
        order of first appearance. Consecutive same-speaker segments
        coalesce into one line. Segments with no overlapping turn attach
        to the previous speaker (mid-utterance gaps are usually the
        diarizer missing a beat, not a new voice).
    """
    if not segments:
        return ""

    label_map: dict[str, str] = {}

    def _friendly(raw: str) -> str:
        if raw not in label_map:
            index = len(label_map)
            label_map[raw] = (
                f"Speaker {_SPEAKER_ALPHABET[index]}" if index < len(_SPEAKER_ALPHABET) else f"Speaker {index + 1}"
            )
        return label_map[raw]

    def _speaker_for(seg_start: float, seg_end: float) -> str | None:
        best_raw, best_overlap = None, 0.0
        for t_start, t_end, raw in turns:
            overlap = min(seg_end, t_end) - max(seg_start, t_start)
            if overlap > best_overlap:
                best_overlap, best_raw = overlap, raw
        return _friendly(best_raw) if best_raw is not None else None

    lines: list[tuple[str, list[str]]] = []  # (speaker, [texts])
    current_speaker: str | None = None

    for seg_start, seg_end, text in segments:
        text = text.strip()
        if not text:
            continue
        speaker = _speaker_for(seg_start, seg_end) or current_speaker or _friendly("__unknown__")
        if speaker == current_speaker and lines:
            lines[-1][1].append(text)
        else:
            lines.append((speaker, [text]))
            current_speaker = speaker

    return "\n".join(f"{speaker}: {' '.join(texts)}" for speaker, texts in lines)


def transcribe_diarized(wav_path: str) -> str:
    """Transcribe a WAV file with globally-consistent speaker labels.

    Args:
        wav_path: 16kHz mono WAV (the worker's ffmpeg step guarantees this).

    Returns:
        Speaker-prefixed transcript text.
    """
    if not models_loaded():
        load_models()

    start = time.monotonic()

    with _inference_lock:
        # chunk_duration keeps the Metal working set bounded: without it,
        # parakeet-mlx attends over the WHOLE file and a 3-hour episode
        # asks Metal for ~720 GB in one buffer. Internal chunking keeps
        # timestamps global, so pyannote's single full-file diarization
        # pass below still yields consistent speakers across the episode.
        asr_result = _asr_model.transcribe(wav_path, chunk_duration=120.0, overlap_duration=15.0)
        segments = [(s.start, s.end, s.text) for s in asr_result.sentences]

        diarization = _diarization_pipeline(wav_path)
        # pyannote 4.x returns a DiarizeOutput wrapper; its
        # exclusive_speaker_diarization is purpose-built for transcription
        # alignment (no overlapping turns). pyannote 3.x returns the
        # Annotation directly -- fall through to it unchanged.
        annotation = getattr(diarization, "exclusive_speaker_diarization", None)
        if annotation is None:
            annotation = getattr(diarization, "speaker_diarization", diarization)
        turns = [(turn.start, turn.end, speaker) for turn, _, speaker in annotation.itertracks(yield_label=True)]

    text = merge_diarization(segments, turns)

    logger.info(
        "local_transcription_complete",
        wav_path=wav_path,
        segments=len(segments),
        turns=len(turns),
        speakers=len({t[2] for t in turns}),
        word_count=len(text.split()),
        elapsed_seconds=round(time.monotonic() - start, 2),
    )
    return text
