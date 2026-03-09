# Phase 3: Content Ingestion Pipeline - Research

**Researched:** 2026-03-08
**Domain:** RSS feed polling, content deduplication, content filtering, thinker attribution, trigram similarity
**Confidence:** HIGH

## Summary

Phase 3 implements the first real job handlers in ThinkTank: RSS feed polling, three-layer content deduplication, duration/title filtering, tier-based refresh scheduling, content attribution, and discovery orchestration. The foundation is solid -- all 14 SQLAlchemy models exist with correct relationships, the job queue engine is fully operational with claim/retry/backpressure/kill-switch, and the handler registry is ready for Phase 3 handlers to register into.

The core libraries are feedparser (RSS/Atom parsing, already in pyproject.toml implicitly via httpx dependency but needs explicit addition) and httpx (already a dependency, async HTTP client for feed fetching). URL normalization is hand-rolled using Python's stdlib `urllib.parse` because the spec defines a specific normalization ruleset (strip UTMs, force HTTPS, YouTube ID canonicalization) that no single library covers. Content fingerprinting uses hashlib.sha256 from stdlib. The pg_trgm PostgreSQL extension needs to be enabled via an Alembic migration for candidate thinker name deduplication.

**Primary recommendation:** Build three handlers (`fetch_podcast_feed`, `tag_content_thinkers`, `refresh_due_sources`) plus supporting pure-logic modules (URL normalizer, fingerprinter, duration parser, content filter, name matcher). Register all handlers in the existing registry. Use feedparser for RSS parsing, httpx.AsyncClient for HTTP fetching, and raw SQL via SQLAlchemy `text()` for pg_trgm similarity queries.

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| INGEST-01 | RSS feed polling via httpx + feedparser, extracting episodes as content rows | feedparser entry structure research, httpx async fetch patterns, duration parsing from itunes_duration |
| INGEST-02 | URL normalization (strip UTMs, force HTTPS, canonicalize YouTube IDs) with canonical_url unique constraint | urllib.parse stdlib approach, tracking parameter lists, YouTube ID regex patterns |
| INGEST-03 | Content fingerprinting via sha256(title + date + duration) catching cross-platform duplicates | hashlib.sha256 stdlib, fingerprint computation logic, NULL fingerprint handling |
| INGEST-04 | Content filtering by min duration (default 600s) and skip title patterns, with per-source overrides | system_config reads, source.config JSONB override schema, pattern matching |
| INGEST-05 | Source approval workflow -- workers only process sources with approval_status='approved' | Query filtering on Source.approval_status, existing model field confirmed |
| INGEST-06 | Tier-based refresh scheduling (Tier 1: 6h, Tier 2: 24h, Tier 3: 168h) via refresh_due_sources check | SQL interval arithmetic patterns from Phase 2 (LOCALTIMESTAMP, MAKE_INTERVAL), source.refresh_interval_hours field |
| INGEST-07 | Discovery orchestration job coordinating feed checks across all approved sources | refresh_due_sources handler design, job creation patterns, scheduling logic |
| DISC-03 | Content attribution via content_thinkers junction with role and confidence scoring | ContentThinker model, attribution pipeline (source owner, title match, description match), confidence scoring rules |
| DISC-04 | Trigram similarity dedup for candidate thinker names at 0.7 threshold | pg_trgm extension, GiST index on normalized_name, similarity() function, SET pg_trgm.similarity_threshold |
</phase_requirements>

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| feedparser | 6.0.12 | RSS/Atom feed XML parsing | De facto standard Python feed parser; handles all RSS/Atom versions; extracts iTunes podcast extensions (itunes_duration, itunes_author); already referenced in spec Section 2.2 |
| httpx | >=0.28.1 | Async HTTP client for feed fetching | Already a project dependency; async-native; configurable timeouts; spec requires 60s timeout |
| hashlib (stdlib) | N/A | SHA-256 content fingerprinting | Stdlib; spec defines fingerprint as `sha256(lowercase(title) \|\| date_trunc('day', published_at) \|\| coalesce(duration_seconds, 0))` |
| urllib.parse (stdlib) | N/A | URL parsing/normalization | Stdlib; URL decomposition, query parameter filtering, reconstruction |
| unicodedata (stdlib) | N/A | Unicode normalization for candidate names | Stdlib; NFD->NFC normalization per spec Section 5.5 |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| re (stdlib) | N/A | YouTube ID extraction, title pattern matching | URL canonicalization, skip title pattern checks |
| sqlalchemy text() | N/A | Raw SQL for pg_trgm similarity queries | Trigram similarity comparisons not expressible in ORM |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Hand-rolled URL normalizer | url-normalize or urlpy2 library | Spec defines very specific rules (YouTube ID canonicalization, podcast CDN param stripping) that no library covers completely; hand-rolling gives exact control |
| feedparser | podcastparser (gpodder) | podcastparser is podcast-specific and parses duration to seconds natively, but feedparser is already in spec Section 2.2 and handles broader RSS/Atom formats |
| Hand-rolled duration parser | podcastparser.parse_time() | Adding a dependency for one function is not worthwhile; the HH:MM:SS to seconds conversion is ~10 lines |

