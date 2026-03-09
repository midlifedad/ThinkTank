---
phase: 06-discovery-autonomous-growth
plan: 01
subsystem: discovery
tags: [regex, httpx, podcast-index, listen-notes, sha1-auth, quota, rate-limiting]

# Dependency graph
requires:
  - phase: 03-content-ingestion
    provides: name_normalizer, trigram similarity, CandidateThinker model
  - phase: 02-job-queue-engine
    provides: rate_limiter (check_and_acquire_rate_limit)
  - phase: 01-foundation-layer
    provides: config_reader (get_config_value), Base model, SystemConfig
provides:
  - extract_names() -- regex-based person name extraction from podcast episode metadata
  - ListenNotesClient -- Listen Notes API wrapper with rate limit integration
  - PodcastIndexClient -- Podcast Index API wrapper with SHA-1 auth and rate limit integration
  - check_daily_quota() -- daily candidate quota tracking from system_config
  - should_trigger_llm_review() -- 80% threshold trigger for LLM review
  - get_pending_candidate_count() -- pending_llm queue depth check
affects: [06-02-discovery-handlers, discovery-pipeline, candidate-review]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Title-case regex for name extraction (no IGNORECASE on name capture)"
    - "Honorific pre-strip before regex matching"
    - "Per-request SHA-1 auth token generation for Podcast Index"
    - "src.thinktank import paths in source modules (dual-path avoidance)"

key-files:
  created:
    - src/thinktank/discovery/__init__.py
    - src/thinktank/discovery/name_extractor.py
    - src/thinktank/discovery/listennotes_client.py
    - src/thinktank/discovery/podcastindex_client.py
    - src/thinktank/discovery/quota.py
    - tests/unit/test_name_extractor.py
    - tests/unit/test_listennotes_client.py
    - tests/unit/test_podcastindex_client.py
    - tests/unit/test_quota.py
    - tests/fixtures/listennotes/search_episodes.json
    - tests/fixtures/podcastindex/search_byperson.json
  modified: []

key-decisions:
  - "Title-case requirement on name-capture regex instead of global IGNORECASE to reduce false positives"
  - "Pre-strip honorific titles from text before regex matching to handle 'Interview: Dr. Bob Jones' pattern"
  - "src.thinktank.* import paths in source modules to match project convention and avoid dual-import-path SQLAlchemy errors"

patterns-established:
  - "Pure function name extraction with regex + validation + normalization pipeline"
  - "API client pattern: rate limit check -> httpx request -> raise_for_status -> return json"
  - "Checked-in API response fixtures for unit test isolation"

requirements-completed: [DISC-01, DISC-02, DISC-05]

# Metrics
duration: 8min
completed: 2026-03-09
---

# Phase 6 Plan 01: Discovery Module Foundation Summary

**Regex-based podcast guest name extraction, Listen Notes + Podcast Index API clients with rate limiting, and daily candidate quota tracker with 80% LLM review trigger**

## Performance

- **Duration:** 8 min
- **Started:** 2026-03-09T05:06:45Z
- **Completed:** 2026-03-09T05:14:26Z
- **Tasks:** 2
- **Files created:** 11

## Accomplishments
- Built `src/thinktank/discovery/` package with 4 production modules and comprehensive unit tests
- extract_names() extracts person names from podcast episode metadata using 6 regex patterns with Title Case validation, blocklist filtering, and normalize_name integration
- ListenNotesClient and PodcastIndexClient wrap their respective APIs with rate limit integration via existing check_and_acquire_rate_limit
- PodcastIndexClient generates fresh SHA-1 auth headers per request to avoid token expiry
- Quota tracker reads max_candidates_per_day from system_config, counts today's candidates, and provides 80% threshold trigger for LLM review
- 56 new unit tests (33 name extractor + 23 API/quota), 551 total suite (zero regressions)

## Task Commits

Each task was committed atomically (TDD: RED then GREEN):

1. **Task 1: Name extractor module** - `74c724f` (test: RED), `56603c2` (feat: GREEN)
2. **Task 2: API clients + quota tracker** - `72b0b29` (test: RED), `825d332` (feat: GREEN)

_TDD workflow: failing tests committed first, then implementation to make them pass._

