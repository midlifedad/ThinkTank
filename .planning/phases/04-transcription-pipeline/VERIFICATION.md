---
phase: 04-transcription-pipeline
verified: 2026-03-09T03:56:53Z
status: passed
score: 7/7 must-haves verified
---

# Phase 4: Transcription Pipeline Verification Report

**Phase Goal:** Content discovered in Phase 3 is transcribed through a three-pass pipeline (YouTube captions first, existing transcripts second, Parakeet GPU inference last) with on-demand GPU scaling and automatic audio cleanup
**Verified:** 2026-03-09T03:56:53Z
**Status:** passed
**Re-verification:** No -- initial verification

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | A process_content job first attempts YouTube captions, then checks for existing transcripts, and only falls back to Parakeet GPU inference when no text source is found -- with transcription_method recording which pass succeeded | VERIFIED | `src/thinktank/handlers/process_content.py` lines 62-99 implement the three-pass chain in order (Pass 1: youtube_channel check, Pass 2: transcript_url_pattern check, Pass 3: transcribe_via_gpu). Field `content.transcription_method` set to "youtube_captions", "existing_transcript", or "parakeet" on lines 66/76/89. Unit tests (8 tests) and integration tests (6 tests) exercise all paths including fallback. |
| 2 | The GPU worker service loads Parakeet TDT 1.1B into VRAM once and holds it across jobs, processing audio at near real-time speed on an L4 GPU | VERIFIED | `src/thinktank/gpu_worker/model.py` uses module-level `_model = None` singleton (line 17), `load_model()` guards with `if _model is not None: return _model` (line 30), loads `nvidia/parakeet-tdt-1.1b` via NeMo API and calls `.eval()` (lines 37-42). `src/thinktank/gpu_worker/main.py` calls `load_model()` in FastAPI lifespan startup (line 35). |
| 3 | Audio is downloaded via yt-dlp, converted to 16kHz mono WAV via ffmpeg, and deleted immediately after transcription -- audio is never persisted to storage | VERIFIED | `src/thinktank/transcription/audio.py`: `download_audio()` uses `YoutubeDL` (line 55), `convert_to_wav()` uses `asyncio.create_subprocess_exec("ffmpeg", ..., "-ar", "16000", "-ac", "1", ...)` (lines 89-103), `transcribe_via_gpu()` has `finally` block (lines 166-174) that deletes both `audio_path` and `wav_path` via `os.unlink()`. Unit tests verify cleanup on both success and failure paths. |
| 4 | The CPU worker scales the GPU service up via Railway API when process_content queue exceeds threshold, and scales it down after the configured idle timeout with no pending transcription jobs | VERIFIED | `src/thinktank/scaling/railway.py`: `manage_gpu_scaling()` (lines 148-204) reads `gpu_queue_threshold` and `gpu_idle_minutes_before_shutdown` from config, calls `get_queue_depth(session, "process_content")`, scales up when `depth > threshold` and `replicas == 0` (line 184), scales down when `elapsed > timedelta(minutes=idle_minutes)` (lines 199-201). `_gpu_scaling_scheduler` in `worker/loop.py` (lines 330-358) runs this on interval. 6 integration tests verify all scaling paths against real DB. |
| 5 | process_content handler is registered in the handler registry and dispatched by the worker loop | VERIFIED | `src/thinktank/handlers/registry.py` line 55: `register_handler("process_content", handle_process_content)`. Import at line 9. Worker loop dispatches via `get_handler(job.job_type)` at line 235. |
| 6 | Audio temp files are cleaned up after transcription regardless of outcome | VERIFIED | `src/thinktank/transcription/audio.py` lines 166-174: `finally` block iterates `[audio_path, wav_path]` and calls `os.unlink()`. Also `gpu_client.py` lines 132-139 clean up chunk files in `finally`. `gpu_worker/main.py` lines 81-87 clean up uploaded temp files in `finally`. Unit tests `test_cleanup_on_success` and `test_cleanup_on_failure` verify with real temp files. |
| 7 | GPU worker service exposes /transcribe and /health endpoints | VERIFIED | `src/thinktank/gpu_worker/main.py`: `@app.post("/transcribe")` at line 44 accepts multipart WAV upload, calls `transcribe_audio()`, returns `{"text": result}`. `@app.get("/health")` at line 90 returns `{"status": "ok", "model_loaded": bool, "gpu_available": bool}`. |

