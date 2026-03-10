---
phase: 09-thinker-management
plan: 02
subsystem: admin
tags: [fastapi, htmx, jinja2, thinker-detail, candidate-queue, discovery, sqlalchemy, postgresql]

# Dependency graph
requires:
  - phase: 09-thinker-management-01
    provides: "Thinker list page with CRUD, 7 existing endpoints on thinkers router"
provides:
  - "Thinker detail page at /admin/thinkers/{id} with HTMX-loaded sources, content, reviews"
  - "PodcastIndex discovery trigger creating discover_guests_podcastindex jobs"
  - "Candidate queue at /admin/thinkers/candidates with promote/reject actions"
  - "Promote creates new thinker with awaiting_llm status and LLM approval job"
  - "19 integration tests covering all detail, candidate, and discovery endpoints"
affects: [10, 12]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "HTMX on-load sections: hx-trigger='load' for lazy-loading detail page sections"
    - "Raw SQL for JSONB queries: context_snapshot->>'thinker_id' for cross-entity review lookup"
    - "Route ordering: /candidates before /{thinker_id} to prevent FastAPI treating 'candidates' as UUID"
    - "Candidate promotion workflow: create Thinker + Job + update CandidateThinker in single transaction"
    - "Separate HTMX target partial (candidate_list.html) included in full page (candidate_queue.html) for swappable content"

key-files:
  created:
    - src/thinktank/admin/templates/thinker_detail.html
    - src/thinktank/admin/templates/partials/thinker_sources.html
    - src/thinktank/admin/templates/partials/thinker_content.html
    - src/thinktank/admin/templates/partials/thinker_reviews.html
    - src/thinktank/admin/templates/candidate_queue.html
    - src/thinktank/admin/templates/partials/candidate_list.html
    - src/thinktank/admin/templates/partials/discovery_result.html
    - tests/integration/test_admin_thinker_detail.py
  modified:
    - src/thinktank/admin/routers/thinkers.py
    - src/thinktank/admin/templates/thinkers.html

key-decisions:
  - "Raw SQL text() for LLM review JSONB queries -- context_snapshot->>'thinker_id' cannot use ORM filter easily"
  - "Candidate promote sets tier=3 (lowest) by default -- LLM approval will refine tier later"
  - "Discovery result rendered as separate small partial rather than full page reload"
  - "Candidate list extracted as include partial for HTMX swap targeting from promote/reject actions"

patterns-established:
  - "Detail page pattern: main page loads metadata, HTMX lazy-loads sections on page load"
  - "Candidate workflow pattern: promote creates entity + queues LLM job + updates candidate atomically"
  - "Route ordering discipline: literal path segments before parameterized UUID segments"

requirements-completed: [THNK-04, THNK-05, THNK-06]

# Metrics
duration: 4min
completed: 2026-03-10
---

# Phase 9 Plan 02: Thinker Detail Page Summary

**Thinker detail page with HTMX-loaded sources/content/reviews, PodcastIndex discovery trigger, candidate queue with promote/reject workflow, and 19 integration tests**

## Performance

- **Duration:** 4 min
- **Started:** 2026-03-10T06:09:52Z
- **Completed:** 2026-03-10T06:14:30Z
- **Tasks:** 2
- **Files modified:** 10

## Accomplishments
- Thinker detail page at /admin/thinkers/{id} with lazy-loaded HTMX sections for sources, content, and LLM review history
- PodcastIndex discovery trigger button creates discover_guests_podcastindex job from the detail page
- Candidate queue at /admin/thinkers/candidates showing all candidates ordered by appearance count
- Promote workflow creates a new Thinker (tier 3, awaiting_llm), queues llm_approval_check job, and updates candidate to promoted status
- Reject workflow updates candidate status with admin reviewer and timestamp
- 19 integration tests across 8 test classes all passing, no regressions in existing 66 admin tests

## Task Commits

Each task was committed atomically:

1. **Task 1: Thinker detail page, candidate queue, and discovery trigger endpoints** - `14da8a1` (feat)
2. **Task 2: Integration tests for thinker detail, candidates, and discovery trigger** - `6d22d7a` (test)

## Files Created/Modified
- `src/thinktank/admin/routers/thinkers.py` - Added 8 new endpoints (detail, sources/content/reviews partials, discover, candidates, promote, reject)
- `src/thinktank/admin/templates/thinker_detail.html` - Detail page with tier/status badges, HTMX sections, discovery trigger
- `src/thinktank/admin/templates/partials/thinker_sources.html` - Sources table with type, status, errors, last fetched
- `src/thinktank/admin/templates/partials/thinker_content.html` - Content table with title, status, published date, duration (mm:ss)
- `src/thinktank/admin/templates/partials/thinker_reviews.html` - Reviews table with decision color-coding and truncated reasoning
- `src/thinktank/admin/templates/candidate_queue.html` - Full candidate queue page extending base.html
- `src/thinktank/admin/templates/partials/candidate_list.html` - Swappable candidate table with promote/reject inline forms
- `src/thinktank/admin/templates/partials/discovery_result.html` - Success message partial for discovery trigger
- `src/thinktank/admin/templates/thinkers.html` - Added Candidate Queue link button
- `tests/integration/test_admin_thinker_detail.py` - 19 integration tests across 8 test classes

## Decisions Made
- Used raw SQL text() for LLM review JSONB queries since context_snapshot->>'thinker_id' is awkward with ORM filters
- Promoted candidates default to tier 3 (lowest) -- LLM approval refines tier through its review process
- Discovery result uses a small inline partial rather than reloading the entire detail page
- Candidate list extracted as a reusable partial included by the full page and targeted by HTMX swaps
- Candidates route placed before {thinker_id} route to prevent FastAPI interpreting "candidates" as a UUID parameter

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
None.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Phase 9 complete -- all 7 THNK requirements fulfilled
- Thinker management pages fully functional with list, detail, candidates, and discovery
- 39 total Phase 9 integration tests passing (20 from plan 01 + 19 from plan 02)
- 105 total admin integration tests passing (66 existing + 39 new)
- Ready for Phase 10 (Source Management) which builds on thinker/source relationships

## Self-Check: PASSED

- All 8 created files verified on disk
- SUMMARY.md exists in plan directory
- Commit 14da8a1 (Task 1) verified in git log
- Commit 6d22d7a (Task 2) verified in git log

---
*Phase: 09-thinker-management*
*Completed: 2026-03-10*