## Files Created/Modified
- `src/thinktank/discovery/__init__.py` - Package init
- `src/thinktank/discovery/name_extractor.py` - Regex-based person name extraction from episode titles/descriptions
- `src/thinktank/discovery/listennotes_client.py` - Listen Notes API wrapper with rate limit integration
- `src/thinktank/discovery/podcastindex_client.py` - Podcast Index API wrapper with SHA-1 auth headers
- `src/thinktank/discovery/quota.py` - Daily candidate quota tracking and cascade pause logic
- `tests/unit/test_name_extractor.py` - 33 tests for name extraction patterns, validation, dedup
- `tests/unit/test_listennotes_client.py` - 4 tests for Listen Notes client (success, rate-limit, headers, HTTP error)
- `tests/unit/test_podcastindex_client.py` - 6 tests for Podcast Index client (success, rate-limit, auth headers, fresh timestamp, URL/params, HTTP error)
- `tests/unit/test_quota.py` - 13 tests for quota (daily quota boundaries, config read, LLM trigger threshold, pending count)
- `tests/fixtures/listennotes/search_episodes.json` - Checked-in Listen Notes API response fixture (3 results, one without RSS)
- `tests/fixtures/podcastindex/search_byperson.json` - Checked-in Podcast Index API response fixture (2 items with feedUrl)

## Decisions Made
- **Title-case regex instead of IGNORECASE on name capture:** Using `[A-Z][a-z]+` (case-sensitive) for name words prevents greedy matching of non-name words like "about", "discussing" in sentences like "with John Smith about AI". Keywords use inline `(?i:...)` for case-insensitive matching.
- **Pre-strip honorific titles before regex matching:** Instead of making patterns handle "Dr.", "Prof." etc., we strip them from text first so "Interview: Dr. Bob Jones" becomes "Interview: Bob Jones" which matches cleanly.
- **src.thinktank.* import paths in source modules:** Project convention uses `src.thinktank.*` in source code and `src.thinktank.*` in tests. Using `thinktank.*` in source caused dual-import-path SQLAlchemy table registration errors.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed IGNORECASE causing greedy name capture**
- **Found during:** Task 1 (Name extractor GREEN phase)
- **Issue:** Using `re.IGNORECASE` on the full pattern made `[A-Z][a-z]+` match any word, causing "with John Smith about AI" to capture "John Smith about AI" (then fail validation due to all-caps "AI"). But "Podcast with John Smith about Music" would capture "John Smith about Music" (4 valid-looking words).
- **Fix:** Used inline `(?i:...)` flag only for keyword matching; name capture requires proper Title Case.
- **Files modified:** src/thinktank/discovery/name_extractor.py
- **Verification:** All 33 name extractor tests pass including the "with-in-sentence" parametrized case.
- **Committed in:** 56603c2 (Task 1 GREEN commit)

**2. [Rule 1 - Bug] Added honorific title pre-stripping for regex matching**
- **Found during:** Task 1 (Name extractor GREEN phase)
- **Issue:** "Interview: Dr. Bob Jones" failed to match because the period after "Dr." broke the `[A-Z][a-z]+` word boundary, preventing capture of "Bob Jones".
- **Fix:** Added `_TITLE_STRIP` regex to remove honorific titles from text before pattern matching.
- **Files modified:** src/thinktank/discovery/name_extractor.py
- **Verification:** test_interview_with_title_stripped passes, returning ["bob jones"].
- **Committed in:** 56603c2 (Task 1 GREEN commit)

**3. [Rule 3 - Blocking] Fixed dual-import-path SQLAlchemy registration error**
- **Found during:** Task 2 (API/quota GREEN phase)
- **Issue:** Using `from thinktank.models.candidate import CandidateThinker` in quota.py while the model itself uses `from src.thinktank.models.base import Base` caused SQLAlchemy to register the `candidate_thinkers` table twice under different module paths.
- **Fix:** Changed all source module imports to `src.thinktank.*` and all test imports to `src.thinktank.*` to match project convention.
- **Files modified:** All 4 source modules + 4 test modules
- **Verification:** All 56 new tests pass, full suite of 551 passes with no regressions.
- **Committed in:** 825d332 (Task 2 GREEN commit)

---

**Total deviations:** 3 auto-fixed (2 bugs, 1 blocking)
**Impact on plan:** All auto-fixes necessary for correctness. No scope creep.

## Issues Encountered
None beyond the auto-fixed deviations above.

## User Setup Required
None - no external service configuration required. API keys are injected at runtime via client constructors.

## Next Phase Readiness
- Discovery module foundation complete with all 4 building blocks
- Plan 02 can compose these modules into handler implementations:
  - `scan_for_candidates` handler uses extract_names() + check_daily_quota()
  - `discover_guests_listennotes` handler uses ListenNotesClient
  - `discover_guests_podcastindex` handler uses PodcastIndexClient
- All modules have comprehensive unit tests; Plan 02 will add integration/contract tests

## Self-Check: PASSED

All 11 created files verified present. All 4 commit hashes verified in git log.
Full test suite: 551 passed, 0 failed.

---
*Phase: 06-discovery-autonomous-growth*
*Completed: 2026-03-09*