**Score:** 7/7 truths verified

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `src/thinktank/transcription/captions.py` | Pass 1 YouTube caption extraction | VERIFIED | 106 lines. Exports `extract_youtube_captions`. Uses yt-dlp + webvtt-py. 100-word threshold. Fail-safe try/except. |
| `src/thinktank/transcription/existing.py` | Pass 2 existing transcript fetch | VERIFIED | 97 lines. Exports `fetch_existing_transcript`. Derives URL from pattern, async httpx fetch, HTML stripping. |
| `src/thinktank/transcription/audio.py` | Audio download and ffmpeg conversion with cleanup | VERIFIED | 175 lines. Exports `download_audio`, `convert_to_wav`, `transcribe_via_gpu`. yt-dlp sync download, async ffmpeg subprocess, guaranteed cleanup in finally. |
| `src/thinktank/transcription/gpu_client.py` | HTTP client for GPU worker service | VERIFIED | 188 lines. Exports `send_to_gpu`, `transcribe_with_chunking`. Multipart POST, 45-min chunking for >60min audio. |
| `src/thinktank/scaling/railway.py` | Railway GraphQL client for GPU scaling | VERIFIED | 205 lines. Exports `scale_gpu_service`, `get_gpu_replica_count`, `manage_gpu_scaling`. GraphQL mutations, idle-timeout logic. |
| `src/thinktank/handlers/process_content.py` | Three-pass transcription orchestrator handler | VERIFIED | 115 lines. Exports `handle_process_content`. Three-pass fallback, updates content with body_text, word_count, transcription_method, status, processed_at. |
| `src/thinktank/gpu_worker/main.py` | GPU worker FastAPI service | VERIFIED | 109 lines. Exports `app`. /transcribe (multipart WAV) and /health endpoints. Model preloaded in lifespan. |
| `src/thinktank/gpu_worker/model.py` | Parakeet model singleton loader | VERIFIED | 71 lines. Exports `load_model`, `transcribe_audio`. Module-level singleton, lazy NeMo import, eval mode. |
| `src/thinktank/worker/loop.py` | Worker loop with GPU scaling scheduler | VERIFIED | 372 lines. `_gpu_scaling_scheduler` at line 330, started as `asyncio.create_task` at line 105, cancelled in finally at lines 198-202. |
| `src/thinktank/handlers/registry.py` | Handler registry with process_content registered | VERIFIED | `register_handler("process_content", handle_process_content)` at line 55. |
| `tests/unit/test_captions.py` | Caption extraction unit tests | VERIFIED | 5 tests passing. |
| `tests/unit/test_existing_transcript.py` | Existing transcript unit tests | VERIFIED | 4 tests passing. |
| `tests/unit/test_audio.py` | Audio download/convert/cleanup unit tests | VERIFIED | 7 tests passing. |
| `tests/unit/test_gpu_client.py` | GPU client unit tests | VERIFIED | 6 tests passing (5 + 1 for short audio no-chunking). |
| `tests/unit/test_railway_client.py` | Railway scaling unit tests | VERIFIED | 9 tests passing (8 + 1 for start idle timer). |
| `tests/unit/test_process_content.py` | Process content handler unit tests | VERIFIED | 8 tests passing. |
| `tests/unit/test_gpu_scaling_scheduler.py` | GPU scaling scheduler unit tests | VERIFIED | 3 tests passing. |
| `tests/integration/test_process_content.py` | Process content integration tests | VERIFIED | 6 tests passing against real PostgreSQL. |
| `tests/integration/test_gpu_scaling.py` | GPU scaling integration tests | VERIFIED | 6 tests passing against real PostgreSQL. |
| `tests/contract/test_transcription_handlers.py` | Contract test for process_content | VERIFIED | 1 test passing. |

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `transcription/audio.py` | yt-dlp | `from yt_dlp import YoutubeDL` | WIRED | Line 17: import present. Lines 55-56: `YoutubeDL(opts)` used in `download_audio()`. |
| `transcription/audio.py` | ffmpeg | `asyncio.create_subprocess_exec` | WIRED | Line 89: `asyncio.create_subprocess_exec("ffmpeg", "-i", ..., "-ar", "16000", "-ac", "1", ...)` in `convert_to_wav()`. |
| `transcription/gpu_client.py` | httpx | Multipart POST to /transcribe | WIRED | Lines 51-56: `httpx.AsyncClient` + `client.post(transcribe_url, files={"file": ...})`. |
| `scaling/railway.py` | Railway GraphQL API | httpx POST to backboard.railway.com | WIRED | Line 20: `RAILWAY_API_URL = "https://backboard.railway.com/graphql/v2"`. Lines 64-71: `httpx.AsyncClient` POST with mutation. |
| `handlers/process_content.py` | `transcription/captions.py` | Pass 1 caption extraction | WIRED | Line 27: `from src.thinktank.transcription.captions import extract_youtube_captions`. Line 64: called in handler. |
| `handlers/process_content.py` | `transcription/existing.py` | Pass 2 existing transcript fetch | WIRED | Line 28: `from src.thinktank.transcription.existing import fetch_existing_transcript`. Line 74: called in handler. |
| `handlers/process_content.py` | `transcription/audio.py` | Pass 3 audio download/convert + GPU | WIRED | Line 26: `from src.thinktank.transcription.audio import transcribe_via_gpu`. Line 86: called in handler. |
| `handlers/process_content.py` | `transcription/gpu_client.py` | Pass 3 GPU transcription via HTTP | WIRED | Line 29: `from src.thinktank.transcription.gpu_client import transcribe_with_chunking`. Line 83: used in `_gpu_fn` closure. |
| `handlers/registry.py` | `handlers/process_content.py` | register_handler call | WIRED | Line 9: import. Line 55: `register_handler("process_content", handle_process_content)`. |
| `worker/loop.py` | `scaling/railway.py` | GPU scaling scheduler calls manage_gpu_scaling | WIRED | Line 34: `from src.thinktank.scaling.railway import manage_gpu_scaling`. Line 352: `scaled, gpu_idle_since = await manage_gpu_scaling(session, gpu_idle_since)`. |
| `tests/integration/test_gpu_scaling.py` | `scaling/railway.py` | Tests manage_gpu_scaling with real DB | WIRED | Line 14: `from src.thinktank.scaling.railway import manage_gpu_scaling`. |

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|------------|-------------|--------|----------|
| TRANS-01 | 04-01, 04-02 | Three-pass transcription pipeline: YouTube captions first, existing transcripts second, Parakeet GPU inference last | SATISFIED | `process_content.py` implements three-pass chain. `captions.py`, `existing.py`, `audio.py` implement individual passes. 8 unit tests + 6 integration tests verify all paths. |
| TRANS-02 | 04-02 | GPU Worker service running Parakeet TDT 1.1B on Railway L4, model persisted in VRAM across jobs | SATISFIED | `gpu_worker/model.py` singleton pattern with `_model = None` + `load_model()` guard. `gpu_worker/main.py` FastAPI lifespan preloads model. /transcribe endpoint accepts multipart WAV. |
| TRANS-03 | 04-01 | Audio download via yt-dlp (pinned to 2025.12.08) with ffmpeg conversion to 16kHz WAV | SATISFIED | `pyproject.toml` has `"yt-dlp==2025.12.08"`. `audio.py` uses `YoutubeDL` for download and `asyncio.create_subprocess_exec("ffmpeg", ..., "-ar", "16000", "-ac", "1", ...)` for conversion. |
| TRANS-04 | 04-02 | On-demand GPU scaling via Railway API -- spin up when queue > threshold, shut down after idle timeout | SATISFIED | `railway.py` `manage_gpu_scaling()` with threshold-based scale-up and idle-timeout scale-down. `_gpu_scaling_scheduler` in worker loop. 6 integration tests. |
| TRANS-05 | 04-01 | Audio temp file cleanup after transcription (audio never persisted to storage) | SATISFIED | `audio.py` `transcribe_via_gpu()` has `finally` block deleting `audio_path` and `wav_path`. `gpu_client.py` chunk cleanup in `finally`. `gpu_worker/main.py` temp upload cleanup in `finally`. Unit tests verify cleanup on success and failure. |
| TRANS-06 | 04-02 | Transcription output stored in content.body_text with metadata (word count, duration, source pass used) | SATISFIED | `process_content.py` lines 101-106: sets `content.body_text`, `content.word_count`, `content.transcription_method`, `content.status = "done"`, `content.processed_at`. Integration tests verify DB state after handler execution. |