**Installation:**
```bash
uv add feedparser
```

Note: httpx, sqlalchemy, structlog are already in pyproject.toml dependencies.

## Architecture Patterns

### Recommended Project Structure
```
src/thinktank/
├── ingestion/                    # NEW: All Phase 3 ingestion logic
│   ├── __init__.py
│   ├── feed_parser.py            # RSS parsing wrapper around feedparser
│   ├── url_normalizer.py         # URL canonicalization (pure logic)
│   ├── fingerprint.py            # Content fingerprinting (pure logic)
│   ├── duration.py               # Duration string parsing (pure logic)
│   ├── content_filter.py         # Duration/title filtering (pure logic)
│   ├── name_matcher.py           # Thinker name matching in text (pure logic)
│   └── name_normalizer.py        # Candidate name normalization (pure logic)
├── handlers/                     # EXISTING
│   ├── base.py                   # JobHandler protocol (exists)
│   ├── registry.py               # Handler registry (exists, empty)
│   ├── fetch_podcast_feed.py     # NEW: fetch_podcast_feed handler
│   ├── tag_content_thinkers.py   # NEW: tag_content_thinkers handler
│   └── refresh_due_sources.py    # NEW: refresh_due_sources handler
├── queue/                        # EXISTING (all from Phase 2)
├── models/                       # EXISTING (all from Phase 1)
└── worker/                       # EXISTING (all from Phase 2)
```

### Pattern 1: Pure Logic Modules in `ingestion/`

**What:** All feed parsing, URL normalization, fingerprinting, filtering, and name matching are pure functions with no I/O, no database access, and no async. They take data in and return data out.

**When to use:** Always -- for every piece of logic that does not require a database or network call.

**Why:** This directly follows STANDARDS.md testing pyramid. Unit tests for these modules are fast, require no database, and test the hardest logic in isolation.

**Example:**
```python
# src/thinktank/ingestion/url_normalizer.py
from urllib.parse import urlparse, urlunparse, parse_qs, urlencode
import re

# Tracking parameters to strip (spec Section 5.5)
_TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "ref", "fbclid", "gclid",
}

_YOUTUBE_VIDEO_RE = re.compile(
    r"(?:youtube\.com/watch\?.*v=|youtu\.be/|youtube\.com/embed/)([a-zA-Z0-9_-]{11})"
)

def normalize_url(url: str) -> str:
    """Normalize a URL to canonical form per spec Section 5.5.

    1. Force https://
    2. Strip www.
    3. Strip tracking parameters (utm_*, ref, fbclid, gclid)
    4. YouTube: extract video ID, canonicalize to https://youtube.com/watch?v={id}
    5. Sort remaining query params for deterministic output
    """
    parsed = urlparse(url)

    # Force HTTPS
    scheme = "https"

    # Strip www.
    netloc = parsed.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]

    # YouTube canonicalization
    yt_match = _YOUTUBE_VIDEO_RE.search(url)
    if yt_match:
        return f"https://youtube.com/watch?v={yt_match.group(1)}"

    # Strip tracking params
    query_params = parse_qs(parsed.query, keep_blank_values=False)
    filtered = {
        k: v for k, v in query_params.items()
        if k.lower() not in _TRACKING_PARAMS
    }
    new_query = urlencode(filtered, doseq=True)

    return urlunparse((scheme, netloc, parsed.path.rstrip("/"), "", new_query, ""))
```

