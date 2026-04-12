---
phase: 13-efficient-episode-cataloging-and-thinker-guest-detection
plan: "03"
subsystem: ingestion
tags: [pipeline-wiring, integration, catalog-promote, handler-registry, admin, migration]

dependency_graph:
  requires:
    - "13-01: scan_episodes_for_thinkers handler, rescan_cataloged_for_thinker handler, podcast_person_parser"
    - "13-02: fetch_youtube_channel handler, YouTubeClient"
  provides:
    - "Fully wired catalog-then-promote pipeline: fetch -> catalog -> scan -> promote"
    - "Handler registry with all 3 new Phase 13 handlers registered"
    - "Rescan trigger on thinker approval and candidate promotion"
    - "Admin source form with YouTube channel dispatch and channel ID parsing"
    - "Partial index on content.status='cataloged' for efficient queries"
  affects:
    - "src/thinktank/handlers/fetch_podcast_feed.py: status changed from 'pending' to 'cataloged'"
    - "src/thinktank/handlers/registry.py: 3 new handler registrations"
    - "src/thinktank/llm/decisions.py: rescan trigger on approval + promotion"
    - "src/thinktank/admin/routers/sources.py: YouTube channel dispatch"
    - "src/thinktank/agent/system_prompt.py: cataloged status and job types documented"

tech_stack:
  added: []
  patterns:
    - "catalog-then-promote: Content starts as 'cataloged', promoted to 'pending' only on thinker match"
    - "Retroactive rescan trigger: thinker approval enqueues rescan_cataloged_for_thinker job"
    - "Source-type dispatch: admin force-refresh routes to correct fetch handler"
    - "Partial index: PostgreSQL WHERE clause index for status='cataloged' filtering"

key_files:
  created:
    - "alembic/versions/phase13_cataloged_index.py"
    - "tests/integration/test_catalog_promote_flow.py"
  modified:
    - "src/thinktank/handlers/fetch_podcast_feed.py"
    - "src/thinktank/handlers/registry.py"
    - "src/thinktank/llm/decisions.py"
    - "src/thinktank/admin/routers/sources.py"
    - "src/thinktank/agent/system_prompt.py"

key-decisions:
  - "Kept tag_content_thinkers registration in registry for backward compatibility with in-flight jobs"
  - "Added rescan trigger to both apply_thinker_decision and promote_candidate_to_thinker for full coverage"
  - "Used source.source_type dispatch in admin force-refresh rather than separate endpoints"

requirements-completed: [CATALOG-07, CATALOG-08, YOUTUBE-04, INTEGRATION-01]

metrics:
  duration: 6min
  completed: "2026-04-12"
  tasks_completed: 3
  tasks_total: 3
  files_created: 2
  files_modified: 5
  tests_added: 4
  tests_passing: 4
---

# Phase 13 Plan 03: Pipeline Wiring and Integration Summary

**Wired the catalog-then-promote pipeline into the live codebase: fetch_podcast_feed creates cataloged content, scan_episodes_for_thinkers promotes matches, thinker approval triggers retroactive rescan, admin dispatches YouTube sources correctly, with 4 end-to-end integration tests proving 80% transcription cost savings on guest sources.**

## What Was Built

### Pipeline Wiring (fetch_podcast_feed.py)

Three surgical changes to the existing RSS handler:
1. **Status change**: Non-skipped episodes now get `status='cataloged'` instead of `'pending'` -- they await thinker scan before being queued for transcription
2. **Job chain**: Enqueues `scan_episodes_for_thinkers` instead of `tag_content_thinkers`, passing `raw_xml` (the RSS XML) in the payload for podcast:person extraction
3. **Docstring update**: Documents the new catalog-then-promote flow

### Handler Registry (registry.py)

Three new handlers registered alongside existing Phase 3-7 handlers:
- `scan_episodes_for_thinkers` -- scans cataloged episodes for thinker name matches
- `fetch_youtube_channel` -- fetches YouTube channel videos via Data API v3
- `rescan_cataloged_for_thinker` -- retroactive scanning when new thinkers approved

Existing `tag_content_thinkers` registration preserved for backward compatibility.

### Rescan Trigger (decisions.py)

Two locations now enqueue `rescan_cataloged_for_thinker` when a thinker is approved:
1. `apply_thinker_decision()` -- when LLM approves a thinker
2. `promote_candidate_to_thinker()` -- when a candidate is promoted to thinker

Both create a Job with `thinker_id` and `thinker_name` in the payload.

### Admin Source Form (sources.py)

- `force_refresh_source()` dispatches `fetch_youtube_channel` for YouTube sources, `fetch_podcast_feed` for all others
- `add_source()` parses YouTube channel ID from URL (`/channel/UC...`) into `source.external_id`

### Agent System Prompt (system_prompt.py)

- Documented the `cataloged` status value in the content table description
- Listed all 12 job types for agent awareness

### Alembic Migration (phase13_cataloged_index.py)

Partial index `ix_content_status_cataloged` on `content.status` WHERE `status = 'cataloged'` for efficient queries by the rescan handler.

## Commits

| Hash | Type | Description |
|------|------|-------------|
| `08f6a48` | feat | Wire catalog-then-promote pipeline: fetch creates cataloged, registers handlers, adds rescan trigger |
| `4103263` | feat | Admin YouTube dispatch, system prompt docs, cataloged index migration |
| `1d5f941` | test | End-to-end integration tests for catalog-then-promote pipeline |

## Test Results

| Suite | Tests | Status |
|-------|-------|--------|
| `tests/integration/test_catalog_promote_flow.py` | 4 | All passing |

### Integration Tests (4)
- **test_full_pipeline_guest_source_efficiency**: 10 episodes fetched as cataloged, scan promotes 2 matching Sam Harris, 8 remain cataloged (80% savings)
- **test_full_pipeline_host_source_all_promoted**: 5 episodes from host source, all promoted to pending with role=primary, confidence=10
- **test_rescan_promotes_after_new_thinker**: 5 cataloged episodes, 1 matching Naval Ravikant promoted after rescan with confidence=7
- **test_existing_pending_episodes_not_demoted**: Pre-existing pending content unaffected by scan handler (D-05 compliance)

## Deviations from Plan

None - plan executed exactly as written.

## Self-Check: PASSED

All 7 files verified present (2 created, 5 modified). All 3 task commits verified in git log (08f6a48, 4103263, 1d5f941).
