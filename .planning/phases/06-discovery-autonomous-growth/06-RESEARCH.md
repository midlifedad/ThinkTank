# Phase 6: Discovery and Autonomous Growth - Research

**Researched:** 2026-03-08
**Domain:** Cascade discovery, podcast guest discovery APIs, daily quota management
**Confidence:** HIGH

## Summary

Phase 6 delivers the autonomous growth engine that transforms ThinkTank from a manually-curated system into a self-expanding corpus. Three capabilities are needed: (1) scanning episode metadata for person names not in the thinkers table and surfacing them as candidates after 3+ appearances (DISC-01), (2) discovering guest appearances via Listen Notes and Podcast Index APIs and registering discovered feeds as sources pending LLM approval (DISC-02), and (3) daily quota limits that prevent unbounded candidate growth with cascade pausing when the queue needs LLM review (DISC-05).

The existing codebase provides strong foundations: the `candidate_thinkers` model and `pg_trgm` trigram similarity are already built (Phase 3), the `name_normalizer` and `name_matcher` modules exist as pure functions, the `rate_limiter` handles sliding-window API coordination, the `llm_approval_check` handler with `candidate_review` flow is complete (Phase 5), and the `backpressure` module already references all Phase 6 job types (`discover_guests_listennotes`, `discover_guests_podcastindex`, `scan_for_candidates`). The work is connecting these building blocks with new API client modules and two new job handlers, plus a quota-checking layer.

**Primary recommendation:** Build in two plans -- Plan 01 for pure logic modules (name extraction from text, Listen Notes client, Podcast Index client, quota tracker) with unit tests, Plan 02 for handlers (`scan_for_candidates`, `discover_guests_listennotes`, `discover_guests_podcastindex`), handler registration, and integration/contract tests.

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| DISC-01 | Cascade discovery -- scan episode titles/descriptions for names not in thinkers table, surface as candidates after 3+ appearances | Name extraction module (regex-based person name extraction from text), existing `name_normalizer`, existing `trigram.py` for dedup, existing `CandidateThinker` model. New `scan_for_candidates` handler. |
| DISC-02 | Guest discovery via Listen Notes and Podcast Index APIs with rate-limited queries | New API client modules for both APIs, using httpx (already a dependency). Rate limiting via existing `check_and_acquire_rate_limit`. New `discover_guests_listennotes` and `discover_guests_podcastindex` handlers. Source registration pending LLM approval. |
| DISC-05 | Daily quota limits on candidate discovery to prevent unbounded growth | New quota tracker module reading `max_candidates_per_day` from `system_config`, counting today's candidates, triggering `quota_check` LLM review when limit approached. Cascade pauses until LLM reviews existing queue. |
</phase_requirements>

## Standard Stack

### Core (already in project)

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| httpx | >=0.28.1 | HTTP client for API calls | Already used for RSS fetching. Async-native, timeout control. Use directly instead of SDK wrappers. |
| SQLAlchemy 2.0 | >=2.0.46 | ORM for all DB operations | Async session pattern already established in all handlers. |
| structlog | >=25.5.0 | Structured logging | Existing pattern with bind() for job context. |
| feedparser | >=6.0.12 | RSS feed parsing | Already used in `feed_parser.py`. Needed for parsing guest feeds from API results. |

### Supporting

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| re (stdlib) | - | Name extraction regex | For extracting person names from episode text (DISC-01) |
| hashlib (stdlib) | - | Podcast Index auth | SHA-1 hash for `Authorization` header |
| time (stdlib) | - | Podcast Index auth | Unix timestamp for `X-Auth-Date` header |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Direct httpx calls | `podcast-api` (Listen Notes SDK) | SDK adds a dependency for a 2-endpoint surface. Direct httpx is simpler, matches existing patterns, and allows tighter error handling. **Use httpx.** |
| Direct httpx calls | `python-podcastindex` | Same reasoning. The package hasn't been updated for modern Python (claims 2.7-3.7 support). **Use httpx.** |

