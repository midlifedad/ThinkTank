---
phase: 03-content-ingestion-pipeline
plan: 01
subsystem: ingestion
tags: [pure-logic, url-normalization, fingerprint, duration-parser, content-filter, name-matching, feed-parser, tdd]
dependency_graph:
  requires: []
  provides: [url_normalizer, fingerprint, duration, content_filter, name_normalizer, name_matcher, feed_parser]
  affects: [handlers/fetch_podcast_feed, handlers/tag_content_thinkers]
tech_stack:
  added: [feedparser-6.0.12]
  patterns: [pure-functions, tdd-red-green, dataclass-dtos]
key_files:
  created:
    - src/thinktank/ingestion/__init__.py
    - src/thinktank/ingestion/url_normalizer.py
    - src/thinktank/ingestion/fingerprint.py
    - src/thinktank/ingestion/duration.py
    - src/thinktank/ingestion/content_filter.py
    - src/thinktank/ingestion/name_normalizer.py
    - src/thinktank/ingestion/name_matcher.py
    - src/thinktank/ingestion/feed_parser.py
    - tests/unit/test_url_normalizer.py
    - tests/unit/test_fingerprint.py
    - tests/unit/test_duration.py
    - tests/unit/test_content_filter.py
    - tests/unit/test_name_normalizer.py
    - tests/unit/test_name_matcher.py
    - tests/unit/test_feed_parser.py
  modified:
    - pyproject.toml
    - uv.lock
key_decisions:
  - feedparser-dependency: Added feedparser>=6.0.12 as explicit project dependency for RSS/Atom parsing
  - pure-function-architecture: All 7 modules are pure functions with zero I/O, zero async, zero DB access
  - title-precedence-in-matching: name_matcher deduplicates matches per thinker, title match (confidence 9) takes precedence over description match (confidence 6)
  - benign-bozo-ignored: feed_parser only raises ValueError on SAXParseException bozo; CharacterEncodingOverride and other benign bozo types are silently ignored
  - sorted-query-params: URL normalizer sorts remaining query params alphabetically for deterministic canonical URLs
metrics:
  duration: 3min
  completed: 2026-03-09
  tasks_completed: 1
  tasks_total: 1
  tests_added: 61
  tests_total: 194
---

# Phase 03 Plan 01: Pure Logic Ingestion Modules Summary

Seven pure-logic modules for the content ingestion pipeline with 61 unit tests covering URL canonicalization, SHA-256 fingerprinting, iTunes duration parsing, duration/title content filtering, thinker name normalization with title stripping, full-name matching with confidence scoring, and feedparser-based RSS episode extraction.

## What Was Built

### 1. URL Normalizer (`url_normalizer.py`)
- Forces HTTPS, strips `www.`, lowercases netloc (preserves path case)
- Strips tracking params: `utm_source`, `utm_medium`, `utm_campaign`, `utm_term`, `utm_content`, `ref`, `fbclid`, `gclid`
- YouTube canonicalization: `youtu.be/`, `/embed/`, and `/watch?v=` all normalize to `https://youtube.com/watch?v={id}`
- Strips trailing slashes, sorts remaining query params alphabetically
- 11 tests covering all normalization rules

### 2. Content Fingerprint (`fingerprint.py`)
- `sha256(lowercase(title) || date_str || duration)` per spec Section 5.5
- Returns None for empty/None titles (no fingerprint possible)
- None date uses empty string, None duration uses "0"
- Case-insensitive (lowercases title before hashing)
- 6 tests covering basic, edge cases, determinism

### 3. Duration Parser (`duration.py`)
- Handles HH:MM:SS, MM:SS, and raw seconds string formats
- Returns None for None input, empty strings, or unparseable values
- Strips whitespace before parsing
- 8 tests covering all formats and edge cases

### 4. Content Filter (`content_filter.py`)
- `should_skip_by_duration(seconds, min)`: True if episode too short; None duration NOT skipped (conservative)
- `should_skip_by_title(title, patterns)`: Case-insensitive substring matching
- Both functions are pure -- thresholds and patterns passed as parameters (not DB reads)
- 9 tests covering duration and title filtering rules

### 5. Name Normalizer (`name_normalizer.py`)
- Strips titles: Dr, Prof, PhD, Jr, Sr, III, II, IV, Mr, Mrs, Ms, Rev
- Unicode NFC normalization, lowercase, whitespace collapse
- Regex-based title removal handles with/without trailing dots
- 9 tests including unicode and combined title stripping

### 6. Name Matcher (`name_matcher.py`)
- Source owner tagged `role='primary'`, `confidence=10`
- Title exact match: `role='guest'`, `confidence=9`
- Description exact match: `role='guest'`, `confidence=6`
- Full name required (partial "John" does not match "John Smith")
- Title match takes precedence when name appears in both title and description
- Per-thinker deduplication (highest confidence wins)
- 10 tests covering matching, precedence, and edge cases

### 7. Feed Parser (`feed_parser.py`)
- Wraps feedparser library, returns `list[FeedEntry]` dataclasses
- Calls `parse_duration()` for `itunes_duration` field
- Converts `published_parsed` to timezone-naive datetime
- URL extraction: prefers enclosure `href`, falls back to `entry.link`
- Raises `ValueError` for truly broken XML (SAXParseException), ignores benign bozo
- Extracts `show_name` from feed-level `<title>`
- 8 tests using inline XML strings

## Commits

| Hash | Description |
|------|-------------|
| `8b1f5ca` | feat(ingestion): implement pure logic modules for content pipeline |

## Deviations from Plan

None -- plan executed exactly as written.

## Verification

```
$ uv run pytest tests/unit/test_url_normalizer.py tests/unit/test_fingerprint.py tests/unit/test_duration.py tests/unit/test_content_filter.py tests/unit/test_name_normalizer.py tests/unit/test_name_matcher.py tests/unit/test_feed_parser.py -x -q
61 passed in 0.08s

$ uv run pytest tests/unit/ -x -q
194 passed, 1 warning in 0.15s

$ python -c "from src.thinktank.ingestion import url_normalizer, fingerprint, duration, content_filter, name_normalizer, name_matcher, feed_parser"
All 7 modules importable
```

## Self-Check: PASSED

- All 15 created files: FOUND
- Commit 8b1f5ca: FOUND
- All 61 new tests: PASSED
- All 194 unit tests (existing + new): PASSED
- feedparser in pyproject.toml: CONFIRMED