### Pattern 2: Handlers as Thin Orchestrators

**What:** Job handlers (in `handlers/`) are thin async functions that orchestrate database reads, call pure logic modules, and write results back. They contain minimal logic themselves.

**When to use:** Every handler follows this pattern.

**Example:**
```python
# src/thinktank/handlers/fetch_podcast_feed.py
async def handle_fetch_podcast_feed(session: AsyncSession, job: Job) -> None:
    """Fetch and parse a podcast RSS feed, inserting new content rows.

    1. Load source from payload.source_id
    2. Verify source is approved + active
    3. Fetch feed XML via httpx
    4. Parse with feedparser
    5. For each entry: normalize URL, compute fingerprint, check dedup, filter, insert
    6. Tag content_thinkers for each new row
    7. Update source.last_fetched and source.item_count
    """
    source_id = job.payload["source_id"]
    source = await session.get(Source, source_id)
    # ... orchestration logic
```

### Pattern 3: Checked-in RSS Fixture Files for Tests

**What:** Real RSS feed XML files saved in `tests/fixtures/rss/` for deterministic testing. Mock httpx to return these fixtures instead of making real network calls.

**When to use:** All integration and unit tests for feed parsing.

**Example fixture files:**
```
tests/fixtures/rss/
├── podcast_basic.xml          # Standard podcast RSS feed with 3 episodes
├── podcast_itunes.xml         # Feed with iTunes extensions (duration, author)
├── podcast_no_duration.xml    # Feed with episodes missing duration
├── podcast_duplicates.xml     # Feed with duplicate URLs for dedup testing
├── podcast_short_episodes.xml # Feed with episodes under min_duration
└── podcast_skip_titles.xml    # Feed with "trailer", "best of" titles
```

### Pattern 4: System Config Reads via Helper

**What:** Read system_config values (min_duration_seconds, skip_title_patterns) via a helper function that queries the DB, falling back to code defaults.

**When to use:** Whenever a handler needs config values that may change at runtime.

**Example:**
```python
async def get_config_value(session: AsyncSession, key: str, default: Any) -> Any:
    """Read a system_config value, falling back to default."""
    result = await session.execute(
        select(SystemConfig.value).where(SystemConfig.key == key)
    )
    row = result.scalar_one_or_none()
    return row if row is not None else default
```

### Anti-Patterns to Avoid

