---
phase: 04-transcription-pipeline
plan: 01
subsystem: transcription
tags: [yt-dlp, webvtt, ffmpeg, httpx, railway-api, graphql, gpu, async-subprocess]

# Dependency graph
requires:
  - phase: 03-ingestion-pipeline
    provides: queue/backpressure.py get_queue_depth, ingestion/config_reader.py get_config_value, models/content.py Content model, queue/errors.py ErrorCategory
provides:
  - YouTube caption extraction module (extract_youtube_captions)
  - Existing transcript fetch module (fetch_existing_transcript)
  - Audio download + async ffmpeg conversion (download_audio, convert_to_wav)
  - Temp file lifecycle with guaranteed cleanup (transcribe_via_gpu)
  - GPU HTTP client with multipart WAV upload and chunking (send_to_gpu, transcribe_with_chunking)
  - Railway GraphQL scaling client (scale_gpu_service, get_gpu_replica_count, manage_gpu_scaling)
affects: [04-02-process-content-handler, worker-loop-gpu-scheduler]

# Tech tracking
tech-stack:
  added: [yt-dlp==2025.12.08, webvtt-py>=0.5.1]
  patterns: [sync yt-dlp wrapped in try/except fail-safe, async subprocess for ffmpeg, dependency-injected GPU client callable, Railway GraphQL via httpx, idle-timeout scaling with datetime tracking]

key-files:
  created:
    - src/thinktank/transcription/__init__.py
    - src/thinktank/transcription/captions.py
    - src/thinktank/transcription/existing.py
    - src/thinktank/transcription/audio.py
    - src/thinktank/transcription/gpu_client.py
    - src/thinktank/scaling/__init__.py
    - src/thinktank/scaling/railway.py
    - tests/unit/test_captions.py
    - tests/unit/test_existing_transcript.py
    - tests/unit/test_audio.py
    - tests/unit/test_gpu_client.py
    - tests/unit/test_railway_client.py
  modified:
    - pyproject.toml
    - uv.lock

key-decisions:
  - "Used webvtt.from_buffer instead of deprecated read_buffer for VTT parsing"
  - "Caption deduplication uses ordered list (not set) to preserve cue order while removing overlapping duplicates"
  - "download_audio is sync (yt-dlp is sync), convert_to_wav is async (ffmpeg subprocess)"
  - "transcribe_via_gpu takes gpu_client_fn callable for dependency injection and testability"
  - "manage_gpu_scaling returns (bool, datetime|None) tuple for caller to track idle state"

patterns-established:
  - "Fail-safe pattern: entire function wrapped in try/except returning None for non-critical extraction (captions, existing transcript)"
  - "Guaranteed cleanup: audio temp files deleted in finally blocks on both success and failure"
  - "Async subprocess pattern: asyncio.create_subprocess_exec with wait_for timeout and proc.kill on timeout"
  - "Railway GraphQL: mutation + query via httpx POST with Bearer auth, graceful fallback on missing env vars"

requirements-completed: [TRANS-01, TRANS-03, TRANS-05]

# Metrics
duration: 7min
completed: 2026-03-08
---

# Phase 4 Plan 01: Transcription Building Blocks Summary

**YouTube caption extraction via yt-dlp with VTT dedup, existing transcript fetch, async ffmpeg audio conversion, GPU multipart client with 45-min chunking, and Railway GraphQL scaling client**

## Performance

- **Duration:** 7 min
- **Started:** 2026-03-09T03:29:39Z
- **Completed:** 2026-03-09T03:36:44Z
- **Tasks:** 2
- **Files modified:** 14

## Accomplishments
- Built 5 transcription/scaling modules covering all three passes of the transcription pipeline
- Full unit test coverage: 31 new tests across 5 test files, all passing
- Guaranteed temp file cleanup on both success and failure paths (TRANS-05)
- GPU scaling logic with threshold-based scale-up and idle-timeout scale-down
- Full regression suite green: 342 tests pass

