---
phase: 13-efficient-episode-cataloging-and-thinker-guest-detection
plan: 01
subsystem: ingestion
tags: [rss, xml-parsing, podcast-person, episode-scanning, thinker-matching, content-promotion]

# Dependency graph
requires:
  - phase: 03-content-ingestion
    provides: "Content model, feed_parser, name_matcher, tag_content_thinkers handler"
  - phase: 05-llm-governance
    provides: "Thinker approval workflow, approval_status field"
provides:
  - "podcast_person_parser: extract_podcast_persons() for RSS podcast:person XML tags"
  - "scan_episodes_for_thinkers handler: host/guest promotion engine"
  - "rescan_cataloged_for_thinker handler: retroactive scanning on thinker approval"
  - "RSS fixture with podcast:person namespace tags"
affects: [13-02, 13-03]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "podcast:person XML namespace extraction via stdlib ElementTree"
    - "XML size guard pattern (10MB limit) for resource exhaustion prevention"
    - "Host vs guest source promotion strategy with different confidence levels"
    - "Retroactive ILIKE title matching for newly-approved thinkers"

key-files:
  created:
    - "src/thinktank/ingestion/podcast_person_parser.py"
    - "src/thinktank/handlers/scan_episodes_for_thinkers.py"
    - "src/thinktank/handlers/rescan_cataloged_for_thinker.py"
    - "tests/fixtures/rss/podcast_person.xml"
    - "tests/unit/test_podcast_person_parser.py"
    - "tests/contract/test_scan_episodes_handler.py"
  modified: []

key-decisions:
  - "Used stdlib xml.etree.ElementTree with 10MB size guard instead of defusedxml -- feedparser already handles HTTP layer, risk is limited to supplementary parse"
  - "Retroactive rescan matches on title only (not description) since Content model has no description column"
  - "Retroactive match confidence=7 vs real-time title match confidence=9 to distinguish discovery quality"

patterns-established:
  - "Host source promotion: all episodes promoted with role=primary, confidence=10"
  - "Guest source promotion: only name-matched episodes promoted with role from name_matcher"
  - "Rescan pattern: ILIKE title matching for retroactive thinker discovery"

requirements-completed: [CATALOG-01, CATALOG-02, CATALOG-03, CATALOG-04, CATALOG-05, CATALOG-06]

# Metrics
duration: 5min
completed: 2026-04-12
---

# Phase 13 Plan 01: Core Catalog-Promote Modules Summary

**podcast:person XML parser, scan_episodes_for_thinkers host/guest promotion engine, and rescan_cataloged_for_thinker retroactive scanner with 16 passing tests**

## Performance

- **Duration:** 5 min
- **Started:** 2026-04-12T21:37:46Z
- **Completed:** 2026-04-12T21:42:50Z
- **Tasks:** 2
- **Files modified:** 6

## Accomplishments

- podcast_person_parser extracts guest names from podcast:person XML tags with namespace-aware parsing and 10MB size guard
- scan_episodes_for_thinkers promotes matched cataloged episodes to pending, creating ContentThinker attribution rows (host sources promote all, guest sources only matches)
- rescan_cataloged_for_thinker retroactively promotes cataloged episodes when new thinkers are approved, using ILIKE title matching
- 7 unit tests for XML parsing and 9 contract tests for both handlers covering host/guest promotion, attribution, dedup, and edge cases

## Task Commits

Each task was committed atomically:

1. **Task 1: podcast_person_parser and scan_episodes_for_thinkers handler** - `6ee4bef` (feat)
2. **Task 2 RED: failing contract tests for scan and rescan handlers** - `b393abd` (test)
3. **Task 2 GREEN: rescan_cataloged_for_thinker handler implementation** - `ceef0f5` (feat)

## Files Created/Modified

- `src/thinktank/ingestion/podcast_person_parser.py` - Extracts podcast:person XML tags from RSS feeds, returns dict mapping GUID to person list
- `src/thinktank/handlers/scan_episodes_for_thinkers.py` - Scans cataloged episodes for thinker matches, promotes to pending with ContentThinker attribution
- `src/thinktank/handlers/rescan_cataloged_for_thinker.py` - Retroactive scanning when new thinkers approved, ILIKE title matching with confidence=7
- `tests/fixtures/rss/podcast_person.xml` - RSS fixture with 3 items and podcast:person namespace tags
- `tests/unit/test_podcast_person_parser.py` - 7 pure-logic unit tests for XML parsing
- `tests/contract/test_scan_episodes_handler.py` - 9 contract tests against real PostgreSQL for both handlers

## Decisions Made

- Used stdlib xml.etree.ElementTree with 10MB size guard instead of defusedxml -- feedparser already handles the HTTP layer, risk is limited to the supplementary parse of already-fetched XML
- Retroactive rescan matches on title only (not description) since Content model has no description column -- descriptions are transient, available only in job payloads at fetch time
- Retroactive match confidence=7 (vs real-time title match confidence=9) to distinguish discovery quality and enable downstream prioritization

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

- PostgreSQL test database running on port 5432 (not the default 5433 in conftest.py) -- used TEST_DATABASE_URL environment variable override to connect. No code changes needed, just runtime configuration.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- Three production modules ready for Plan 03 pipeline wiring
- scan_episodes_for_thinkers expects job payload with content_ids, source_id, descriptions, and optional raw_xml
- rescan_cataloged_for_thinker expects job payload with thinker_id and thinker_name
- Both handlers follow JobHandler protocol and integrate with existing name_matcher

## Self-Check: PASSED

All 7 created files verified present. All 3 task commits verified in git log.

---
*Phase: 13-efficient-episode-cataloging-and-thinker-guest-detection*
*Completed: 2026-04-12*
