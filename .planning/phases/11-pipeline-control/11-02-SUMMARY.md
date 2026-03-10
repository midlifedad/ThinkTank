---
phase: 11-pipeline-control
plan: 02
subsystem: admin
tags: [fastapi, htmx, jinja2, scheduler, system-config, pipeline, admin-dashboard]

# Dependency graph
requires:
  - phase: 11-pipeline-control
    provides: "Pipeline control page at /admin/pipeline with router, templates, job queue browser"
  - phase: 08-dashboard-config
    provides: "Admin dashboard base layout, system_config table patterns, HTMX partial conventions"
provides:
  - "Recurring task scheduler editor at /admin/pipeline with 5 configurable tasks"
  - "Per-task frequency (hours) input with save to system_config JSONB"
  - "Per-task enable/disable toggle persisted to system_config"
  - "Run Now for pipeline tasks creates pending job; LLM tasks show info message"
  - "Scheduler config stored as scheduler_{task_key} rows in system_config table"
affects: [12-agent-chat]

# Tech tracking
tech-stack:
  added: []
  patterns: ["Per-task system_config rows (scheduler_{key}) with JSONB value for frequency/enabled/timestamps"]

key-files:
  created:
    - src/thinktank/admin/templates/partials/scheduler_editor.html
    - tests/integration/test_admin_scheduler.py
  modified:
    - src/thinktank/admin/routers/pipeline.py
    - src/thinktank/admin/templates/pipeline.html

key-decisions:
  - "Each scheduled task stored as individual system_config row (scheduler_{key}) rather than single aggregate row -- enables per-task upsert without read-modify-write conflicts"
  - "LLM tasks tracked for visibility but Run Now returns info message instead of creating job -- worker loop manages their internal schedule"

patterns-established:
  - "Scheduler config pattern: SCHEDULED_TASKS constant defines defaults, system_config rows override, _build_scheduler_context merges both for template rendering"

requirements-completed: [PIPE-03]

# Metrics
duration: 3min
completed: 2026-03-10
---

# Phase 11 Plan 02: Recurring Task Scheduler Summary

**Recurring task scheduler editor with per-task frequency/toggle/Run Now controls, system_config JSONB persistence, and 14 integration tests**

## Performance

- **Duration:** 3 min
- **Started:** 2026-03-10T06:37:20Z
- **Completed:** 2026-03-10T06:41:04Z
- **Tasks:** 2
- **Files modified:** 4

## Accomplishments
- Scheduler editor section on pipeline page showing 5 recurring tasks: Refresh Due Sources, Scan for Candidates, LLM Health Check, LLM Daily Digest, LLM Weekly Audit
- Save endpoint persists frequency to system_config and recalculates next_run_at based on last_run_at or current time
- Toggle endpoint flips enabled/disabled state with next_run_at updated when re-enabled
- Run Now creates pending job for pipeline tasks (refresh_due_sources, scan_for_candidates) and shows info message for LLM tasks
- 14 integration tests covering all scheduler endpoints: partial load, defaults, custom config, save create/update, toggle enable/disable/create, run-now job creation/LLM skip/invalid key

## Task Commits

Each task was committed atomically:

1. **Task 1: Add scheduler editor endpoints and template to pipeline page** - `916f6a0` (feat)
2. **Task 2: Integration tests for scheduler editor endpoints** - `7f41f2b` (test)

## Files Created/Modified
- `src/thinktank/admin/routers/pipeline.py` - Added SCHEDULED_TASKS constant, _utcnow helper, _build_scheduler_context, and 4 scheduler endpoints (partial, save, toggle, run-now)
- `src/thinktank/admin/templates/pipeline.html` - Added Recurring Task Schedule section with HTMX-loaded scheduler editor
- `src/thinktank/admin/templates/partials/scheduler_editor.html` - Scheduler table with frequency inputs, enabled toggles, last/next run times, Run Now buttons
- `tests/integration/test_admin_scheduler.py` - 14 integration tests across 4 test classes

## Decisions Made
- Each scheduled task stored as individual system_config row (`scheduler_{key}`) rather than a single aggregate row -- enables independent per-task upsert without read-modify-write conflicts
- LLM tasks tracked for visibility but Run Now returns an informational message instead of creating a job -- the worker loop manages their internal schedule independently

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
None.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Phase 11 (Pipeline Control) complete: job queue browser, triggers, retry/cancel, detail, and scheduler all done
- All 34 Phase 11 integration tests pass
- All 86 existing admin tests pass (no regressions)
- Ready for Phase 12 (Agent Chat) which depends on all prior v1.1 phases

## Self-Check: PASSED

- All 4 created/modified files verified on disk
- Commit 916f6a0 (Task 1) verified in git log
- Commit 7f41f2b (Task 2) verified in git log
- 14/14 scheduler tests pass
- 34/34 Phase 11 tests pass
- 86/86 existing admin tests pass (no regressions)

---
*Phase: 11-pipeline-control*
*Completed: 2026-03-10*
