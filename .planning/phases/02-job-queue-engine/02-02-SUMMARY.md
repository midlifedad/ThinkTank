---
phase: 02-job-queue-engine
plan: 02
subsystem: queue-coordination
tags: [rate-limiting, backpressure, kill-switch, reclamation, tdd]
dependency_graph:
  requires: [02-01]
  provides: [rate_limiter, backpressure, kill_switch, reclaim]
  affects: [02-03]
tech_stack:
  added: []
  patterns: [sliding-window-rate-limit, backpressure-hysteresis, bulk-update-returning, localtimestamp-for-naive-columns]
key_files:
  created:
    - src/thinktank/queue/rate_limiter.py
    - src/thinktank/queue/backpressure.py
    - src/thinktank/queue/kill_switch.py
    - src/thinktank/queue/reclaim.py
    - tests/unit/test_rate_limiter.py
    - tests/unit/test_backpressure.py
    - tests/integration/test_rate_limit.py
    - tests/integration/test_backpressure.py
    - tests/integration/test_kill_switch.py
    - tests/integration/test_reclaim.py
  modified:
    - src/thinktank/queue/__init__.py
decisions:
  - Used LOCALTIMESTAMP instead of NOW() or Python UTC for TIMESTAMP WITHOUT TIME ZONE comparisons to avoid timezone mismatch
  - Used raw SQL text() for rate limiter window query and reclamation bulk UPDATE for timezone safety and RETURNING support
  - Used MagicMock (not AsyncMock) for SQLAlchemy result objects in unit tests because scalar_one/scalar_one_or_none are sync methods
  - Used MAKE_INTERVAL(mins => :param) for parameterized interval arithmetic instead of string interpolation
metrics:
  duration: ~10 minutes
  completed: 2026-03-09
  tasks_completed: 2
  tasks_total: 2
  tests_added: 47
  tests_total_passing: 204
---

# Phase 02 Plan 02: Queue Coordination Modules Summary

Sliding-window rate limiting via rate_limit_usage table, backpressure priority demotion (+3) with 80% hysteresis restore, global kill switch via workers_active config, and stale job reclamation with retry/fail logic using bulk RETURNING query.

## What Was Built

### Rate Limiter (`src/thinktank/queue/rate_limiter.py`)

- `get_rate_limit_config(session, api_name)` -- reads `{api_name}_calls_per_hour` from system_config, handles JSONB dict and raw int formats
- `check_and_acquire_rate_limit(session, api_name, worker_id, window_minutes=60)` -- counts calls within sliding window using `LOCALTIMESTAMP`, inserts usage row if under limit, returns False when at limit, returns True (fail-open) when no config exists

### Backpressure (`src/thinktank/queue/backpressure.py`)

- `BACKPRESSURE_JOB_TYPES` -- set of all 10 discovery/fetch job types from spec Section 6
- `get_queue_depth(session, job_type)` -- counts pending+retrying jobs for a type
- `get_effective_priority(session, job)` -- returns `min(priority + 3, 10)` when process_content depth > threshold, original priority when depth < 80% threshold, original priority in hysteresis band (80-100%)
- Threshold read from `max_pending_transcriptions` config (default 500)

### Kill Switch (`src/thinktank/queue/kill_switch.py`)

- `is_workers_active(session)` -- reads `workers_active` from system_config
- Returns True when active, False when killed
- Fail-open: returns True when no config row exists
- Handles JSONB formats: raw boolean, `{"value": true/false}`

### Stale Job Reclamation (`src/thinktank/queue/reclaim.py`)

- `reclaim_stale_jobs(session)` -- bulk UPDATE with RETURNING for stuck running jobs
- Two-phase: reads `stale_job_timeout_minutes` config first (default 30), then uses as bind parameter
- Reclaimed jobs with attempts+1 < max_attempts: status='retrying', scheduled_at with exponential backoff
- Reclaimed jobs with attempts+1 >= max_attempts: status='failed', completed_at set
- All reclaimed jobs: worker_id=NULL, error='Reclaimed: exceeded stale_job_timeout_minutes', error_category='worker_timeout'

## Test Coverage

| Test File | Type | Tests | Covers |
|-----------|------|-------|--------|
| tests/unit/test_rate_limiter.py | unit | 7 | Config extraction, fail-open, rate check logic |
| tests/unit/test_backpressure.py | unit | 13 | Job type membership, demotion math, priority logic |
| tests/integration/test_rate_limit.py | integration | 6 | Sliding window, blocking, fail-open, old rows, per-API isolation |
| tests/integration/test_backpressure.py | integration | 8 | Queue depth counting, demotion, hysteresis, cap at 10 |
| tests/integration/test_kill_switch.py | integration | 5 | Active/inactive, fail-open, JSONB formats |
| tests/integration/test_reclaim.py | integration | 8 | Stale/fresh detection, retry/fail, mixed jobs, default timeout |

**Total: 47 new tests. Full suite: 204 passing, 0 failures.**

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Timezone mismatch in sliding-window query**
- **Found during:** Task 1, rate limiter integration tests
- **Issue:** PostgreSQL `NOW()` returns timezone-aware timestamp, but `called_at` column is `TIMESTAMP WITHOUT TIME ZONE` with `server_default=NOW()` which stores in PG's local timezone (America/Vancouver). Python's UTC-based cutoff computation created a 7-hour mismatch where all rows appeared "within window" regardless of age.
- **Fix:** Replaced Python-computed cutoff with `LOCALTIMESTAMP - MAKE_INTERVAL(mins => :param)` in raw SQL, ensuring both sides of the comparison use the same time base. Applied same pattern to reclaim module.
- **Files modified:** `src/thinktank/queue/rate_limiter.py`, `tests/integration/test_rate_limit.py`
- **Commit:** 0387ae3

**2. [Rule 1 - Bug] AsyncMock returning coroutines for sync Result methods**
- **Found during:** Task 1, unit test execution
- **Issue:** Using `AsyncMock` for SQLAlchemy `Result` objects caused `scalar_one()` and `scalar_one_or_none()` to return coroutines instead of values, since AsyncMock makes all methods async by default.
- **Fix:** Changed unit test result mocks from `AsyncMock` to `MagicMock` (sync) while keeping `AsyncSession` as `AsyncMock` (async).
- **Files modified:** `tests/unit/test_rate_limiter.py`, `tests/unit/test_backpressure.py`
- **Commit:** 0387ae3

## Commits

| Hash | Message |
|------|---------|
| 6274485 | test(02-02): add failing tests for rate limiter and backpressure |
| 0387ae3 | feat(02-02): implement rate limiter and backpressure modules |
| 49498bf | test(02-02): add failing tests for kill switch and reclamation |
| 535d78b | feat(02-02): implement kill switch and stale job reclamation |

## Verification

```
$ uv run pytest tests/ -x -q
204 passed, 1 warning in 3.80s
```

All Phase 1 and Phase 2 tests pass with zero regressions.

## Self-Check: PASSED

- All 10 created files exist on disk
- All 4 commits found in git history
- 204/204 tests passing