**Installation:**
No new dependencies needed. All required libraries are already in `pyproject.toml`.

## Architecture Patterns

### Recommended Module Structure
```
src/thinktank/
├── discovery/                         # NEW: Phase 6 modules
│   ├── __init__.py
│   ├── name_extractor.py              # Pure: extract person names from text
│   ├── listennotes_client.py          # Async: Listen Notes API wrapper
│   ├── podcastindex_client.py         # Async: Podcast Index API wrapper
│   └── quota.py                       # Async: daily quota tracking + pause logic
├── handlers/
│   ├── scan_for_candidates.py         # NEW: DISC-01 handler
│   ├── discover_guests_listennotes.py # NEW: DISC-02 handler (Listen Notes)
│   ├── discover_guests_podcastindex.py# NEW: DISC-02 handler (Podcast Index)
│   └── registry.py                    # MODIFIED: register 3 new handlers
├── ingestion/
│   ├── name_normalizer.py             # EXISTING: reuse for candidate name normalization
│   ├── name_matcher.py                # EXISTING: reuse for thinker matching
│   └── trigram.py                     # EXISTING: reuse for candidate dedup
└── queue/
    ├── rate_limiter.py                # EXISTING: reuse for API rate limiting
    └── errors.py                      # MODIFIED: add LISTENNOTES_RATE_LIMIT, PODCASTINDEX_ERROR categories
```

### Pattern 1: Pure Function Name Extraction (DISC-01)

**What:** Extract candidate person names from episode titles and descriptions using regex heuristics, not NLP/NER.
**When to use:** Every time `scan_for_candidates` processes content items.
**Rationale:** The specification explicitly says "deliberately simple string matching for v1" and "LLM-assisted NER deferred to Phase 2." Regex patterns for common podcast guest name formats (e.g., "with John Smith", "feat. Jane Doe", "Interview: Dr. Bob Jones") are sufficient and keep the dependency set clean.

```python
# Source: ThinkTank spec Section 5.3, tag_content_thinkers.py comment
# "This handler does NOT perform general NER/name extraction from
#  descriptions. That is Phase 6 (DISC-01 scan_for_candidates)."

import re
from thinktank.ingestion.name_normalizer import normalize_name

# Common patterns in podcast episode titles/descriptions
_GUEST_PATTERNS = [
    # "with John Smith" / "w/ John Smith"
    r"(?:with|w/)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)",
    # "feat. John Smith" / "featuring John Smith"
    r"(?:feat\.?|featuring)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)",
    # "Interview: John Smith" / "Guest: John Smith"
    r"(?:interview|guest|conversation)[:\s]+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)",
    # "John Smith on Topic" (name at start of title)
    r"^([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\s+(?:on|talks|discusses|explains)",
    # "#123 - John Smith" (episode number prefix)
    r"#?\d+\s*[-–—:]\s*([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)",
    # "| John Smith" (pipe separator)
    r"\|\s*([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)",
]

def extract_names(title: str, description: str) -> list[str]:
    """Extract candidate person names from episode metadata.
    Returns deduplicated list of normalized names.
    """
    names = set()
    for text in [title, description]:
        for pattern in _GUEST_PATTERNS:
            for match in re.finditer(pattern, text):
                raw = match.group(1).strip()
                if _looks_like_person_name(raw):
                    names.add(normalize_name(raw))
    return list(names)

def _looks_like_person_name(name: str) -> bool:
    """Basic validation: 2-4 words, reasonable length."""
    parts = name.split()
    return 2 <= len(parts) <= 4 and all(len(p) >= 2 for p in parts)
```

### Pattern 2: API Client with Rate Limit Integration

**What:** Thin httpx wrappers for Listen Notes and Podcast Index that integrate with the existing rate limiter.
**When to use:** Guest discovery handlers call these clients.

