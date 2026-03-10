---
phase: 09-thinker-management
plan: 01
subsystem: admin
tags: [fastapi, htmx, jinja2, thinker-management, crud, sqlalchemy, postgresql]

# Dependency graph
requires:
  - phase: 08-dashboard-and-system-configuration
    provides: "Admin dashboard with base.html nav, HTMX partial patterns, config editor patterns"
provides:
  - "Thinker management page at /admin/thinkers with 7 endpoints"
  - "Searchable/filterable thinker list with HTMX partials"
  - "Inline add form creating thinkers with awaiting_llm status and llm_approval_check job"
  - "Inline edit form for name, tier, bio, categories, active status"
  - "Toggle active/inactive without data deletion"
  - "20 integration tests covering all CRUD paths"
affects: [09-02, 10, 12]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "HTMX search with debounce: hx-trigger='input changed delay:300ms' with hx-include for combined filters"
    - "Source count via outerjoin subquery: select(Thinker, source_count_sq.c.source_count).outerjoin()"
    - "Category names resolved from ThinkerCategory relationship + separate Category query"
    - "Form multi-select via getlist('category_ids') on request.form()"
    - "populate_existing=True for test DB verification after HTTP endpoint modifications"

key-files:
  created:
    - src/thinktank/admin/routers/thinkers.py
    - src/thinktank/admin/templates/thinkers.html
    - src/thinktank/admin/templates/partials/thinker_list.html
    - src/thinktank/admin/templates/partials/thinker_add_form.html
    - src/thinktank/admin/templates/partials/thinker_edit_form.html
    - tests/integration/test_admin_thinkers.py
  modified:
    - src/thinktank/admin/main.py
    - src/thinktank/admin/templates/base.html

key-decisions:
  - "Slug generation uses regex strip of non-alphanumeric-dash chars after lowercasing and space-to-dash"
  - "Category names resolved per-thinker via separate query rather than eager join, keeping query simple"
  - "Used populate_existing=True in tests to bypass SQLAlchemy identity map caching after HTTP endpoint commits"
  - "Default relevance=5 for all admin-created ThinkerCategory rows"

patterns-established:
  - "Thinker CRUD pattern: router with list partial, add form partial, add POST, edit GET/POST, toggle POST"
  - "Filter combination: hx-include collects all filter inputs for combined query parameters"
  - "Edit form cancel returns to list via hx-get on cancel button instead of page reload"

requirements-completed: [THNK-01, THNK-02, THNK-03, THNK-07]

# Metrics
duration: 6min
completed: 2026-03-10
---

# Phase 9 Plan 01: Thinker List Page Summary

**Thinker management page with HTMX search/filter, inline add form (LLM approval trigger), inline edit form, active toggle, and 20 integration tests**

## Performance

- **Duration:** 6 min
- **Started:** 2026-03-10T05:59:47Z
- **Completed:** 2026-03-10T06:06:14Z
- **Tasks:** 2
- **Files modified:** 8

## Accomplishments
- Full thinker CRUD page at /admin/thinkers with 7 endpoints covering list, search, filter, add, edit, and toggle
- HTMX-powered search with 300ms debounce, tier filter, and active status filter all work independently and combined
- Adding a thinker creates the record with awaiting_llm status and queues an llm_approval_check job in the jobs table
- Toggle active/inactive preserves all thinker data (sources, categories, bio) -- deactivation is non-destructive
- 20 integration tests passing across 5 test classes covering all CRUD paths

## Task Commits

Each task was committed atomically:

1. **Task 1: Thinker list router, page template, and HTMX partials** - `10bd5dd` (feat)
2. **Task 2: Integration tests for thinker list, add, edit, toggle, and search** - `faba34e` (test)

## Files Created/Modified
- `src/thinktank/admin/routers/thinkers.py` - Thinker router with 7 endpoints: page, list partial, add form, add POST, edit GET/POST, toggle active
- `src/thinktank/admin/templates/thinkers.html` - Thinker management page with search/filter bar and HTMX-loaded list
- `src/thinktank/admin/templates/partials/thinker_list.html` - Table partial with name, tier, categories, status, active, sources, actions columns
- `src/thinktank/admin/templates/partials/thinker_add_form.html` - Inline add form with name, tier, bio, category checkboxes
- `src/thinktank/admin/templates/partials/thinker_edit_form.html` - Inline edit form with pre-populated values and active checkbox
- `src/thinktank/admin/main.py` - Added thinkers_router import and registration
- `src/thinktank/admin/templates/base.html` - Added Thinkers nav link after Dashboard
- `tests/integration/test_admin_thinkers.py` - 20 integration tests across 5 test classes

## Decisions Made
- Slug generation strips non-alphanumeric-dash characters after lowercasing, sufficient for URL slugs from thinker names
- Category names resolved per-thinker via individual Category queries rather than complex join, keeping the main query simple
- Default relevance=5 for all admin-created ThinkerCategory rows (can be refined later in edit)
- Used `populate_existing=True` execution option in tests to bypass SQLAlchemy identity map caching when verifying DB state after HTTP endpoint commits in a separate session

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed SQLAlchemy identity map caching in integration tests**
- **Found during:** Task 2 (integration tests)
- **Issue:** Test session cached Thinker objects in identity map; after HTTP endpoint modified data via its own session, test queries returned stale cached objects
- **Fix:** Added `execution_options(populate_existing=True)` to verification queries and extracted `_verify_thinker()` helper
- **Files modified:** tests/integration/test_admin_thinkers.py
- **Verification:** All 20 tests pass with correct DB state verification
- **Committed in:** faba34e (Task 2 commit)

---

**Total deviations:** 1 auto-fixed (1 bug)
**Impact on plan:** Auto-fix necessary for test correctness. No scope creep.

## Issues Encountered
None beyond the auto-fixed test caching issue above.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Thinker list page complete with full CRUD, ready for Plan 09-02 (thinker detail page)
- All 7 endpoints registered and working with HTMX partials
- Nav bar updated with Thinkers link for easy access
- 67 total admin integration tests passing (47 existing + 20 new)

## Self-Check: PASSED

- All 7 created files verified on disk
- SUMMARY.md exists in plan directory
- Commit 10bd5dd (Task 1) verified in git log
- Commit faba34e (Task 2) verified in git log

---
*Phase: 09-thinker-management*
*Completed: 2026-03-10*
