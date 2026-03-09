---
phase: 04-transcription-pipeline
plan: 02
subsystem: transcription
tags: [process-content, gpu-worker, parakeet, railway-scaling, three-pass, handler]

# Dependency graph
requires:
  - phase: 04-transcription-pipeline
    plan: 01
    provides: captions.py, existing.py, audio.py, gpu_client.py, railway.py
  - phase: 03-ingestion-pipeline
    provides: queue/backpressure.py get_queue_depth, models/content.py Content, handlers/base.py JobHandler, handlers/registry.py
provides:
  - Three-pass process_content handler orchestrator (handle_process_content)
  - GPU worker FastAPI service with Parakeet model singleton (/transcribe, /health)
  - GPU scaling scheduler in worker loop (manage_gpu_scaling integration)
  - process_content registered in handler registry
affects: [worker-loop, handler-registry, content-status-transitions]

# Tech tracking
tech-stack:
  added: []
  patterns: [three-pass-fallback-orchestrator, call-site-mocking-for-integration-tests, session-factory-context-manager-mock, gpu-scaling-scheduler-pattern]

key-files:
  created:
    - src/thinktank/handlers/process_content.py
    - src/thinktank/gpu_worker/__init__.py
    - src/thinktank/gpu_worker/main.py
    - src/thinktank/gpu_worker/model.py
    - tests/unit/test_process_content.py
    - tests/unit/test_gpu_scaling_scheduler.py
    - tests/integration/test_gpu_scaling.py
    - tests/integration/test_process_content.py
    - tests/contract/test_transcription_handlers.py
  modified:
    - src/thinktank/handlers/registry.py
    - src/thinktank/worker/loop.py
    - src/thinktank/scaling/railway.py
    - tests/unit/test_railway_client.py

key-decisions:
  - "process_content handler catches GPU transcription exceptions and falls through to 'all passes failed' rather than propagating immediately"
  - "GPU scaling scheduler reuses reclaim_interval (300s) for scaling check interval"
  - "Call-site mocking pattern for integration tests: patch at src.thinktank.handlers.process_content.* not definition site"
  - "Fixed timezone-naive consistency in manage_gpu_scaling (datetime.now(UTC).replace(tzinfo=None)) to match project convention"

patterns-established:
  - "Three-pass fallback orchestrator: try each pass in order, first non-None result wins, RuntimeError if all fail"
  - "Call-site mocking: patch imported names at the handler module namespace for correct mock interception"
  - "Session factory mock using contextlib.asynccontextmanager for proper async-with protocol"
  - "GPU worker lifespan pattern: model preloaded into VRAM at FastAPI startup, singleton reused across requests"

requirements-completed: [TRANS-01, TRANS-02, TRANS-04, TRANS-06]

# Metrics
duration: 11min
completed: 2026-03-08
---

# Phase 4 Plan 02: Process Content Handler and GPU Worker Summary

**Three-pass process_content handler orchestrating YouTube captions, existing transcripts, and Parakeet GPU inference, with GPU worker FastAPI service and scaling scheduler integrated into worker loop**

## Performance

- **Duration:** 11 min
- **Started:** 2026-03-09T03:40:39Z
- **Completed:** 2026-03-09T03:52:21Z
- **Tasks:** 2
- **Files modified:** 13

## Accomplishments
- Three-pass process_content handler implementing youtube_captions -> existing_transcript -> parakeet fallback chain
- GPU worker FastAPI service with Parakeet model singleton, /transcribe (multipart WAV upload), and /health endpoints
- GPU scaling scheduler in worker loop running every 5 minutes, scaling up when queue depth > threshold, scaling down after idle timeout
- Handler registered in registry as `process_content`, dispatched by worker loop
- 24 new tests across 5 files: 8 unit (handler), 3 unit (scheduler), 6 integration (scaling), 6 integration (handler), 1 contract
- Full regression suite green: 366 tests pass, 55 Phase 4 tests total

## Task Commits

Each task was committed atomically:

1. **Task 1: process_content handler, GPU worker, scaling scheduler** - `2077ee7` (feat)
2. **Task 2: Integration and contract tests** - `46d0349` (test)