- **Parsing RSS in the handler directly:** Extract all feedparser logic into `ingestion/feed_parser.py` so it can be unit-tested without async/DB
- **Fetching system_config values at module load time:** Config changes at runtime via the admin dashboard; always read at job execution time
- **Using ORM queries for trigram similarity:** pg_trgm's `similarity()` function and `%` operator are not in SQLAlchemy's ORM; use `text()` raw SQL
- **Treating feedparser's `entry.get()` as always present:** Many podcast feeds omit itunes_duration, published date, or enclosure; always use `.get()` with defaults
- **Hardcoding filter thresholds:** All thresholds come from system_config (min_duration_seconds, skip_title_patterns) with code defaults as fallback

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| RSS/Atom parsing | Custom XML parser | feedparser 6.0.12 | feedparser handles 10+ RSS/Atom versions, character encoding, malformed XML, iTunes extensions, bozo detection |
| HTTP fetching with timeout | Custom socket/urllib | httpx.AsyncClient | Already a dependency; handles redirects, retries, timeouts, connection pooling; spec requires 60s timeout |
| Content fingerprint hashing | Custom hash function | hashlib.sha256 | Stdlib, battle-tested, spec explicitly says "sha256" |
| Exponential backoff/retry | Custom retry loop | Existing queue/retry.py | Phase 2 already built this; handlers just raise exceptions and the worker loop handles retry |
| Job claiming/completion | Custom job lifecycle | Existing queue/claim.py | Phase 2 already built claim_job/complete_job/fail_job |
| Error categorization | Custom error mapping | Existing queue/errors.py | ErrorCategory enum and categorize_error() already exist; may need new categories added |

**Key insight:** Phase 3 handlers build ON TOP of Phase 2 infrastructure. The handlers never manage their own retry, backoff, or completion -- they raise on failure and return on success. The worker loop handles everything else.

## Common Pitfalls

### Pitfall 1: feedparser Duration Format Variability
**What goes wrong:** `itunes_duration` comes in as "3:00", "01:30:00", "5400" (seconds as string), or None. Code that assumes HH:MM:SS crashes on edge cases.
**Why it happens:** The iTunes RSS spec allows multiple duration formats. Some podcasters use tools that emit raw seconds.
**How to avoid:** Write a `parse_duration()` function that handles all three formats: HH:MM:SS, MM:SS, and raw seconds. Return Optional[int] (seconds) and handle None gracefully.
**Warning signs:** Tests only use "01:30:00" format; production feeds use "90:00" or "5400".

### Pitfall 2: Timezone-Naive vs Timezone-Aware Datetime Mismatch
**What goes wrong:** feedparser's `published_parsed` returns a `time.struct_time` in UTC, but the Content model uses TIMESTAMP WITHOUT TIME ZONE columns. Mixing timezone-aware and naive datetimes causes asyncpg errors.
**Why it happens:** Established in Phase 1 decision: all timestamps are timezone-naive.
**How to avoid:** Convert feedparser's `published_parsed` to a naive datetime: `datetime(*entry.published_parsed[:6])`. Never use `datetime.utcnow()` or `datetime.now(UTC)` without `.replace(tzinfo=None)`. The `_now()` pattern from `queue/claim.py` is the reference.
**Warning signs:** asyncpg "cannot compare timestamp with timestamptz" errors in integration tests.

### Pitfall 3: UNIQUE Constraint Violations on Dedup
**What goes wrong:** Inserting content with a canonical_url or content_fingerprint that already exists raises IntegrityError. If not caught, the handler fails and retries, hitting the same error.
**Why it happens:** This is expected behavior -- dedup means rejecting duplicates. But the handler must catch and handle it, not let it propagate as a failure.
**How to avoid:** Check for existing canonical_url BEFORE insert (SELECT first). For fingerprint collisions, log the alias URL and skip insertion. Use `ON CONFLICT DO NOTHING` or catch IntegrityError as a fallback safety net.
**Warning signs:** Jobs failing with `database_error` category on second poll of the same feed.

### Pitfall 4: pg_trgm Extension Not Enabled
**What goes wrong:** Queries using `similarity()` or `%` operator fail with "function similarity(text, text) does not exist".
**Why it happens:** pg_trgm is a PostgreSQL extension that must be explicitly enabled per database.
**How to avoid:** Create an Alembic migration that runs `CREATE EXTENSION IF NOT EXISTS pg_trgm`. This must also be done in the test database setup.
**Warning signs:** Integration tests pass locally (if extension was manually enabled) but fail in CI.

