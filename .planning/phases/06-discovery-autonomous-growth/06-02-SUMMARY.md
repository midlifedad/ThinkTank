---
phase: 06-discovery-autonomous-growth
plan: 02
subsystem: discovery-handlers
tags: [handlers, registry, error-categories, quota, cascade-pause, api-integration, trigram-dedup]

# Dependency graph
requires:
  - phase: 06-discovery-autonomous-growth
    plan: 01
    provides: extract_names, ListenNotesClient, PodcastIndexClient, check_daily_quota, should_trigger_llm_review, get_pending_candidate_count
  - phase: 03-content-ingestion
    provides: trigram similarity (find_similar_candidates, find_similar_thinkers), url_normalizer, name_normalizer
  - phase: 02-job-queue-engine
    provides: Job model, worker loop, handler registry
provides:
  - handle_scan_for_candidates -- scans episode content for candidate thinker names with quota enforcement
  - handle_discover_guests_listennotes -- discovers guest appearances via Listen Notes API
  - handle_discover_guests_podcastindex -- discovers guest appearances via Podcast Index API
  - LISTENNOTES_RATE_LIMIT and PODCASTINDEX_ERROR error categories
affects: [discovery-pipeline, candidate-review, llm-approval]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Handler pattern: load from payload, check guards, process items, commit, log"
    - "Cascade pause: pending_llm > 40 triggers early return"
    - "API client mocking via call-site patch (handler module namespace)"
    - "URL dedup via normalize_url before Source insert"

key-files:
  created:
    - src/thinktank/handlers/scan_for_candidates.py
    - src/thinktank/handlers/discover_guests_listennotes.py
    - src/thinktank/handlers/discover_guests_podcastindex.py
    - tests/unit/test_discovery_handlers_unit.py
    - tests/integration/test_scan_candidates.py
    - tests/integration/test_discover_guests.py
    - tests/contract/test_discovery_handlers.py
  modified:
    - src/thinktank/handlers/registry.py
    - src/thinktank/queue/errors.py
    - tests/unit/test_errors.py

key-decisions:
  - "Used body_text instead of description for Content field -- Content model has body_text not description"
  - "httpx.HTTPStatusError handling placed before generic Python exceptions in categorize_error to avoid shadowing"
  - "Contract tests follow existing pattern from test_llm_approval_handler.py with pytestmark = pytest.mark.anyio"

patterns-established:
  - "Discovery handler pattern: guard checks (cascade pause, quota) -> iterate items -> dedup via trigram/URL -> create/update rows -> trigger follow-up jobs"
  - "API handler pattern: read env for keys -> create client -> call API -> handle rate limit as ValueError -> iterate results -> dedup -> create Sources"

requirements-completed: [DISC-01, DISC-02, DISC-05]

# Metrics
duration: 5min
completed: 2026-03-09
---

# Phase 6 Plan 02: Discovery Handlers Summary

**Three job handlers wiring discovery modules into the worker loop: name-to-candidate scanning with quota/cascade guards, and Listen Notes + Podcast Index guest discovery with URL-deduplicated source registration**

## Performance

- **Duration:** 5 min
- **Started:** 2026-03-09T05:19:16Z
- **Completed:** 2026-03-09T05:24:24Z
- **Tasks:** 2
- **Files created:** 7
- **Files modified:** 3

## Accomplishments
- Built 3 production handlers that close the discovery loop: content ingestion -> name extraction -> candidate creation -> LLM approval -> thinker activation -> guest discovery -> source registration -> more content ingestion
- scan_for_candidates handler extracts names from episode content via extract_names(), creates/updates CandidateThinker rows with trigram dedup, enforces daily quota, triggers LLM review at 80%, and pauses when pending_llm > 40
- discover_guests_listennotes handler searches Listen Notes API for podcast episodes featuring a thinker, registers discovered feeds as Sources with approval_status=pending_llm, deduplicates by normalized URL
- discover_guests_podcastindex handler searches Podcast Index API by person name, registers feeds as Sources pending LLM approval with URL dedup
- Extended ErrorCategory with LISTENNOTES_RATE_LIMIT and PODCASTINDEX_ERROR members
- Added httpx.HTTPStatusError handling in categorize_error (429 -> RATE_LIMITED, others -> HTTP_ERROR)
- All 3 handlers registered in registry.py under Phase 6 section
- 31 new tests (12 unit + 17 integration/contract + 2 error category), 582 total suite (zero regressions from 551 baseline)

