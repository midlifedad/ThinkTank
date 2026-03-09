---
phase: 03-content-ingestion-pipeline
plan: 02
subsystem: testing
tags: [rss, xml, pg_trgm, postgresql, alembic, fixtures, trigram]

# Dependency graph
requires:
  - phase: 01-foundation-layer
    provides: SQLAlchemy models including CandidateThinker with normalized_name column
  - phase: 02-job-queue-engine
    provides: Alembic migration chain (002_partial_claim) and test infrastructure
provides:
  - 6 RSS fixture XML files for deterministic feed parsing tests
  - pg_trgm PostgreSQL extension enabled via Alembic migration
  - GiST index on candidate_thinkers.normalized_name for trigram similarity
  - Test database pg_trgm extension and GiST index setup in conftest.py
affects: [03-content-ingestion-pipeline, 05-discovery-engine]

# Tech tracking
tech-stack:
  added: [pg_trgm (PostgreSQL extension)]
  patterns: [checked-in XML fixtures for deterministic testing, extension creation in test conftest before create_all]

key-files:
  created:
    - tests/fixtures/rss/podcast_basic.xml
    - tests/fixtures/rss/podcast_itunes.xml
    - tests/fixtures/rss/podcast_no_duration.xml
    - tests/fixtures/rss/podcast_duplicates.xml
    - tests/fixtures/rss/podcast_short_episodes.xml
    - tests/fixtures/rss/podcast_skip_titles.xml
    - alembic/versions/003_add_pg_trgm_extension.py
  modified:
    - tests/conftest.py

key-decisions:
  - "Manual Alembic migration (not autogenerate) for pg_trgm since CREATE EXTENSION is not ORM-discoverable"
  - "pg_trgm extension created in conftest.py before create_all to match production capabilities in test DB"
  - "GiST index explicitly created in conftest.py since SQLAlchemy create_all does not run Alembic migrations"

patterns-established:
  - "RSS fixture pattern: checked-in XML files in tests/fixtures/rss/ for deterministic feed parsing tests"
  - "Extension setup pattern: CREATE EXTENSION IF NOT EXISTS before Base.metadata.create_all in test conftest"

requirements-completed: [DISC-04]

# Metrics
duration: 3min
completed: 2026-03-09
---

# Phase 3 Plan 02: RSS Fixtures, pg_trgm Migration, and Test Infrastructure Summary

**6 RSS fixture XML files for deterministic feed testing, pg_trgm Alembic migration with GiST index on candidate_thinkers.normalized_name, and test conftest updated for trigram similarity support**

## Performance

- **Duration:** 3 min
- **Started:** 2026-03-09T02:38:24Z
- **Completed:** 2026-03-09T02:41:22Z
- **Tasks:** 1
- **Files modified:** 8

## Accomplishments
- Created 6 RSS fixture XML files covering all integration test scenarios (basic, iTunes extensions, no duration, duplicates, short episodes, skip titles)
- Added Alembic migration 003 enabling pg_trgm extension and GiST index on candidate_thinkers.normalized_name
- Updated tests/conftest.py to enable pg_trgm and create GiST index in the test database

## Task Commits

Each task was committed atomically:

1. **Task 1: Create RSS fixture files, pg_trgm Alembic migration, and update test conftest** - `0e9a491` (feat)

## Files Created/Modified
- `tests/fixtures/rss/podcast_basic.xml` - Standard 3-episode podcast feed (durations 3600/2400/5400s)
- `tests/fixtures/rss/podcast_itunes.xml` - Feed with iTunes namespace (HH:MM:SS and MM:SS duration formats)
- `tests/fixtures/rss/podcast_no_duration.xml` - 2 episodes with no duration metadata
- `tests/fixtures/rss/podcast_duplicates.xml` - Same title/date/duration with different URLs + tracking param URL
- `tests/fixtures/rss/podcast_short_episodes.xml` - Episodes at 120s, 300s, 3600s for duration filtering
- `tests/fixtures/rss/podcast_skip_titles.xml` - 4 episodes: trailer, best-of, full interview, announcement
- `alembic/versions/003_add_pg_trgm_extension.py` - Alembic migration enabling pg_trgm and GiST index
- `tests/conftest.py` - Added pg_trgm extension and GiST index creation to engine fixture

## Decisions Made
- Used manual Alembic migration file (not autogenerate) since CREATE EXTENSION is not ORM-discoverable
- Created pg_trgm extension in test conftest before Base.metadata.create_all to ensure test DB matches production capabilities
- Added explicit GiST index creation in conftest.py since SQLAlchemy create_all does not execute Alembic migrations

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
- Alembic upgrade command required explicit DATABASE_URL since env var was not set in shell; resolved by providing URL directly (pre-existing infrastructure note in STATE.md)
- test_feed_parser.py (pre-existing from future plan) import-errors due to unwritten module; excluded from test run (not in scope)
- Integration tests cannot run due to Docker test database not available (port 5433); unit tests all pass (186/186)

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- RSS fixtures ready for Plans 03 and 04 integration tests
- pg_trgm extension available for trigram similarity queries in Plans 03/04
- Test database matches production capabilities (extension + index)
- All prerequisite infrastructure for content ingestion handlers is in place

---
*Phase: 03-content-ingestion-pipeline*
*Completed: 2026-03-09*