### Pitfall 5: Forgetting to Update source.last_fetched
**What goes wrong:** Without updating `last_fetched`, the `refresh_due_sources` check keeps re-queuing the same source on every run, creating duplicate fetch jobs.
**Why it happens:** `last_fetched` is the cutoff for incremental refreshes and the basis for tier-based scheduling.
**How to avoid:** Always update `source.last_fetched = now` at the END of a successful fetch, after all content has been inserted. Also increment `source.item_count`.
**Warning signs:** Exponentially growing job queue; same feed fetched every hour regardless of tier.

### Pitfall 6: feedparser Bozo Feeds
**What goes wrong:** feedparser sets `feed.bozo = True` when a feed has errors (malformed XML, encoding issues). Code that doesn't check this may process garbage data.
**Why it happens:** Real-world RSS feeds are frequently malformed.
**How to avoid:** Check `feed.bozo` after parsing. If True, check `feed.bozo_exception` type. Some bozo exceptions are benign (CharacterEncodingOverride) and can be ignored. Others (SAXParseException) mean the feed is truly broken and should fail the job with `RSS_PARSE` error category.
**Warning signs:** Content rows with garbled titles or missing metadata from malformed feeds.

### Pitfall 7: NULL Fingerprints Bypassing UNIQUE Constraint
**What goes wrong:** PostgreSQL's UNIQUE constraint ignores NULLs (two NULL values are not considered duplicates). Content without a title produces NULL fingerprints that bypass dedup.
**Why it happens:** Spec says "Null fingerprints (content without a title yet) are excluded from uniqueness checks."
**How to avoid:** This is intentional per the spec. But be aware that content without titles CAN result in duplicates. Title-less content is rare for podcasts (episodes almost always have titles). For safety, the canonical_url UNIQUE constraint (Layer 1) catches most cases.
**Warning signs:** Multiple content rows with NULL fingerprint for the same actual episode.

## Code Examples

### Duration Parsing (Pure Logic)
```python
# src/thinktank/ingestion/duration.py
import re

_HMS_RE = re.compile(r"^(\d+):(\d{2}):(\d{2})$")  # HH:MM:SS
_MS_RE = re.compile(r"^(\d+):(\d{2})$")             # MM:SS

def parse_duration(raw: str | None) -> int | None:
    """Parse itunes_duration to seconds.

    Handles: "01:30:00" (HH:MM:SS), "90:00" (MM:SS), "5400" (raw seconds), None.
    Returns None if unparseable.
    """
    if raw is None:
        return None

    raw = raw.strip()

    # Try HH:MM:SS
    m = _HMS_RE.match(raw)
    if m:
        return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))

    # Try MM:SS
    m = _MS_RE.match(raw)
    if m:
        return int(m.group(1)) * 60 + int(m.group(2))

    # Try raw seconds
    try:
        return int(raw)
    except ValueError:
        return None
```

### Content Fingerprint (Pure Logic)
```python
# src/thinktank/ingestion/fingerprint.py
import hashlib
from datetime import datetime

def compute_fingerprint(
    title: str,
    published_at: datetime | None,
    duration_seconds: int | None,
) -> str | None:
    """Compute content fingerprint per spec Section 5.5 Layer 2.

    fingerprint = sha256(lowercase(title) || date_trunc('day', published_at) || coalesce(duration_seconds, 0))
    Returns None if title is empty (no fingerprint possible).
    """
    if not title:
        return None

    date_str = published_at.strftime("%Y-%m-%d") if published_at else ""
    duration = str(duration_seconds or 0)

    payload = f"{title.lower()}{date_str}{duration}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
```

### Content Filtering (Pure Logic)
```python
# src/thinktank/ingestion/content_filter.py

def should_skip_by_duration(
    duration_seconds: int | None,
    min_duration: int,
) -> bool:
    """Return True if episode should be skipped due to short duration.

    Episodes with no duration are NOT skipped (conservative -- assume long-form).
    """
    if duration_seconds is None:
        return False
    return duration_seconds < min_duration


def should_skip_by_title(
    title: str,
    skip_patterns: list[str],
) -> bool:
    """Return True if title matches any skip pattern (case-insensitive).

    Uses substring matching per spec Section 5.7.
    """
    title_lower = title.lower()
    return any(pattern.lower() in title_lower for pattern in skip_patterns)
```