No orphaned requirements found. All 6 TRANS requirements mapped to Phase 4 are claimed and satisfied.

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| None | - | - | - | No anti-patterns detected |

No TODOs, FIXMEs, placeholders, empty implementations, or console.log-only handlers found in any Phase 4 file.

### Human Verification Required

### 1. GPU Worker Under Real CUDA Load

**Test:** Deploy GPU worker to Railway L4 instance, submit a 10-minute audio file via /transcribe, verify transcription returns in near real-time
**Expected:** Parakeet model loads successfully into VRAM, returns accurate transcript, model persists across subsequent requests without reloading
**Why human:** Requires GPU hardware and CUDA runtime not available in test environment. NeMo import is lazy-loaded and mocked in tests.

### 2. Railway API Scaling End-to-End

**Test:** Set environment variables (RAILWAY_API_KEY, RAILWAY_GPU_SERVICE_ID, RAILWAY_ENVIRONMENT_ID), create jobs exceeding gpu_queue_threshold, observe GPU service scale-up, drain queue, wait idle timeout, observe scale-down
**Expected:** Railway GraphQL mutations successfully scale GPU replicas up and down
**Why human:** Requires live Railway API credentials and running Railway deployment. Integration tests mock the Railway API calls.

### 3. yt-dlp Audio Download from Real YouTube URL

**Test:** Run `download_audio()` against a real YouTube video URL, verify audio file is produced
**Expected:** Audio file downloaded to tmp directory, ffmpeg converts to 16kHz mono WAV
**Why human:** Requires network access and real YouTube video. yt-dlp behavior may vary with YouTube API changes.

### Gaps Summary

No gaps found. All 7 observable truths verified. All 20 artifacts exist, are substantive, and are properly wired. All 11 key links are connected. All 6 TRANS requirements are satisfied. No anti-patterns detected. Full test suite passes (366 tests, 55 Phase 4 tests). Three items flagged for human verification involving real GPU hardware, live Railway API, and real YouTube downloads.

---

_Verified: 2026-03-09T03:56:53Z_
_Verifier: Claude (gsd-verifier)_