```python
# Source: Existing rate_limiter.py pattern + API documentation
import httpx
from sqlalchemy.ext.asyncio import AsyncSession
from thinktank.queue.rate_limiter import check_and_acquire_rate_limit

class ListenNotesClient:
    BASE_URL = "https://listen-api.listennotes.com/api/v2"

    def __init__(self, api_key: str):
        self._api_key = api_key

    async def search_episodes_by_person(
        self,
        session: AsyncSession,
        worker_id: str,
        person_name: str,
        offset: int = 0,
    ) -> dict | None:
        """Search for episodes featuring a person. Returns None if rate-limited."""
        if not await check_and_acquire_rate_limit(session, "listennotes", worker_id):
            return None  # Rate limited

        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.BASE_URL}/search",
                params={"q": person_name, "type": "episode", "offset": offset},
                headers={"X-ListenAPI-Key": self._api_key},
                timeout=30.0,
            )
            response.raise_for_status()
            return response.json()
```

### Pattern 3: Daily Quota with Cascade Pause

**What:** Check `max_candidates_per_day` before creating candidates. When approached, trigger `quota_check` LLM review and pause discovery.
**When to use:** Called by `scan_for_candidates` handler before inserting candidates.

```python
# Source: Spec Section 5.3, system_config defaults
from datetime import datetime, UTC
from sqlalchemy import func, select
from thinktank.models.candidate import CandidateThinker
from thinktank.ingestion.config_reader import get_config_value

async def check_daily_quota(session: AsyncSession) -> tuple[bool, int, int]:
    """Check if daily candidate quota allows more candidates.

    Returns:
        (can_continue, candidates_today, daily_limit)
    """
    daily_limit = await get_config_value(session, "max_candidates_per_day", 20)
    today_start = datetime.now(UTC).replace(
        hour=0, minute=0, second=0, microsecond=0, tzinfo=None
    )
    candidates_today = await session.scalar(
        select(func.count()).select_from(CandidateThinker)
        .where(CandidateThinker.first_seen_at >= today_start)
    ) or 0

    can_continue = candidates_today < daily_limit
    return can_continue, candidates_today, daily_limit
```

### Anti-Patterns to Avoid

- **NLP/NER for name extraction:** Do NOT use spaCy, NLTK, or any ML-based NER. The spec says "simple string matching for v1." Regex patterns for podcast guest name formats are sufficient and avoid heavy dependencies.
- **SDK wrappers for simple APIs:** Do NOT add `podcast-api` or `python-podcastindex` packages. Both APIs have 1-2 endpoints we use. Direct httpx calls match existing patterns and avoid unmaintained dependencies.
- **Blocking on rate limits:** Do NOT loop-retry when rate-limited. Return None or reschedule the job with `scheduled_at` in the future. The worker loop handles rescheduling.
- **Scanning ALL content on each run:** The `scan_for_candidates` handler should process batches of recently-ingested content (since last scan), not re-scan the entire corpus. Track scan progress via job payload or config.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Name normalization | Custom normalizer | `ingestion/name_normalizer.py` | Already handles Unicode NFC, title stripping, case folding. Tested. |
| Candidate dedup | Custom similarity check | `ingestion/trigram.py` | pg_trgm with 0.7 threshold already implemented. GiST index exists. |
| API rate limiting | Per-handler rate logic | `queue/rate_limiter.py` | Sliding-window via `rate_limit_usage` table. Shared across workers. |
| Config reading | Direct SQL for config | `ingestion/config_reader.py` | `get_config_value(session, key, default)` already handles JSONB. |
| LLM candidate review | Custom LLM flow | `handlers/llm_approval_check.py` | Candidate review flow is complete. Just enqueue `llm_approval_check` job with `review_type="candidate_review"`. |
| Candidate promotion | Custom thinker creation | `llm/decisions.py` | `promote_candidate_to_thinker()` already creates Thinker from approved candidate. |
| Error categorization | Custom error mapping | `queue/errors.py` | Extend `ErrorCategory` enum with new categories. `categorize_error()` dispatches. |