### Name Normalization (Pure Logic)
```python
# src/thinktank/ingestion/name_normalizer.py
import re
import unicodedata

_TITLE_PATTERNS = re.compile(
    r"\b(Dr|Prof|Ph\.?D|Jr|Sr|III|II|IV|Mr|Mrs|Ms|Rev)\b\.?\s*",
    re.IGNORECASE,
)

def normalize_name(name: str) -> str:
    """Normalize a thinker name for dedup comparison per spec Section 5.5.

    1. Lowercase
    2. Strip titles (Dr., Prof., Ph.D., Jr., Sr., III)
    3. Unicode normalize (NFD -> NFC)
    4. Collapse whitespace
    """
    # Unicode normalize
    name = unicodedata.normalize("NFC", name)
    # Lowercase
    name = name.lower()
    # Strip titles
    name = _TITLE_PATTERNS.sub("", name)
    # Collapse whitespace
    name = re.sub(r"\s+", " ", name).strip()
    return name
```

### Trigram Similarity Query (SQL)
```python
# In handler or query module
from sqlalchemy import text

async def find_similar_candidates(
    session: AsyncSession,
    normalized_name: str,
    threshold: float = 0.7,
) -> list[tuple[str, str, float]]:
    """Find candidate_thinkers with similar normalized_name using pg_trgm.

    Returns list of (id, name, similarity_score) above threshold.
    """
    result = await session.execute(
        text("""
            SELECT id, name, similarity(normalized_name, :name) AS sml
            FROM candidate_thinkers
            WHERE similarity(normalized_name, :name) > :threshold
            ORDER BY sml DESC
        """),
        {"name": normalized_name, "threshold": threshold},
    )
    return [(str(row[0]), row[1], row[2]) for row in result.fetchall()]


async def find_similar_thinkers(
    session: AsyncSession,
    normalized_name: str,
    threshold: float = 0.7,
) -> list[tuple[str, str, float]]:
    """Check if a candidate name matches an existing thinker using pg_trgm.

    Prevents candidates that already exist as thinkers.
    """
    result = await session.execute(
        text("""
            SELECT id, name, similarity(lower(name), :name) AS sml
            FROM thinkers
            WHERE similarity(lower(name), :name) > :threshold
            ORDER BY sml DESC
        """),
        {"name": normalized_name, "threshold": threshold},
    )
    return [(str(row[0]), row[1], row[2]) for row in result.fetchall()]
```

