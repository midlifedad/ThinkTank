---
phase: 05-llm-governance
plan: 03
subsystem: llm
tags: [escalation, scheduled-tasks, health-check, digest, audit, worker-loop, time-utils]

# Dependency graph
requires:
  - phase: 05-llm-governance
    plan: 01
    provides: "LLMClient, response schemas, prompt builders, snapshot builders"
provides:
  - "Time computation utilities for scheduled task cadence"
  - "Timeout escalation for awaiting_llm jobs past llm_timeout_hours"
  - "Scheduled health check, daily digest, and weekly audit LLM tasks"
  - "Worker loop integration with 4 new LLM governance schedulers"
affects: [worker-loop, llm-reviews, job-pipeline-resilience]

# Tech tracking
tech-stack:
  added: []
  patterns: [recompute-on-each-iteration for drift avoidance, raw SQL escalation matching reclaim.py pattern, module-level LLMClient singleton]

key-files:
  created:
    - src/thinktank/llm/time_utils.py
    - src/thinktank/llm/escalation.py
    - src/thinktank/llm/scheduled.py
    - tests/unit/test_llm_time_utils.py
    - tests/unit/test_llm_escalation.py
    - tests/integration/test_llm_escalation.py
    - tests/integration/test_llm_scheduled.py
  modified:
    - src/thinktank/worker/loop.py

key-decisions:
  - "Used _utc_now() helper function (not datetime.now(UTC)) for testability via mock patching"
  - "Digest and audit schedulers recompute wait on each iteration instead of fixed intervals, avoiding clock drift"
  - "Escalation uses raw SQL UPDATE with jsonb_set matching reclaim.py pattern for consistency"
  - "Scheduled tasks catch Exception broadly and return None on failure to never crash the scheduler"
  - "LLM scheduler cancel loop uses for-loop pattern instead of individual cancel blocks for DRY"

patterns-established:
  - "Recompute-on-iteration: digest/audit schedulers call seconds_until_next_utc_hour/monday on each loop pass"
  - "Module-level LLMClient singleton (_llm_client) for shared scheduled task usage"
  - "Graceful degradation: all scheduled LLM tasks log and continue on any exception"

requirements-completed: [GOV-06, GOV-07, GOV-08, GOV-09]

# Metrics
duration: 5min
completed: 2026-03-09
---

# Phase 5 Plan 03: Timeout Escalation and Scheduled LLM Tasks Summary

**Timeout escalation flags stalled awaiting_llm jobs, 3 scheduled LLM tasks (health check/digest/audit) with graceful degradation, 4 new worker loop schedulers with drift-free cadence**

## Performance

- **Duration:** 5 min
- **Started:** 2026-03-09T04:33:10Z
- **Completed:** 2026-03-09T04:38:46Z
- **Tasks:** 2
- **Files created:** 7
- **Files modified:** 1
- **Tests added:** 25 (443 -> 468 from this plan)

## Accomplishments
- Time utilities compute seconds until any UTC hour and next Monday with frozen-time testability
- Timeout escalation finds awaiting_llm jobs older than llm_timeout_hours, sets needs_human_review flag via jsonb_set, creates LLMReview audit rows with decision="escalate_to_human"
- Three scheduled LLM tasks (health check, daily digest, weekly audit) build bounded context, call Claude, log structured results to llm_reviews, and handle API failures gracefully
- Worker loop now starts 6 total schedulers: reclamation, GPU scaling, LLM escalation (15min), health check (6h), daily digest (07:00 UTC), weekly audit (Monday 07:00 UTC)
- All new schedulers are cancelled on graceful shutdown alongside existing ones

## Task Commits

Each task was committed atomically:

1. **Task 1: Time utilities, escalation logic, and scheduled task implementations** - `0c7077b` (feat)
2. **Task 2: Worker loop integration with LLM schedulers and integration tests** - `d7f441a` (feat)

## Files Created/Modified
- `src/thinktank/llm/time_utils.py` - seconds_until_next_utc_hour and seconds_until_next_monday_utc with _utc_now() for testability
- `src/thinktank/llm/escalation.py` - escalate_timed_out_reviews with raw SQL UPDATE + LLMReview creation
- `src/thinktank/llm/scheduled.py` - run_health_check, run_daily_digest, run_weekly_audit with module-level _llm_client singleton
- `src/thinktank/worker/loop.py` - Added 4 new scheduler coroutines and their lifecycle management
- `tests/unit/test_llm_time_utils.py` - 13 tests for time computation with frozen time
- `tests/unit/test_llm_escalation.py` - 3 tests for escalation function signature verification
- `tests/integration/test_llm_escalation.py` - 4 tests for escalation against real PostgreSQL
- `tests/integration/test_llm_scheduled.py` - 5 tests for scheduled tasks with mocked LLM client

## Decisions Made
- **_utc_now() helper for testability:** Instead of patching datetime.now directly (which is fragile), time_utils exports a _utc_now() function that tests patch at the module level. This is clean and follows the same pattern as claim.py and snapshots.py.
- **Recompute-on-iteration for drift avoidance:** The daily digest and weekly audit schedulers call seconds_until_next_utc_hour(7) and seconds_until_next_monday_utc(7) on each loop iteration rather than using a fixed interval. This ensures the schedule self-corrects even if execution takes variable time.
- **Raw SQL for escalation:** Used text() with jsonb_set and MAKE_INTERVAL matching the established reclaim.py pattern, ensuring timezone consistency with LOCALTIMESTAMP.
- **Broad exception catching in scheduled tasks:** All three scheduled functions catch Exception (not just anthropic-specific errors) because scheduled tasks must never crash the worker loop scheduler.
- **Cancel loop for LLM tasks:** Used a for-loop to cancel all 4 LLM scheduler tasks instead of 4 separate try/except blocks, keeping the shutdown code DRY.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed ruff lint issues (import sorting, UTC alias, unused imports)**
- **Found during:** Task 1 and Task 2 implementation
- **Issue:** Import blocks unsorted (I001), timezone.utc instead of UTC alias (UP017), unused pytest imports (F401), unused logging import (F401)
- **Fix:** Ran ruff --fix on all new and modified files
- **Files modified:** All 7 new files + loop.py
- **Verification:** ruff check passes on all Plan 05-03 files

No other deviations. Plan executed as written.

## Issues Encountered

**Pre-existing uncommitted changes from Plan 05-02:** The working directory contained uncommitted modifications to `src/thinktank/queue/errors.py` and `tests/unit/test_errors.py` (Anthropic error categorization from Plan 05-02). These are outside Plan 05-03 scope and were left untouched. They do not affect Plan 05-03 test results.

## User Setup Required
None - no external service configuration required. ANTHROPIC_API_KEY is read from environment at runtime.

## Next Phase Readiness
- Phase 5 Plan 03 completes the LLM Governance scheduled task track
- All 3 Phase 5 plans (01: core module, 02: approval handler, 03: scheduled tasks) are now implemented
- Worker loop has the full complement of 6 schedulers for production operation
- 468+ tests pass with zero regressions from Plan 05-03 changes

## Self-Check: PASSED
