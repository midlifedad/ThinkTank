---
phase: 13-efficient-episode-cataloging-and-thinker-guest-detection
plan: "02"
subsystem: ingestion
tags: [youtube, api-client, content-cataloging, handler, filtering]
dependency_graph:
  requires:
    - "13-01: content_filter, config_reader, url_normalizer, fingerprint, rate_limiter"
  provides:
    - "YouTubeClient: quota-efficient YouTube Data API v3 client"
    - "handle_fetch_youtube_channel: handler creating cataloged Content rows from YouTube channels"
  affects:
    - "Worker job routing: new job_type fetch_youtube_channel"
    - "Content pipeline: new content_type 'video' with status 'cataloged'"
tech_stack:
  added: ["google-api-python-client>=2.0"]
  patterns: ["catalog-then-promote (status=cataloged)", "quota-efficient API usage (playlistItems not search)", "ISO 8601 duration parsing", "YouTube category filtering"]
key_files:
  created:
    - src/thinktank/ingestion/youtube_client.py
    - src/thinktank/handlers/fetch_youtube_channel.py
    - tests/unit/test_youtube_client.py
    - tests/contract/test_fetch_youtube_channel.py
    - tests/fixtures/youtube/playlist_items_page1.json
    - tests/fixtures/youtube/video_details_batch.json
  modified:
    - pyproject.toml
    - uv.lock
decisions:
  - "Used playlistItems.list (1 quota unit/page) instead of search.list (100 units/call) for quota efficiency"
  - "SKIP_CATEGORY_IDS = {10, 20, 17} (Music, Gaming, Sports) -- clearly non-interview content"
  - "YouTube-specific skip title patterns: shorts, #shorts, highlights, clip -- merged with global patterns"
  - "Handler uses status='cataloged' per catalog-then-promote pattern (not 'pending' like podcast handler)"
  - "ISO 8601 duration parsed via regex, not external library -- lightweight for PT*H*M*S format"
  - "Lazy import of googleapiclient.discovery.build inside __init__ to avoid import overhead for non-YouTube workers"
metrics:
  duration: 8min
  completed: "2026-04-12"
  tasks_completed: 2
  tasks_total: 2
  files_created: 6
  files_modified: 2
  tests_added: 24
  tests_passing: 24
requirements:
  - YOUTUBE-01
  - YOUTUBE-02
  - YOUTUBE-03
---

# Phase 13 Plan 02: YouTube Client and Channel Fetch Handler Summary

YouTube Data API v3 client using quota-efficient playlistItems.list endpoint (1 unit/page) with fetch_youtube_channel handler creating cataloged Content rows, applying 3-layer filtering (duration/title/category), 2-layer dedup (URL + fingerprint), and chaining to scan_episodes_for_thinkers.

## What Was Built

### YouTubeClient (`src/thinktank/ingestion/youtube_client.py`)

Synchronous client wrapping the Google API Python client for YouTube Data API v3. Key design choices:

- **`playlistItems.list`** (1 quota unit/page of 50 items) instead of `search.list` (100 units/call) -- 100x more quota-efficient
- **UC->UU prefix swap** for uploads playlist ID discovery (0 quota cost vs 3 units for channels.list API call)
- **`videos.list`** for details (duration, description, categoryId) in batches of 50 (1 quota unit/batch)
- **`_parse_iso_duration`**: regex-based ISO 8601 duration parser (PT1H23M45S -> 5025 seconds)
- **`SKIP_CATEGORY_IDS`**: {10=Music, 20=Gaming, 17=Sports} for non-interview content filtering
- **`max_pages`** parameter (default 100) prevents runaway pagination quota exhaustion
- **`quota_used`** property tracks total API quota consumed per client instance

### fetch_youtube_channel Handler (`src/thinktank/handlers/fetch_youtube_channel.py`)

Async handler following `JobHandler` protocol, mirroring `fetch_podcast_feed.py` structure:

1. Loads source, verifies approved + active
2. Reads global and per-source filter config (min_duration, skip patterns)
3. Merges YouTube-specific skip patterns (shorts, #shorts, highlights, clip)
4. Checks rate limit via `check_and_acquire_rate_limit(session, "youtube", ...)`
5. Extracts channel_id from source.external_id or URL regex
6. Creates YouTubeClient, fetches all channel videos
7. For each video: normalizes URL, checks 2-layer dedup, applies 3-layer filtering
8. Creates Content rows with `content_type="video"`, `status="cataloged"` or `"skipped"`
9. Updates source metadata (last_fetched, item_count, backfill_complete)
10. Enqueues `scan_episodes_for_thinkers` with content_ids and descriptions

### Test Fixtures

- `tests/fixtures/youtube/playlist_items_page1.json`: Standard playlistItems.list response with 3 items
- `tests/fixtures/youtube/video_details_batch.json`: Standard videos.list response with 3 items (Education 90min, Education 5min, Music 45min)

## Commits

| Hash | Type | Description |
|------|------|-------------|
| `ee3fedd` | feat | YouTube Data API v3 client with quota-efficient endpoints |
| `3ad4a60` | test | TDD RED: failing contract tests for fetch_youtube_channel |
| `7d1ae72` | feat | TDD GREEN: fetch_youtube_channel handler implementation |

## Test Results

| Suite | Tests | Status |
|-------|-------|--------|
| `tests/unit/test_youtube_client.py` | 17 | All passing |
| `tests/contract/test_fetch_youtube_channel.py` | 7 | All passing |

### Unit Tests (17)
- Duration parsing: full (PT1H23M45S), minutes only, hours+seconds, hours only, seconds only, invalid, empty
- SKIP_CATEGORY_IDS membership verification
- get_uploads_playlist_id: UC prefix shortcut, non-UC fallback, not found error
- list_playlist_videos: items returned, quota tracking
- get_video_details: single batch, multiple batches (>50 videos)
- fetch_all_channel_videos: single page, pagination (2 pages), max_pages limit

### Contract Tests (7)
- Creates cataloged content from YouTube videos
- Duration filtering (short videos -> skipped)
- Category filtering (Music -> skipped)
- Title filtering (#shorts -> skipped)
- Enqueues scan_episodes_for_thinkers job
- URL dedup (pre-existing canonical URL)
- Source metadata updates (last_fetched, item_count, backfill_complete)

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed test video IDs to match YouTube format**
- **Found during:** Task 2 contract test execution
- **Issue:** Test video IDs like "vid_existing" (12 chars) were not matched by the URL normalizer's YouTube regex which expects exactly 11-char IDs, causing dedup failures
- **Fix:** Updated all test video IDs to use 11-character IDs matching real YouTube format (e.g., "kL7mN8oP9qR")
- **Files modified:** tests/contract/test_fetch_youtube_channel.py
- **Commit:** 7d1ae72

**2. [Rule 3 - Blocking] Database port mismatch for contract tests**
- **Found during:** Task 2 contract test execution
- **Issue:** Default TEST_DATABASE_URL in conftest.py uses port 5433, but PostgreSQL runs on port 5432
- **Fix:** Ran tests with correct TEST_DATABASE_URL environment variable (pre-existing issue from Phase 1, documented in STATE.md blockers)
- **No code change needed** -- existing known issue

**3. [Rule 3 - Blocking] Lazy import mock path**
- **Found during:** Task 1 unit test execution
- **Issue:** `patch("src.thinktank.ingestion.youtube_client.build")` failed because `build` is imported lazily inside `__init__`, not at module level
- **Fix:** Changed mock target to `patch("googleapiclient.discovery.build")`
- **Files modified:** tests/unit/test_youtube_client.py
- **Commit:** ee3fedd

## Self-Check: PASSED

All 6 created files verified present. All 3 commit hashes verified in git log.