## Task Commits

Each task was committed atomically (TDD: RED then GREEN):

1. **Task 1: Handlers + error extensions + registration** - `a1049d5` (test: RED), `cddce80` (feat: GREEN)
2. **Task 2: Integration + contract tests** - `3eeac47` (test: tests for implemented handlers)

_TDD workflow: failing tests committed first, then implementation to make them pass._

## Files Created/Modified
- `src/thinktank/handlers/scan_for_candidates.py` - Handler scanning episode metadata for candidate thinkers with quota enforcement
- `src/thinktank/handlers/discover_guests_listennotes.py` - Handler discovering guest appearances via Listen Notes API
- `src/thinktank/handlers/discover_guests_podcastindex.py` - Handler discovering guest appearances via Podcast Index API
- `src/thinktank/handlers/registry.py` - Updated with 3 new Phase 6 handler registrations
- `src/thinktank/queue/errors.py` - Extended ErrorCategory with 2 new members + httpx handling
- `tests/unit/test_errors.py` - Updated enum count (17->19), added httpx categorization tests
- `tests/unit/test_discovery_handlers_unit.py` - 12 tests for handler registration and protocol conformance
- `tests/integration/test_scan_candidates.py` - 6 integration tests for scan_for_candidates
- `tests/integration/test_discover_guests.py` - 8 integration tests for both guest discovery handlers
- `tests/contract/test_discovery_handlers.py` - 3 contract tests verifying handler side effects

## Decisions Made
- **Used body_text instead of description for Content field:** The plan referenced `content.description` but the Content model has `body_text` (nullable). Used `content.body_text or ""` to match the actual model field.
- **httpx.HTTPStatusError handling before generic exceptions:** Placed httpx check before `isinstance(exc, TimeoutError)` and `isinstance(exc, ConnectionError)` to prevent generic Python exception types from shadowing more specific httpx errors.
- **Contract tests with pytestmark = pytest.mark.anyio:** Followed the established contract test pattern (from test_llm_approval_handler.py) using pytest.mark.anyio for async test discovery.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Content model uses body_text not description**
- **Found during:** Task 1
- **Issue:** Plan referenced `content.description` but Content model field is `body_text`
- **Fix:** Used `content.body_text or ""` in scan_for_candidates handler
- **Files modified:** src/thinktank/handlers/scan_for_candidates.py
- **Committed in:** cddce80

---

**Total deviations:** 1 auto-fixed (blocking)
**Impact on plan:** Trivial field name correction, no scope impact.

## Issues Encountered
None beyond the field name deviation above.

## User Setup Required
None - API keys are read from environment variables at runtime. No new configuration needed.

## Next Phase Readiness
- All 3 discovery handlers are live in the worker loop
- The complete discovery loop is now functional:
  1. `scan_for_candidates` -> creates CandidateThinker rows from episode content
  2. `llm_approval_check` (Phase 5) -> reviews candidates, promotes to Thinkers
  3. `discover_guests_listennotes` / `discover_guests_podcastindex` -> finds podcast appearances for approved Thinkers
  4. New Sources get LLM approval -> `fetch_podcast_feed` (Phase 3) ingests their content -> cycle continues
- Quota and cascade pause guards prevent unbounded growth

## Self-Check: PASSED

All 7 created files verified present. All 3 commit hashes verified in git log.
Full test suite: 582 passed, 0 failed.

---
*Phase: 06-discovery-autonomous-growth*
*Completed: 2026-03-09*