**Key insight:** Phase 5 built the entire LLM governance pipeline, including candidate review and promotion. Phase 6 only needs to **feed candidates into the existing pipeline** -- it does not need to build any LLM interaction logic.

## Common Pitfalls

### Pitfall 1: Name Extraction Produces Too Many False Positives

**What goes wrong:** Regex patterns extract non-person strings (company names, show titles, technical terms) from episode descriptions. The candidate table fills with noise like "Artificial Intelligence", "The New York Times", "Machine Learning."
**Why it happens:** Episode descriptions contain many capitalized multi-word strings that match person-name patterns.
**How to avoid:**
1. Apply patterns primarily to **titles** (higher signal-to-noise than descriptions)
2. Validate extracted names: 2-4 words, each word 2+ chars, no all-caps words, no common non-person words (blocklist: "The", "Inc", "LLC", "University", etc.)
3. Check extracted names against existing thinkers via `find_similar_thinkers()` to skip already-known names
4. The 3-appearance threshold acts as a natural noise filter -- random false positives rarely appear 3+ times
**Warning signs:** Candidate table growing faster than 50/day, candidate approval rate below 20%

### Pitfall 2: Listen Notes Free Tier Quota Exhaustion

**What goes wrong:** 10K requests/month sounds like a lot but with 50+ thinkers, each needing 5-20 paginated API calls, the quota exhausts in 1-2 weeks. After exhaustion, guest discovery silently stops (429 errors retry but never succeed until quota resets).
**Why it happens:** The cascade discovery loop surfaces new candidates which trigger more guest searches, creating a multiplicative effect.
**How to avoid:**
1. Implement monthly quota tracking in `api_usage` -- not just hourly rate limiting
2. Prioritize Listen Notes calls by thinker tier: Tier 1 first, Tier 3 last
3. Cache search results: guest appearance results for a thinker should be valid for 30 days (store in job payload or Source config)
4. Use Podcast Index as a **true parallel path** -- search Podcast Index first (free, no quota), fall back to Listen Notes only when Podcast Index returns no results
5. The health check should flag "Listen Notes quota < 20% remaining"
**Warning signs:** `api_usage` showing >75% Listen Notes monthly usage with >7 days left in billing cycle

### Pitfall 3: Podcast Index Authentication Token Expiry

**What goes wrong:** Podcast Index requires SHA-1 hash of `(apiKey + apiSecret + unixTimestamp)` with a 3-minute validity window. If the worker's clock drifts or the token is reused across retries, all requests fail with auth errors.
**Why it happens:** The auth token is time-sensitive. Clock drift, request queuing, or retry delays can push the token past its 3-minute window.
**How to avoid:**
1. Generate a fresh auth token for **every request** (not cached)
2. Use `time.time()` for the timestamp (not a pre-computed value)
3. Categorize Podcast Index auth failures as `API_ERROR` with immediate retry (not exponential backoff)
**Warning signs:** Podcast Index errors with 401 status that succeed on immediate retry

### Pitfall 4: Unbounded Candidate Queue Blocks LLM Review

**What goes wrong:** If `scan_for_candidates` runs faster than LLM can review candidates, the `pending_llm` queue grows without bound. The LLM batch review is bounded to 20 candidates per review (spec Section 8.5), so a queue of 200 candidates takes 10 review cycles to clear.
**Why it happens:** Daily quota (`max_candidates_per_day=20`) limits new candidate creation, but if the LLM is slow or unavailable, the queue accumulates across days.
**How to avoid:**
1. `scan_for_candidates` checks both daily quota AND pending queue depth before creating candidates
2. If `pending_llm` candidates > 40 (2x batch size), pause cascade discovery entirely
3. The quota_check LLM review (triggered at 80% of daily limit) can instruct the system to pause or continue
**Warning signs:** `candidate_thinkers` table with >40 rows in `pending_llm` status

