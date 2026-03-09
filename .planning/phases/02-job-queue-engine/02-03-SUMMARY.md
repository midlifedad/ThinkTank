---
phase: 02-job-queue-engine
plan: 03
subsystem: worker-loop
tags: [worker-loop, handler-registry, protocol, asyncio, graceful-shutdown, contract-tests]
dependency_graph:
  requires: [02-01 (claim, retry, errors), 02-02 (kill-switch, reclaim, backpressure, rate-limiter)]
  provides: [worker_loop, generate_worker_id, JobHandler, JOB_HANDLERS, register_handler, get_handler, WorkerSettings, get_worker_settings]
  affects: [03-xx (Phase 3+ job handlers register into JOB_HANDLERS)]
tech_stack:
  added: []
  patterns: [asyncio-semaphore-concurrency, protocol-based-dispatch, signal-handler-graceful-shutdown, interruptible-sleep]
key_files:
  created:
    - src/thinktank/worker/loop.py
    - src/thinktank/worker/config.py
    - src/thinktank/worker/__main__.py
    - src/thinktank/handlers/__init__.py
    - src/thinktank/handlers/base.py
    - src/thinktank/handlers/registry.py
    - tests/contract/__init__.py
    - tests/contract/test_handler_contracts.py
    - tests/integration/test_worker_loop.py
  modified:
    - src/thinktank/worker/__init__.py
    - src/thinktank/queue/__init__.py
decisions:
  - "Worker loop accepts optional shutdown_event parameter for testability without signal handlers"
  - "Used merge() to persist backpressure priority changes on detached job objects"
  - "Handler-not-found uses max_attempts=1 to immediately fail (no retry for missing handlers)"
  - "Reclamation scheduler sleeps first then runs, avoiding immediate reclamation on startup"
  - "_interruptible_sleep pattern used throughout for responsive shutdown"
metrics:
  duration: ~4 minutes
  completed: 2026-03-09
  tasks_completed: 2
  tasks_total: 2
  tests_added: 11
  tests_total_passing: 215
---

# Phase 02 Plan 03: Worker Loop, Handler Registry, and Contract Tests Summary

Async worker loop with poll/claim/dispatch cycle using asyncio.Semaphore concurrency, Protocol-based JobHandler interface with registry dispatch map, backpressure priority adjustment before dispatch, stale reclamation scheduler, and graceful SIGTERM shutdown with 60-second in-flight task timeout.

## What Was Built

### JobHandler Protocol (`src/thinktank/handlers/base.py`)
- `JobHandler(Protocol)` defines `async __call__(session: AsyncSession, job: Job) -> None`
- Uses `TYPE_CHECKING` import to avoid circular dependency with Job model
- Handlers raise on failure; worker loop catches, categorizes, and calls `fail_job()`

### Handler Registry (`src/thinktank/handlers/registry.py`)
- `JOB_HANDLERS: dict[str, JobHandler]` -- dispatch map populated by Phase 3+
- `register_handler(job_type, handler)` -- raises `ValueError` on duplicate registration
- `get_handler(job_type)` -- returns handler or `None` for unregistered types

### Worker Configuration (`src/thinktank/worker/config.py`)
- `WorkerSettings(BaseSettings)` with `WORKER_` env prefix (same pattern as `thinktank.config`)
- `poll_interval=2.0`, `max_idle_backoff=30.0`, `idle_backoff_multiplier=1.5`
- `max_concurrency=4`, `reclaim_interval=300.0`, `service_type="cpu"`, `job_types=None`
- `get_worker_settings()` with `@lru_cache` singleton

### Worker Loop (`src/thinktank/worker/loop.py`)

**`generate_worker_id(service_type)`** -- returns `{service_type}-{hostname}-{pid}`

**`worker_loop(session_factory, settings?, shutdown_event?)`** -- main async loop:
1. Initializes semaphore, active_tasks set, worker_id
2. Registers SIGTERM/SIGINT handlers (only when no external shutdown_event)
3. Starts `_reclamation_scheduler` as background task
4. Main loop: check kill switch -> claim job -> backpressure check -> dispatch via semaphore
5. Graceful shutdown: cancel reclamation, `asyncio.wait(active_tasks, timeout=60)`

**`_process_job(session_factory, job, semaphore, worker_id)`** -- single job processing:
- Looks up handler via `get_handler(job.job_type)`
- No handler: `fail_job()` with `ErrorCategory.HANDLER_NOT_FOUND`, `max_attempts=1`
- Handler success: `complete_job(session, job.id)`
- Handler exception: `categorize_error(exc)` then `fail_job()` with category
- Always releases semaphore in `finally` block

**`_reclamation_scheduler(session_factory, interval, shutdown_event)`** -- runs `reclaim_stale_jobs()` every `interval` seconds, logs results, continues on error

**`_interruptible_sleep(duration, shutdown_event)`** -- sleep that responds to shutdown immediately

### Entry Point (`src/thinktank/worker/__main__.py`)
- `python -m thinktank.worker` invokes `asyncio.run(worker_loop(async_session_factory))`

### Queue Re-exports (`src/thinktank/queue/__init__.py`)
- Re-exports all public functions from all 6 submodules (claim, errors, retry, kill_switch, reclaim, rate_limiter, backpressure)
- `__all__` list for explicit public API

## Test Coverage

| Test File | Type | Tests | Covers |
|-----------|------|-------|--------|
| tests/contract/test_handler_contracts.py | contract | 7 | JobHandler protocol conformance (function + class), register/get/duplicate, dispatch with correct args, exception propagation |
| tests/integration/test_worker_loop.py | integration | 4 | Full lifecycle (claim+dispatch+complete), handler failure (fail/retry), kill switch prevents claiming, handler-not-found fails job |

**Total: 11 new tests. Full suite: 215 passing, 0 failures.**

## Deviations from Plan

None - plan executed exactly as written.

## Commits

| Hash | Message |
|------|---------|
| `361fa28` | feat(02-03): add handler protocol, registry, and worker config |
| `fa71d6f` | test(02-03): add failing tests for worker loop and handler contracts |
| `ae9f5cd` | feat(02-03): implement worker loop with poll/claim/dispatch cycle |

## Verification

```
$ uv run pytest tests/ -x -q
215 passed, 1 warning in 5.79s
```

```
$ uv run python -c "from src.thinktank.worker.loop import worker_loop; from src.thinktank.handlers.registry import JOB_HANDLERS; print(f'Worker loop ready, {len(JOB_HANDLERS)} handlers registered')"
Worker loop ready, 0 handlers registered
```

All Phase 1, Phase 2, and new tests pass with zero regressions.

## Self-Check: PASSED

- All 12 files verified present on disk (9 created + 2 modified + 1 summary)
- All 3 commits (361fa28, fa71d6f, ae9f5cd) verified in git log
- 215/215 tests passing
