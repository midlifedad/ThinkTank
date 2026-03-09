---
phase: 03-content-ingestion-pipeline
plan: 04
subsystem: ingestion-handlers
tags: [content-attribution, trigram-dedup, pg_trgm, contract-tests, candidate-dedup]
dependency_graph:
  requires:
    - 03-01 (pure logic modules: name_matcher, name_normalizer)
    - 03-03 (handlers: fetch_podcast_feed, refresh_due_sources, handler registry)
  provides: [tag_content_thinkers handler, trigram similarity module, content attribution, contract tests]
  affects: [Phase 6 cascade discovery, candidate thinker pipeline, worker loop dispatch]
tech_stack:
  added: []
  patterns: [CAST-for-asyncpg-pg_trgm, composite-pk-dedup, contract-test-pattern]
key_files:
  created:
    - src/thinktank/ingestion/trigram.py
    - src/thinktank/handlers/tag_content_thinkers.py
    - tests/integration/test_tag_content.py
    - tests/integration/test_trigram_dedup.py
    - tests/contract/test_ingestion_handlers.py
    - tests/contract/conftest.py
  modified:
    - src/thinktank/handlers/registry.py
    - tests/integration/test_migrations.py
key_decisions:
  - cast-syntax-for-asyncpg: Used CAST(:name AS text) instead of :name::text because SQLAlchemy text() parser conflicts with PostgreSQL :: cast syntax when the left operand is a bind parameter
  - composite-pk-dedup: ContentThinker dedup uses session.get() on composite PK (content_id, thinker_id) before insert rather than catching IntegrityError
  - v1-no-ner-extraction: tag_content_thinkers does NOT scan for arbitrary names in text (no NER) -- that is Phase 6 DISC-01 scan_for_candidates
  - contract-conftest-autocleanup: Added conftest.py to tests/contract/ with autouse _auto_cleanup fixture to support DB-backed contract tests
metrics:
  duration: 6min
  completed: 2026-03-09
  tasks_completed: 2
  tasks_total: 2
  tests_added: 16
  tests_total: 311
---

# Phase 03 Plan 04: Content Attribution, Trigram Dedup, and Contract Tests Summary

tag_content_thinkers handler creating ContentThinker attribution rows with role/confidence scoring (primary/10, guest/9, guest/6), pg_trgm trigram similarity module for candidate dedup at 0.7 threshold using CAST syntax for asyncpg compatibility, and contract tests for all three Phase 3 handlers per QUAL-04.

## What Was Built

### 1. Trigram Similarity Module (`trigram.py`)
- `find_similar_candidates(session, normalized_name, threshold=0.7)`: Queries candidate_thinkers using pg_trgm `similarity()` function, returns (id, name, score) tuples above threshold
- `find_similar_thinkers(session, normalized_name, threshold=0.7)`: Queries thinkers table to prevent candidate creation for existing thinkers under variant names
- Uses `CAST(:name AS text)` instead of `:name::text` to avoid SQLAlchemy/asyncpg bind parameter conflict with PostgreSQL cast syntax
- Results ordered by similarity descending, UUIDs cast to str

### 2. tag_content_thinkers Handler (`tag_content_thinkers.py`)
- Reads canonical payload schema from fetch_podcast_feed: `{content_ids, source_id, descriptions}`
- Loads all active approved thinkers, builds name list for matching
- Resolves source owner name from source -> thinker relationship
- For each content item: calls `match_thinkers_in_text()` with title, description, thinker list, and source owner name
- Creates ContentThinker rows with composite PK dedup (checks `session.get()` before insert)
- Skips content with `status='skipped'` or missing content
- v1 simplification: no NER/name extraction for candidate discovery (Phase 6)
- Structured logging with attribution and candidate counts

### 3. Handler Registry Update (`registry.py`)
- tag_content_thinkers handler imported and registered at module level
- All three Phase 3 handlers now discoverable via `get_handler()`

### 4. Integration Tests (13 tests)

**test_tag_content.py (7 tests):**
- Source owner tagged as primary with confidence=10
- Title name match tagged as guest with confidence=9
- Description name match (from payload descriptions dict) tagged as guest with confidence=6
- No thinker names in text -> only source owner attribution
- Multiple thinkers in title -> two guest attributions + one primary
- Skipped content not attributed
- Duplicate attribution prevented on second handler run

**test_trigram_dedup.py (6 tests):**
- Similar candidate found above 0.7 threshold
- Dissimilar candidate not found (below threshold)
- Existing thinker blocks candidate creation via find_similar_thinkers
- Candidate appearance_count incrementable on match
- Threshold respected (short names with low similarity rejected)
- GiST index verified on candidate_thinkers.normalized_name

### 5. Contract Tests (3 tests)

**test_ingestion_handlers.py:**
- fetch_podcast_feed contract: approved source + RSS feed -> content rows, source.last_fetched updated, tag_content_thinkers job enqueued with descriptions in payload
- refresh_due_sources contract: due sources in DB -> fetch_podcast_feed jobs created for each
- tag_content_thinkers contract: content + thinkers -> ContentThinker attribution rows with correct roles and confidence

## Commits

| Hash | Description |
|------|-------------|
| `4b079d4` | feat(03-04): add trigram similarity module, tag_content_thinkers handler, register handler |
| `d0093a7` | test(03-04): add attribution, trigram dedup, and contract tests for Phase 3 handlers |

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] CAST syntax for asyncpg pg_trgm parameter binding**
- **Found during:** Task 2 (test execution)
- **Issue:** SQLAlchemy `text()` parser interprets `:name::text` as two bind parameters (`:name` and `:text`) because `:` is the bind marker. asyncpg then sends the wrong parameter types to PostgreSQL's `similarity()` function.
- **Fix:** Changed all pg_trgm queries to use `CAST(:name AS text)` instead of `:name::text`
- **Files modified:** `src/thinktank/ingestion/trigram.py`
- **Commit:** `d0093a7`

**2. [Rule 1 - Bug] Migration test teardown missing pg_trgm restoration**
- **Found during:** Task 2 (full suite regression test)
- **Issue:** `test_migrations.py` uses `DROP SCHEMA public CASCADE` which drops the pg_trgm extension. The teardown fixture calls `Base.metadata.create_all` but does not re-create the extension or GiST index, causing all subsequent trigram tests to fail with "function similarity(text, unknown) does not exist".
- **Fix:** Added `CREATE EXTENSION IF NOT EXISTS pg_trgm` and GiST index recreation to the migration test teardown fixture
- **Files modified:** `tests/integration/test_migrations.py`
- **Commit:** `d0093a7`

## Verification

```
$ uv run pytest tests/integration/test_tag_content.py tests/integration/test_trigram_dedup.py tests/contract/test_ingestion_handlers.py -x -q
16 passed in 0.53s

$ uv run pytest tests/ -x -q
311 passed, 1 warning in 7.47s

$ python -c "from src.thinktank.handlers.registry import get_handler; print(get_handler('fetch_podcast_feed'), get_handler('refresh_due_sources'), get_handler('tag_content_thinkers'))"
<function handle_fetch_podcast_feed at 0x...> <function handle_refresh_due_sources at 0x...> <function handle_tag_content_thinkers at 0x...>
```

## Self-Check: PASSED

- All 8 created/modified files: FOUND
- Commit 4b079d4: FOUND
- Commit d0093a7: FOUND
- All 16 new tests: PASSED
- All 311 tests (full suite): PASSED
- All three handlers registered: CONFIRMED