## Task Commits

Each task was committed atomically:

1. **Task 1: Transcription modules (captions, existing, audio)** - `0cf194f` (feat)
2. **Task 2: GPU client and Railway scaling client** - `6f5a3db` (feat)

## Files Created/Modified
- `pyproject.toml` - Added yt-dlp==2025.12.08 and webvtt-py>=0.5.1 dependencies
- `src/thinktank/transcription/__init__.py` - Package init
- `src/thinktank/transcription/captions.py` - Pass 1: YouTube caption extraction with VTT parsing, 100-word threshold, fail-safe
- `src/thinktank/transcription/existing.py` - Pass 2: Existing transcript fetch with URL pattern substitution and HTML stripping
- `src/thinktank/transcription/audio.py` - Audio download (yt-dlp), async ffmpeg conversion (16kHz mono WAV), orchestrator with cleanup
- `src/thinktank/transcription/gpu_client.py` - GPU HTTP client with multipart WAV upload and >60min audio chunking
- `src/thinktank/scaling/__init__.py` - Package init
- `src/thinktank/scaling/railway.py` - Railway GraphQL client for GPU scaling (serviceInstanceUpdate mutation, replica query, manage_gpu_scaling)
- `tests/unit/test_captions.py` - 5 tests: success, no-subs, too-short, exception, VTT dedup
- `tests/unit/test_existing_transcript.py` - 4 tests: success, no-pattern, 404, timeout
- `tests/unit/test_audio.py` - 7 tests: download success/failure, convert success/failure/timeout, cleanup success/failure
- `tests/unit/test_gpu_client.py` - 7 tests: transcribe success/timeout/error/multipart, chunking long/short
- `tests/unit/test_railway_client.py` - 8 tests: scale up/down/missing-config/api-error, replica count, manage scaling up/down/no-action/start-timer

## Decisions Made
- Used `webvtt.from_buffer` instead of deprecated `read_buffer` (future-proofing for webvtt-py updates)
- Caption deduplication uses ordered list rather than set to preserve cue ordering
- `download_audio` is synchronous because yt-dlp is inherently sync; `convert_to_wav` is async via subprocess
- `transcribe_via_gpu` accepts a `gpu_client_fn` callable parameter for dependency injection, making cleanup tests straightforward with real temp files
- `manage_gpu_scaling` returns a `(bool, datetime | None)` tuple so the caller (worker loop) can track idle state across invocations without module-level state

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed VTT test helper generating non-unique cue text**
- **Found during:** Task 1 (caption tests)
- **Issue:** `_vtt_content(150)` generated 15 cues all containing "word word word..." which the dedup logic correctly collapsed to 1 line (10 words), causing the success test to fail
- **Fix:** Changed helper to generate unique words (`word0`, `word1`, ...) so each cue has distinct text
- **Files modified:** tests/unit/test_captions.py
- **Verification:** test_extract_captions_success passes with 150 unique words
- **Committed in:** 0cf194f (Task 1 commit)

---

**Total deviations:** 1 auto-fixed (1 bug in test helper)
**Impact on plan:** Trivial test data fix. No scope creep.

## Issues Encountered
None beyond the test helper fix documented above.

## User Setup Required
None - no external service configuration required. Railway env vars (RAILWAY_API_KEY, RAILWAY_GPU_SERVICE_ID, RAILWAY_ENVIRONMENT_ID, GPU_WORKER_URL) are needed at runtime but not for development/testing.

## Next Phase Readiness
- All transcription building blocks are ready for Plan 02 (`process_content` handler) to orchestrate
- The handler will import `extract_youtube_captions`, `fetch_existing_transcript`, `transcribe_via_gpu`, and `transcribe_with_chunking`
- `manage_gpu_scaling` is ready to be wired into the worker loop's scheduler
- No blockers

---
*Phase: 04-transcription-pipeline*
*Completed: 2026-03-08*