### Pitfall 5: Guest Feed Registration Creates Duplicate Sources

**What goes wrong:** Both Listen Notes and Podcast Index return feed URLs for the same podcast. The handler registers both as separate sources, creating duplicate `fetch_podcast_feed` jobs and duplicate content.
**Why it happens:** Feed URLs from different APIs may differ slightly (HTTP vs HTTPS, trailing slash, different CDN domains).
**How to avoid:**
1. Normalize feed URLs before checking for existing sources (use existing `url_normalizer.py`)
2. Check `sources.url` for existing source before inserting
3. If a source already exists for the same thinker and feed URL, skip registration
4. Log the duplicate detection for monitoring
**Warning signs:** Multiple sources pointing to the same podcast for the same thinker

## Code Examples

### Listen Notes Search Response Structure

```python
# Source: Listen Notes API documentation (https://www.listennotes.com/api/docs/)
# Response from GET /search?q=person_name&type=episode
{
    "count": 10,
    "total": 150,
    "next_offset": 10,
    "results": [
        {
            "id": "episode_id",
            "title_original": "Episode Title with Guest Name",
            "description_original": "Episode description...",
            "pub_date_ms": 1709251200000,
            "audio_length_sec": 3600,
            "podcast": {
                "id": "podcast_id",
                "title_original": "Podcast Show Name",
                "publisher_original": "Host Name",
                "rss": "https://feed.example.com/rss"  # PRO/ENTERPRISE only
            }
        }
    ]
}
# NOTE: RSS feed URL is only available on PRO/ENTERPRISE plans.
# On FREE plan, use podcast_id to find the feed via Podcast Index.
```

### Podcast Index Search by Person Response

```python
# Source: Podcast Index API docs (https://podcastindex-org.github.io/docs-api/)
# Response from GET /api/1.0/search/byperson?q=person_name
{
    "status": "true",
    "items": [
        {
            "id": 12345,
            "title": "Episode Title",
            "description": "Episode description...",
            "feedUrl": "https://feed.example.com/rss",
            "feedTitle": "Podcast Show Name",
            "datePublished": 1709251200,
            "duration": 3600,
            "enclosureUrl": "https://audio.example.com/ep1.mp3"
        }
    ],
    "count": 10
}
# NOTE: feedUrl is always included in Podcast Index responses (free tier).
```

### Podcast Index Authentication

```python
# Source: Podcast Index API docs + example-code repo
import hashlib
import time
import httpx

def _podcastindex_headers(api_key: str, api_secret: str) -> dict[str, str]:
    """Generate authentication headers for Podcast Index API."""
    epoch_time = str(int(time.time()))
    data_to_hash = api_key + api_secret + epoch_time
    sha1_hash = hashlib.sha1(data_to_hash.encode("utf-8")).hexdigest()
    return {
        "User-Agent": "ThinkTank/1.0",
        "X-Auth-Key": api_key,
        "X-Auth-Date": epoch_time,
        "Authorization": sha1_hash,
    }
```

### Handler Registration Pattern (from existing registry.py)

```python
# Source: src/thinktank/handlers/registry.py
# Phase 6 adds three handlers to the existing registry:
from src.thinktank.handlers.scan_for_candidates import handle_scan_for_candidates
from src.thinktank.handlers.discover_guests_listennotes import handle_discover_guests_listennotes
from src.thinktank.handlers.discover_guests_podcastindex import handle_discover_guests_podcastindex

# --- Phase 6 handler registrations ---
register_handler("scan_for_candidates", handle_scan_for_candidates)
register_handler("discover_guests_listennotes", handle_discover_guests_listennotes)
register_handler("discover_guests_podcastindex", handle_discover_guests_podcastindex)
```

### Quota Check Trigger Pattern

