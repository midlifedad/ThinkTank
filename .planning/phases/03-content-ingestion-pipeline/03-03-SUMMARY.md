---
phase: 03-content-ingestion-pipeline
plan: 03
subsystem: ingestion-handlers
tags: [handlers, rss-polling, dedup, scheduling, config-reader, integration-tests]
dependency_graph:
  requires:
    - 03-01 (pure logic modules: url_normalizer, fingerprint, content_filter, feed_parser)
    - 03-02 (RSS fixture files, pg_trgm migration, test infrastructure)
  provides: [fetch_podcast_feed handler, refresh_due_sources handler, config_reader, handler registration]
  affects: [04-tag_content_thinkers handler, worker loop dispatch, discovery orchestration]
tech_stack:
  added: [httpx (async HTTP client for feed fetching)]
  patterns: [thin-handler-orchestrator, system-config-reads-at-runtime, 3-layer-dedup-pipeline]
key_files:
  created:
    - src/thinktank/ingestion/config_reader.py
    - src/thinktank/handlers/fetch_podcast_feed.py
    - src/thinktank/handlers/refresh_due_sources.py
    - tests/integration/test_fetch_podcast.py
    - tests/integration/test_content_dedup.py
    - tests/integration/test_refresh_due.py
  modified:
    - src/thinktank/handlers/registry.py
key_decisions:
  - thin-handler-pattern: Handlers are thin orchestrators calling pure-logic modules from ingestion/ for all business logic
  - httpx-context-manager: Uses httpx.AsyncClient as async context manager for proper connection cleanup
  - tag-job-descriptions-payload: tag_content_thinkers job payload includes descriptions dict (content_id -> description) for Plan 04 thinker attribution
  - incremental-date-comparison: Incremental mode skips entries where published_at <= source.last_fetched (timezone-naive comparison)
  - make-interval-sql: refresh_due_sources uses MAKE_INTERVAL(hours => refresh_interval_hours) for tier-based scheduling
metrics:
  duration: 5min
  completed: 2026-03-09
  tasks_completed: 2
  tasks_total: 2
  tests_added: 19
  tests_total: 295
---

# Phase 03 Plan 03: Feed Polling, Dedup, Scheduling & Approval Handlers Summary

Two core ingestion handlers (fetch_podcast_feed with 3-layer dedup pipeline and refresh_due_sources with tier-based scheduling), a config reader utility, handler registry registration, and 19 integration tests verifying the full content pipeline against PostgreSQL.

## What Was Built

### 1. Config Reader (`config_reader.py`)
- `get_config_value(session, key, default)`: Async DB read from system_config table with fallback to code defaults
- `get_source_filter_config(source_config, global_min_duration, global_skip_patterns)`: Pure function computing effective filter config from per-source JSONB overrides (min_duration_override, skip_title_patterns_override, additional_skip_patterns)

### 2. Fetch Podcast Feed Handler (`fetch_podcast_feed.py`)
- Full RSS polling pipeline: extract source_id from job payload, verify approval + active, fetch XML via httpx, parse with feedparser
- 3-layer dedup: (1) canonical URL uniqueness, (2) SHA-256 content fingerprint, (3) incremental date filtering
- Duration/title content filtering with per-source config overrides
- Content row insertion with correct metadata (source_owner_id, content_type, show_name, etc.)
- Source updates: last_fetched, item_count, backfill_complete
- Enqueues tag_content_thinkers job with payload containing content_ids, source_id, and descriptions dict
- Structured logging throughout (structlog)

### 3. Refresh Due Sources Handler (`refresh_due_sources.py`)
- PostgreSQL MAKE_INTERVAL-based scheduling query for tier-based refresh
- Finds sources where: active=true AND approved AND (never_fetched OR interval_expired)
- Creates fetch_podcast_feed jobs (priority=2) for each due source

### 4. Handler Registry Update (`registry.py`)
- Both handlers imported and registered at module level
- Discoverable via `get_handler("fetch_podcast_feed")` and `get_handler("refresh_due_sources")`

### 5. Integration Tests (19 tests)

**test_fetch_podcast.py (10 tests):**
- Basic feed poll with metadata verification
- Duplicate poll dedup (zero new rows on second poll)
- Unapproved source silently skipped
- Inactive source silently skipped
- Short episode duration filtering
- Skip title pattern filtering
- Source last_fetched and item_count updates
- Backfill then incremental mode transition
- Tag job enqueued with descriptions dict payload
- Per-source min_duration_override

**test_content_dedup.py (3 tests):**
- URL normalization dedup (tracking params stripped)
- Fingerprint dedup (different URL, same title+date+duration)
- NULL fingerprint rows coexist (PostgreSQL UNIQUE ignores NULLs)

**test_refresh_due.py (6 tests):**
- Due source gets fetch job
- Not-due source skipped
- Never-fetched source is due
- Unapproved source not due
- Inactive source not due
- Orchestrator creates correct number of jobs (2 of 3 sources due)

## Commits

| Hash | Description |
|------|-------------|
| `0b1618f` | feat(03-03): add config reader, fetch_podcast_feed and refresh_due_sources handlers |
| `651625c` | test(03-03): add integration tests for feed polling, dedup, scheduling |

## Deviations from Plan

None -- plan executed exactly as written.

## Verification

```
$ uv run pytest tests/integration/test_fetch_podcast.py tests/integration/test_content_dedup.py tests/integration/test_refresh_due.py -x -q
19 passed in 0.49s

$ uv run pytest tests/ -x -q
295 passed, 1 warning in 5.53s

$ python -c "from src.thinktank.handlers.registry import get_handler; print(get_handler('fetch_podcast_feed'), get_handler('refresh_due_sources'))"
<function handle_fetch_podcast_feed at 0x...> <function handle_refresh_due_sources at 0x...>
```

## Self-Check: PASSED

- All 7 created/modified files: FOUND
- Commit 0b1618f: FOUND
- Commit 651625c: FOUND
- All 19 new tests: PASSED
- All 295 tests (full suite): PASSED
- Both handlers registered: CONFIRMED
