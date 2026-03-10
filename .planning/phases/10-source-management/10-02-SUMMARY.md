---
phase: 10-source-management
plan: 02
subsystem: ui
tags: [fastapi, htmx, jinja2, sqlalchemy, admin, sources, detail-page]

# Dependency graph
requires:
  - phase: 10-source-management
    provides: "Source management router with list, add, approve, reject, force-refresh endpoints"
provides:
  - "Source detail page at /admin/sources/{id} with health summary card"
  - "Episodes HTMX partial showing content items for a source"
  - "Error history HTMX partial showing failed fetch_podcast_feed jobs via raw SQL JSONB query"
  - "7 integration tests covering detail page, episodes, and error history"
affects: [12-agent-chat]

# Tech tracking
tech-stack:
  added: []
  patterns: [htmx-lazy-load-detail-sections, raw-sql-jsonb-payload-query-for-errors]

key-files:
  created:
    - src/thinktank/admin/templates/source_detail.html
    - src/thinktank/admin/templates/partials/source_episodes.html
    - src/thinktank/admin/templates/partials/source_errors.html
    - tests/integration/test_admin_source_detail.py
  modified:
    - src/thinktank/admin/routers/sources.py

key-decisions:
  - "Raw SQL text() for JSONB payload->>'source_id' query on jobs table for error history"
  - "Route ordering: episode/error partials before /{source_id} to prevent FastAPI UUID parsing conflict"

patterns-established:
  - "Source error history: raw SQL querying jobs table by payload JSONB field"
  - "Duration formatting: minutes:seconds via Jinja2 format filter with integer division"

requirements-completed: [SRC-05]

# Metrics
duration: 3min
completed: 2026-03-10
---

# Phase 10 Plan 02: Source Detail Page Summary

**Source detail page with health summary, HTMX lazy-loaded episodes table and error history via JSONB payload query**

## Performance

- **Duration:** 3 min
- **Started:** 2026-03-10T06:37:10Z
- **Completed:** 2026-03-10T06:39:59Z
- **Tasks:** 2
- **Files modified:** 5

## Accomplishments
- Source detail page at /admin/sources/{id} with health summary card showing approval status, active state, type, URL, last fetched, item count, error count, and created date
- Episodes partial lazy-loaded via HTMX showing content titles, status (color-coded), published dates, and durations (mm:ss format)
- Error history partial lazy-loaded via HTMX using raw SQL to query failed fetch_podcast_feed jobs by JSONB payload source_id
- 7 integration tests across 3 test classes covering detail page, episodes, and error history

## Task Commits

Each task was committed atomically:

1. **Task 1: Source detail page with health summary, episodes partial, and errors partial** - `ec7b61e` (feat)
2. **Task 2: Integration tests for source detail, episodes, and errors** - `18df800` (test)

## Files Created/Modified
- `src/thinktank/admin/routers/sources.py` - Added 3 new endpoints: detail page, episodes partial, errors partial (10 total routes)
- `src/thinktank/admin/templates/source_detail.html` - Source detail page with health summary card and HTMX sections
- `src/thinktank/admin/templates/partials/source_episodes.html` - Episodes table with title, status, date, duration columns
- `src/thinktank/admin/templates/partials/source_errors.html` - Error history table with date, category, message, completed columns
- `tests/integration/test_admin_source_detail.py` - 7 integration tests across 3 test classes

## Decisions Made
- Used raw SQL `text()` for querying jobs table by JSONB `payload->>'source_id'` field, matching the Phase 9 pattern for JSONB queries
- Placed `/{source_id}/partials/episodes` and `/{source_id}/partials/errors` routes before `/{source_id}` detail route to maintain correct FastAPI path resolution

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
None

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Phase 10 (Source Management) fully complete: list, add, approve, reject, force-refresh, detail, episodes, errors
- All 22 source management tests pass (15 from plan 01 + 7 from plan 02)
- Ready for Phase 12 (Agent Chat) which depends on Phase 10

---
*Phase: 10-source-management*
*Completed: 2026-03-10*