```python
# Source: Spec Section 5.3 + Section 8.2 (quota_check review)
# When daily quota approaches, create a quota_check LLM review job
import uuid
from thinktank.models.job import Job

def _create_quota_check_job(session, candidates_today, daily_limit):
    """Trigger LLM quota review when at 80% of daily limit."""
    if candidates_today >= daily_limit * 0.8:
        job = Job(
            id=uuid.uuid4(),
            job_type="llm_approval_check",
            payload={
                "review_type": "candidate_review",  # Uses existing flow
                # No candidate_ids = review all pending_llm candidates
            },
            priority=1,
            status="pending",
            attempts=0,
            max_attempts=3,
        )
        session.add(job)
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| SDK wrappers for API calls | Direct httpx calls | Project convention from Phase 3 | Fewer dependencies, consistent error handling |
| NLP/NER for name extraction | Regex patterns with validation | V1 design decision | No ML dependencies, fast, predictable |
| Single API for guest discovery | Dual-API (Podcast Index first, Listen Notes fallback) | Pitfall #12 mitigation | Preserves Listen Notes quota, Podcast Index is free |

**Deprecated/outdated:**
- `python-podcastindex` package: Claims Python 2.7-3.7 support, last meaningful update unclear. Do not use.
- `podcast-api` (Listen Notes SDK): Functional but unnecessary thin wrapper. Direct httpx is preferred per project convention.

## Open Questions

1. **Listen Notes RSS field availability on free tier**
   - What we know: The API docs state RSS feed URL is only available on PRO/ENTERPRISE plans.
   - What's unclear: Whether the free tier truly omits `rss` field or just returns null.
   - Recommendation: Handle gracefully -- if `rss` is null, use Podcast Index `feedUrl` instead. This reinforces the "Podcast Index first" strategy. If neither provides a feed URL, log and skip.

2. **Podcast Index rate limits**
   - What we know: Podcast Index is free and doesn't document hard rate limits. The spec sets `listennotes_calls_per_hour: 100` but has no `podcastindex_calls_per_hour` config entry.
   - What's unclear: Whether Podcast Index has undocumented rate limits that could cause 429s.
   - Recommendation: Add `podcastindex_calls_per_hour` to system_config with a conservative default (e.g., 300/hour). Use existing rate limiter infrastructure. Better to be conservative than discover limits via errors.

3. **Content batch for scan_for_candidates**
   - What we know: The handler needs to scan episode metadata for names. The spec doesn't specify how scan_for_candidates jobs are created (who enqueues them).
   - What's unclear: Whether it runs on a schedule (like refresh_due_sources) or is enqueued per-batch (like tag_content_thinkers).
   - Recommendation: Enqueue `scan_for_candidates` from `tag_content_thinkers` handler (or `fetch_podcast_feed`) for each batch of new content, similar to how `tag_content_thinkers` is already enqueued. This ensures every new content batch is scanned without a separate scheduler.

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 8.x + pytest-asyncio 0.25+ |
| Config file | `pyproject.toml` [tool.pytest.ini_options] |
| Quick run command | `uv run pytest tests/unit/ -x -q` |
| Full suite command | `uv run pytest tests/ -x` |

### Phase Requirements to Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| DISC-01a | Name extraction from episode text returns valid person names | unit | `uv run pytest tests/unit/test_name_extractor.py -x` | Wave 0 |
| DISC-01b | scan_for_candidates creates CandidateThinker rows for names with 3+ appearances | integration | `uv run pytest tests/integration/test_scan_candidates.py -x` | Wave 0 |
| DISC-01c | scan_for_candidates deduplicates against existing thinkers via trigram | integration | `uv run pytest tests/integration/test_scan_candidates.py::test_dedup_existing_thinker -x` | Wave 0 |
| DISC-02a | Listen Notes client searches episodes by person name with rate limiting | unit | `uv run pytest tests/unit/test_listennotes_client.py -x` | Wave 0 |
| DISC-02b | Podcast Index client searches by person with auth headers | unit | `uv run pytest tests/unit/test_podcastindex_client.py -x` | Wave 0 |
| DISC-02c | discover_guests_listennotes registers feed as Source pending LLM approval | integration | `uv run pytest tests/integration/test_discover_guests.py::test_listennotes_registers_source -x` | Wave 0 |
| DISC-02d | discover_guests_podcastindex registers feed as Source pending LLM approval | integration | `uv run pytest tests/integration/test_discover_guests.py::test_podcastindex_registers_source -x` | Wave 0 |
| DISC-05a | Daily quota check returns correct count and limit | unit | `uv run pytest tests/unit/test_quota.py -x` | Wave 0 |
| DISC-05b | scan_for_candidates pauses when daily quota reached | integration | `uv run pytest tests/integration/test_scan_candidates.py::test_quota_pause -x` | Wave 0 |
| DISC-05c | Quota check triggers LLM review at 80% of limit | integration | `uv run pytest tests/integration/test_scan_candidates.py::test_quota_triggers_review -x` | Wave 0 |
| CONTRACT | All 3 new handlers have contract tests (input payload -> expected side effects) | contract | `uv run pytest tests/contract/test_discovery_handlers.py -x` | Wave 0 |

### Sampling Rate
- **Per task commit:** `uv run pytest tests/unit/ -x -q`
- **Per wave merge:** `uv run pytest tests/ -x`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `tests/unit/test_name_extractor.py` -- covers DISC-01a
- [ ] `tests/unit/test_listennotes_client.py` -- covers DISC-02a
- [ ] `tests/unit/test_podcastindex_client.py` -- covers DISC-02b
- [ ] `tests/unit/test_quota.py` -- covers DISC-05a
- [ ] `tests/integration/test_scan_candidates.py` -- covers DISC-01b, DISC-01c, DISC-05b, DISC-05c
- [ ] `tests/integration/test_discover_guests.py` -- covers DISC-02c, DISC-02d
- [ ] `tests/contract/test_discovery_handlers.py` -- covers CONTRACT
- [ ] `tests/fixtures/listennotes/` -- checked-in API response fixtures
- [ ] `tests/fixtures/podcastindex/` -- checked-in API response fixtures

## Sources

### Primary (HIGH confidence)
- ThinkTank_Specification.md Sections 5.3, 5.4, 6.0 (job types table), 6.4, 8.2 -- authoritative for cascade discovery, guest discovery, rate limiting, and quota check flows
- Existing codebase (src/thinktank/) -- all referenced modules verified by direct file reading
- STANDARDS.md -- testing pyramid, factory pattern, mock strategy

### Secondary (MEDIUM confidence)
- [Listen Notes API docs](https://www.listennotes.com/api/docs/) -- search endpoint, response structure, rate limit tiers
- [Podcast Index API docs](https://podcastindex-org.github.io/docs-api/) -- search/byperson endpoint, authentication headers, response structure
- [Podcast Index example-code](https://github.com/Podcastindex-org/example-code) -- authentication pattern verified
- [Listen Notes rate limits](https://www.listennotes.help/article/109-listen-notes-podcast-api-rate-limits) -- free tier constraints
- [python-podcastindex PyPI](https://pypi.org/project/python-podcastindex/) -- version 1.15.0, evaluated and rejected in favor of direct httpx

### Tertiary (LOW confidence)
- Listen Notes free tier RSS field availability -- docs say PRO/ENTERPRISE only but this needs runtime verification
- Podcast Index undocumented rate limits -- no official documentation found; conservative default recommended

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- no new dependencies, all libraries already in project
- Architecture: HIGH -- follows established handler/pure-function patterns from Phases 3-5
- Pitfalls: MEDIUM -- API quota exhaustion and name extraction noise are real risks but mitigated by design
- API integration: MEDIUM -- both APIs documented, but edge cases (RSS availability, undocumented limits) need runtime validation

**Research date:** 2026-03-08
**Valid until:** 2026-04-08 (stable -- API surfaces rarely change, project conventions well-established)