## Files Created/Modified
- `src/thinktank/handlers/process_content.py` - Three-pass transcription orchestrator handler
- `src/thinktank/gpu_worker/__init__.py` - Package init
- `src/thinktank/gpu_worker/model.py` - Parakeet TDT 1.1B model singleton loader with lazy NeMo import
- `src/thinktank/gpu_worker/main.py` - FastAPI service with /transcribe (multipart upload) and /health endpoints
- `src/thinktank/handlers/registry.py` - Added process_content handler registration
- `src/thinktank/worker/loop.py` - Added _gpu_scaling_scheduler and gpu_scaling_task lifecycle
- `src/thinktank/scaling/railway.py` - Fixed timezone-naive datetime consistency in manage_gpu_scaling
- `tests/unit/test_process_content.py` - 8 unit tests for handler three-pass logic
- `tests/unit/test_gpu_scaling_scheduler.py` - 3 unit tests for scheduler lifecycle
- `tests/integration/test_gpu_scaling.py` - 6 integration tests for manage_gpu_scaling with real DB
- `tests/integration/test_process_content.py` - 6 integration tests for handler with real DB
- `tests/contract/test_transcription_handlers.py` - 1 contract test for complete side-effect contract
- `tests/unit/test_railway_client.py` - Fixed tz-aware datetime in existing tests

## Decisions Made
- process_content handler catches GPU exceptions in Pass 3 and falls through to "all passes failed" rather than propagating the exception directly, allowing the RuntimeError to be categorized consistently by the worker loop
- GPU scaling scheduler reuses `settings.reclaim_interval` (300s / 5 minutes) as its check interval, matching the spec requirement without adding a new configuration value
- Integration tests mock at the call site (`src.thinktank.handlers.process_content.extract_youtube_captions`) rather than the definition site (`src.thinktank.transcription.captions.extract_youtube_captions`) because Python's mock.patch intercepts name lookups at the import site
- Fixed timezone-naive consistency bug in manage_gpu_scaling: used `datetime.now(UTC).replace(tzinfo=None)` to match the project-wide convention established in `_now()` helper

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed timezone-naive/aware mismatch in manage_gpu_scaling**
- **Found during:** Task 1 (integration tests for GPU scaling)
- **Issue:** `manage_gpu_scaling` returned `datetime.now(UTC)` (tz-aware) from the "start idle timer" branch but then tried to subtract a tz-naive `gpu_idle_since` from tz-aware `datetime.now(UTC)`, causing `TypeError: can't subtract offset-naive and offset-aware datetimes`
- **Fix:** Changed both datetime creation points to use `datetime.now(UTC).replace(tzinfo=None)` matching the project convention established in `queue/claim.py:_now()`
- **Files modified:** `src/thinktank/scaling/railway.py`, `tests/unit/test_railway_client.py`
- **Commit:** 2077ee7

**2. [Rule 1 - Bug] Fixed session factory mock for async context manager protocol**
- **Found during:** Task 1 (GPU scaling scheduler unit tests)
- **Issue:** `AsyncMock()` when called as `session_factory()` returns a coroutine (not an async context manager), causing `TypeError: 'coroutine' object does not support the asynchronous context manager protocol` in an infinite error loop
- **Fix:** Used `contextlib.asynccontextmanager` wrapper with `MagicMock(side_effect=...)` to create a proper async context manager factory
- **Files modified:** `tests/unit/test_gpu_scaling_scheduler.py`
- **Committed in:** 2077ee7

---

**Total deviations:** 2 auto-fixed (2 bugs -- 1 in production code, 1 in test mock setup)
**Impact on plan:** The production tz bug was pre-existing from Plan 01. The mock setup fix was a test infrastructure issue. Neither affected scope.

## Issues Encountered
None beyond the auto-fixed bugs documented above.

## User Setup Required
None. GPU worker service requires NeMo toolkit and CUDA at runtime but not for development/testing.

## Next Phase Readiness
- Phase 4 (Transcription Pipeline) is now complete with all 6 requirements (TRANS-01 through TRANS-06) satisfied
- Content discovered in Phase 3 can now flow through process_content handler into full-text transcripts
- Worker loop includes GPU scaling scheduler for on-demand Railway GPU management
- No blockers for Phase 5

---
*Phase: 04-transcription-pipeline*
*Completed: 2026-03-08*