### Refresh Due Sources Query (SQL)
```python
# In refresh_due_sources handler
from sqlalchemy import text

async def get_refresh_due_sources(session: AsyncSession) -> list[Source]:
    """Find sources due for refresh based on tier scheduling.

    Per spec Section 5.6:
    - active = true AND approval_status = 'approved'
    - last_fetched + (refresh_interval_hours * '1 hour') < NOW()
    - OR last_fetched IS NULL (never fetched)
    """
    result = await session.execute(
        text("""
            SELECT id FROM sources
            WHERE active = true
              AND approval_status = 'approved'
              AND (
                  last_fetched IS NULL
                  OR last_fetched + MAKE_INTERVAL(hours => refresh_interval_hours) < LOCALTIMESTAMP
              )
        """)
    )
    source_ids = [row[0] for row in result.fetchall()]
    # Load full Source objects
    sources = []
    for sid in source_ids:
        s = await session.get(Source, sid)
        if s:
            sources.append(s)
    return sources
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| feedparser 5.x (last release 2015) | feedparser 6.0.12 (2025) | 2020 | v6 dropped Python 2, cleaned up API; `published_parsed` returns `time.struct_time` as before |
| requests for HTTP | httpx.AsyncClient | 2020+ | httpx is async-native, already in project deps; spec requires it |
| Levenshtein distance for fuzzy matching | pg_trgm trigram similarity | PostgreSQL 9.1+ | Trigram is index-backed (GiST), works with `%` operator, threshold configurable via GUC; spec explicitly requires pg_trgm |
| SQLite for development | PostgreSQL everywhere | Project decision | pg_trgm, SKIP LOCKED, advisory locks, JSONB all require real Postgres; tests run against Postgres per STANDARDS.md |

**Deprecated/outdated:**
- feedparser 5.x: Deprecated, Python 2 era. Use 6.0.12.
- `set_limit()` function in pg_trgm: Deprecated in favor of `pg_trgm.similarity_threshold` GUC parameter.

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 8.x + pytest-asyncio 0.25.x |
| Config file | `pyproject.toml` [tool.pytest.ini_options] |
| Quick run command | `uv run pytest tests/unit -x -q` |
| Full suite command | `uv run pytest tests/ -x -q` |

### Phase Requirements -> Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| INGEST-01 | RSS feed parsed into content rows | unit + integration | `uv run pytest tests/unit/test_feed_parser.py tests/integration/test_fetch_podcast.py -x` | Wave 0 |
| INGEST-02 | URL normalization produces canonical URLs | unit | `uv run pytest tests/unit/test_url_normalizer.py -x` | Wave 0 |
| INGEST-03 | Content fingerprint catches cross-platform dupes | unit + integration | `uv run pytest tests/unit/test_fingerprint.py tests/integration/test_content_dedup.py -x` | Wave 0 |
| INGEST-04 | Duration/title filtering sets status='skipped' | unit + integration | `uv run pytest tests/unit/test_content_filter.py tests/integration/test_fetch_podcast.py -x` | Wave 0 |
| INGEST-05 | Unapproved sources never polled | integration | `uv run pytest tests/integration/test_fetch_podcast.py::test_unapproved_source_skipped -x` | Wave 0 |
| INGEST-06 | Tier-based refresh scheduling | integration | `uv run pytest tests/integration/test_refresh_due.py -x` | Wave 0 |
| INGEST-07 | Discovery orchestration creates poll jobs | integration | `uv run pytest tests/integration/test_refresh_due.py::test_orchestrator_creates_jobs -x` | Wave 0 |
| DISC-03 | Content attribution with role/confidence | unit + integration | `uv run pytest tests/unit/test_name_matcher.py tests/integration/test_tag_content.py -x` | Wave 0 |
| DISC-04 | Trigram dedup at 0.7 threshold | integration | `uv run pytest tests/integration/test_trigram_dedup.py -x` | Wave 0 |

### Sampling Rate
- **Per task commit:** `uv run pytest tests/unit -x -q`
- **Per wave merge:** `uv run pytest tests/ -x -q`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `tests/unit/test_feed_parser.py` -- covers INGEST-01 (feedparser wrapper unit tests)
- [ ] `tests/unit/test_url_normalizer.py` -- covers INGEST-02 (URL normalization pure logic)
- [ ] `tests/unit/test_fingerprint.py` -- covers INGEST-03 (fingerprint computation)
- [ ] `tests/unit/test_content_filter.py` -- covers INGEST-04 (duration/title filtering)
- [ ] `tests/unit/test_duration.py` -- covers INGEST-01 (duration parsing edge cases)
- [ ] `tests/unit/test_name_matcher.py` -- covers DISC-03 (thinker name matching)
- [ ] `tests/unit/test_name_normalizer.py` -- covers DISC-04 (name normalization)
- [ ] `tests/integration/test_fetch_podcast.py` -- covers INGEST-01/02/03/04/05 (full handler)
- [ ] `tests/integration/test_refresh_due.py` -- covers INGEST-06/07 (scheduling + orchestration)
- [ ] `tests/integration/test_tag_content.py` -- covers DISC-03 (attribution handler)
- [ ] `tests/integration/test_trigram_dedup.py` -- covers DISC-04 (pg_trgm similarity)
- [ ] `tests/fixtures/rss/` -- checked-in RSS fixture files per STANDARDS.md
- [ ] `tests/contract/test_ingestion_handlers.py` -- contract tests per QUAL-04
- [ ] Alembic migration for `CREATE EXTENSION IF NOT EXISTS pg_trgm` and GiST index on `candidate_thinkers.normalized_name`
- [ ] feedparser added to pyproject.toml dependencies: `uv add feedparser`

## Open Questions

1. **Per-source config override schema validation**
   - What we know: `sources.config` is JSONB with keys like `min_duration_override`, `skip_title_patterns_override`, `additional_skip_patterns`, `host_name`, `known_guests` per spec Section 5.7
   - What's unclear: Whether to validate config schema with Pydantic or treat it as free-form dict
   - Recommendation: Use a Pydantic model (`SourceConfig`) for parsing source.config with Optional fields and defaults. This gives type safety without requiring DB schema changes.

2. **Backfill vs incremental mode decision point**
   - What we know: First run fetches within `approved_backfill_days`, subsequent runs only fetch after `last_fetched`. `backfill_complete` flag controls this.
   - What's unclear: Whether backfill should be a separate handler or a mode within `fetch_podcast_feed`
   - Recommendation: Single handler with backfill mode. If `source.backfill_complete = False`, fetch all entries within backfill window. After processing, set `backfill_complete = True`. If `backfill_complete = True`, only process entries with `published_parsed` after `source.last_fetched`.

3. **pg_trgm GiST index in test database**
   - What we know: The test database is created via `Base.metadata.create_all()` in conftest.py, which does not run Alembic migrations or create extensions.
   - What's unclear: How to ensure pg_trgm is available in the test database
   - Recommendation: Add `CREATE EXTENSION IF NOT EXISTS pg_trgm` to the engine fixture in conftest.py before `create_all()`. The Alembic migration handles production; the test fixture handles tests.

## Sources

### Primary (HIGH confidence)
- [PostgreSQL pg_trgm Documentation](https://www.postgresql.org/docs/current/pgtrgm.html) - similarity function, % operator, GiST indexes, GUC threshold parameter
- [feedparser PyPI](https://pypi.org/project/feedparser/) - version 6.0.12, Python 3.6+ support
- [feedparser enclosures docs](https://feedparser.readthedocs.io/en/releases/reference-entry-enclosures.html) - enclosure structure (href, length, type)
- ThinkTank_Specification.md Sections 5.5-5.7, 6.6 - deduplication strategy, content filtering, attribution pipeline
- Existing codebase: models, queue, handlers, worker, factories, conftest.py

### Secondary (MEDIUM confidence)
- [httpx Timeouts Documentation](https://www.python-httpx.org/advanced/timeouts/) - timeout configuration patterns
- [feedparser common RSS elements](https://feedparser.readthedocs.io/en/latest/common-rss-elements/) - entry attribute reference
- [httpx Async Support](https://www.python-httpx.org/async/) - AsyncClient patterns

### Tertiary (LOW confidence)
- feedparser `itunes_duration` attribute availability - verified via test XML file references in feedparser repo, but exact normalization behavior needs validation with real feeds

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH - feedparser and httpx are specified in the project spec; pg_trgm is PostgreSQL built-in; URL normalization uses stdlib
- Architecture: HIGH - follows established project patterns from Phase 1/2 (pure logic modules, handler protocol, factory functions, integration/conftest.py)
- Pitfalls: HIGH - based on known feedparser behaviors, PostgreSQL constraint semantics, and established project decisions (timezone-naive timestamps)
- Validation: HIGH - test infrastructure fully established in Phase 1/2; framework, fixtures, and patterns are known

**Research date:** 2026-03-08
**Valid until:** 2026-04-08 (stable libraries, well-established patterns)
