# Phase 13: Efficient Episode Cataloging and Thinker Guest Detection - Research

**Researched:** 2026-04-12
**Domain:** Content ingestion pipeline optimization, multi-source episode cataloging, guest detection
**Confidence:** HIGH

## Summary

Phase 13 addresses a critical efficiency gap in the ThinkTank pipeline: right now, every episode discovered from an RSS feed becomes a Content row with `status='pending'`, making it an immediate candidate for expensive transcription. For sources like Joe Rogan (2000+ episodes) or Lex Fridman (400+ episodes), this means wasting GPU transcription credits on episodes where no tracked thinker appears. The solution is a two-phase ingestion model: **catalog first, then selectively promote**.

The current codebase already has partial guest-detection infrastructure -- `tag_content_thinkers` does name matching in titles/descriptions, and `fetch_podcast_feed` has a `guest_filter_thinker_id` payload field that filters by name. However, these operate AFTER content rows are created with `status='pending'`, meaning transcription jobs can be created before guest detection runs. The fix requires introducing a new content status (`cataloged`) that sits before `pending` in the state machine, adding a dedicated guest-scanning handler that promotes only episodes with relevant thinkers, and extending the pipeline to YouTube channels and Spotify shows.

The architecture preserves the existing Content table (no separate episode catalog table needed) but adds new status values and a new handler (`scan_episodes_for_thinkers`) that runs between `fetch_podcast_feed`/`fetch_youtube_channel` and `process_content`. For RSS, the existing `feedparser` + custom `podcast:person` XML parsing covers guest metadata extraction. For YouTube, the Data API v3 `playlistItems.list` (1 quota unit per page of 50) plus `videos.list` (1 quota unit per batch of 50) provides episode metadata within the 10,000 units/day quota. Spotify's Web API provides episode titles and descriptions but no structured guest credits.

**Primary recommendation:** Introduce `status='cataloged'` as the default for new content from feed fetches, add a `scan_episodes_for_thinkers` handler that promotes relevant episodes to `status='pending'`, and update `fetch_podcast_feed` to set `status='cataloged'` instead of `status='pending'`. This is a surgical change to the existing pipeline, not a rewrite.

## Standard Stack

### Core (Already in Project)

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| feedparser | latest | RSS/Atom feed parsing | Already used in `feed_parser.py`; handles iTunes namespace |
| httpx | latest | Async HTTP client | Already used throughout for API calls |
| SQLAlchemy 2.0 | latest | Async ORM | Already the project's ORM |
| structlog | latest | Structured logging | Already the project's logging framework |

### New Dependencies Required

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| xml.etree.ElementTree | stdlib | Parse `podcast:person` tags from RSS XML | feedparser does NOT support Podcast 2.0 namespace tags natively; use stdlib XML parsing as a supplement |
| google-api-python-client | latest | YouTube Data API v3 | Fetching channel video metadata (titles, descriptions, durations) |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| xml.etree.ElementTree | lxml | lxml is faster but adds a C dependency; stdlib is sufficient for extracting a few tags per feed |
| google-api-python-client | Direct httpx calls to YouTube API | google-api-python-client handles auth, pagination, and retries; avoids reimplementing that |
| Spotify API for guest detection | Podchaser API (16M+ guest credits) | Podchaser has structured guest data but is paid; Spotify episode descriptions + title matching is free and sufficient for v1 |

**Installation:**
```bash
uv add google-api-python-client
```

Note: `xml.etree.ElementTree` is stdlib, no install needed. `feedparser` and `httpx` are already installed.

## Architecture Patterns

### Current Pipeline Flow (BEFORE Phase 13)

```
refresh_due_sources
  -> fetch_podcast_feed (creates Content rows with status='pending' or 'skipped')
    -> tag_content_thinkers (creates ContentThinker attribution)
      -> process_content (GPU transcription picks up status='pending')
```

**Problem:** `process_content` jobs get enqueued for ALL `pending` content, regardless of whether a relevant thinker appears in the episode. For a 2000-episode podcast, maybe 30 episodes have our thinkers.

### Proposed Pipeline Flow (AFTER Phase 13)

```
refresh_due_sources
  -> fetch_podcast_feed / fetch_youtube_channel / fetch_spotify_show
    (creates Content rows with status='cataloged' instead of 'pending')
    -> scan_episodes_for_thinkers (NEW handler)
      (reads titles/descriptions, matches thinker names, RSS podcast:person tags)
      (promotes matches to status='pending', adds ContentThinker rows)
      (leaves non-matches as status='cataloged' permanently)
        -> process_content (only picks up promoted episodes)
```

### Content Status State Machine (Updated)

```
                 +-- 'skipped' (too short, title pattern match)
                 |
 fetch handler --+-- 'cataloged' (NEW: metadata stored, awaiting guest scan)
                 |       |
                 |       +-- scan_episodes_for_thinkers
                 |       |       |
                 |       |       +-- thinker found -> 'pending'
                 |       |       |
                 |       |       +-- no thinker found -> stays 'cataloged'
                 |       |
                 |       +-- manual promotion via admin -> 'pending'
                 |
                 +-- 'pending' (approved for transcription)
                         |
                         +-- 'transcribing' -> 'done' | 'error'
```

Key insight: `cataloged` is a terminal state for most episodes. They remain in the database (preventing re-discovery on next refresh) but NEVER enter the transcription queue unless a thinker match is found.

### Recommended File Structure (New Files Only)

```
src/thinktank/
├── handlers/
│   ├── scan_episodes_for_thinkers.py   # NEW: guest detection + promotion handler
│   ├── fetch_youtube_channel.py        # NEW: YouTube Data API channel video fetch
│   ├── fetch_spotify_show.py           # NEW: Spotify episode metadata fetch
│   └── fetch_podcast_feed.py           # MODIFIED: status='cataloged' instead of 'pending'
├── ingestion/
│   ├── feed_parser.py                  # MODIFIED: extract podcast:person tags
│   ├── podcast_person_parser.py        # NEW: XML parsing for podcast:person namespace
│   └── youtube_client.py              # NEW: YouTube Data API v3 client
├── discovery/
│   └── spotify_client.py              # NEW: Spotify Web API client for show episodes
└── models/
    └── content.py                     # UNCHANGED: reuse existing Content model
```

### Pattern 1: Catalog-Then-Promote (Core Pattern)

**What:** All content starts as `status='cataloged'` (metadata only). A separate handler scans metadata for thinker matches and promotes relevant episodes to `status='pending'`.

**When to use:** Every source type (RSS, YouTube, Spotify) follows this pattern.

**Example:**
```python
# In fetch_podcast_feed.py -- CHANGE from:
status = "pending"
# TO:
status = "cataloged"

# New handler: scan_episodes_for_thinkers
async def handle_scan_episodes_for_thinkers(
    session: AsyncSession, job: Job
) -> None:
    """Scan cataloged episodes for thinker name matches.

    Reads content_ids from payload, checks titles/descriptions/podcast:person
    against all active thinkers, promotes matches to status='pending'.
    """
    content_ids = job.payload.get("content_ids", [])
    source_id = job.payload.get("source_id")

    # Load all active thinkers for matching
    thinkers = await _load_active_thinkers(session)

    for content_id in content_ids:
        content = await session.get(Content, content_id)
        if content is None or content.status != "cataloged":
            continue

        # Check title, description, and podcast:person metadata
        matches = match_thinkers_in_text(
            content.title,
            descriptions.get(str(content.id), ""),
            thinkers,
            source_owner_name,
        )

        if matches:
            content.status = "pending"
            # Create ContentThinker attribution rows
            for match in matches:
                ct = ContentThinker(
                    content_id=content.id,
                    thinker_id=match["thinker_id"],
                    role=match["role"],
                    confidence=match["confidence"],
                )
                session.add(ct)
        # If no matches, content stays 'cataloged' -- never transcribed
```

### Pattern 2: Supplementary RSS XML Parsing for podcast:person

**What:** After feedparser extracts standard fields, do a second pass on the raw XML to extract `podcast:person` tags that feedparser does not support.

**When to use:** Every RSS feed parse.

**Example:**
```python
import xml.etree.ElementTree as ET

PODCAST_NS = "https://podcastindex.org/namespace/1.0"

def extract_podcast_persons(xml_content: str) -> dict[str, list[dict]]:
    """Extract podcast:person tags from RSS XML, keyed by episode GUID.

    Returns:
        Dict mapping episode guid -> list of person dicts with
        {name, role, group, href, img}.
    """
    root = ET.fromstring(xml_content)
    persons_by_guid: dict[str, list[dict]] = {}

    for item in root.iter("item"):
        guid_el = item.find("guid")
        guid = guid_el.text if guid_el is not None else None
        if not guid:
            continue

        persons = []
        for person_el in item.findall(f"{{{PODCAST_NS}}}person"):
            persons.append({
                "name": person_el.text or "",
                "role": (person_el.get("role") or "host").lower(),
                "group": (person_el.get("group") or "cast").lower(),
                "href": person_el.get("href"),
                "img": person_el.get("img"),
            })

        if persons:
            persons_by_guid[guid] = persons

    return persons_by_guid
```

### Pattern 3: YouTube Channel Video Cataloging

**What:** Use the YouTube Data API v3 to fetch all videos from a channel's uploads playlist, creating `cataloged` Content rows.

**When to use:** For sources with `source_type='youtube_channel'`.

**Example:**
```python
from googleapiclient.discovery import build

class YouTubeClient:
    """YouTube Data API v3 client for channel video cataloging."""

    def __init__(self, api_key: str):
        self._youtube = build("youtube", "v3", developerKey=api_key)

    def get_uploads_playlist_id(self, channel_id: str) -> str:
        """Convert channel ID to uploads playlist ID.

        Shortcut: replace 'UC' prefix with 'UU'.
        """
        if channel_id.startswith("UC"):
            return "UU" + channel_id[2:]
        # Fallback: API call
        response = self._youtube.channels().list(
            part="contentDetails",
            id=channel_id,
        ).execute()
        return response["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]

    def list_videos(self, playlist_id: str, page_token: str = None) -> dict:
        """Fetch a page of videos from a playlist. 1 quota unit per call."""
        return self._youtube.playlistItems().list(
            part="snippet",
            playlistId=playlist_id,
            maxResults=50,
            pageToken=page_token,
        ).execute()

    def get_video_details(self, video_ids: list[str]) -> dict:
        """Fetch video details (duration, description). 1 quota unit per call.

        Batch up to 50 video IDs per call.
        """
        return self._youtube.videos().list(
            part="snippet,contentDetails",
            id=",".join(video_ids),
        ).execute()
```

### Anti-Patterns to Avoid

- **Anti-pattern: Separate "episode catalog" table.** The Content table already has all needed fields. Adding a separate table creates sync complexity, migration headaches, and duplicates schema. Use `status='cataloged'` on the EXISTING Content model.

- **Anti-pattern: Running transcription on all episodes, then filtering.** This is what we are fixing. One Parakeet transcription costs ~$0.05-0.10 in GPU time. 2000 episodes * $0.07 = $140 wasted per source. Catalog-then-promote costs effectively $0 for the metadata scan.

- **Anti-pattern: Using YouTube search.list to find channel videos.** `search.list` costs 100 quota units per call. `playlistItems.list` costs 1 unit. For a 500-video channel, search costs 1000 units vs playlistItems costs 10 units. Always use the uploads playlist approach.

- **Anti-pattern: Relying on Spotify API for guest detection.** Spotify's episode metadata has title and description (good for name matching) but NO structured guest/person credits. Do not expect structured guest data from Spotify. Title/description matching is the approach.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| RSS parsing | Custom XML parser for standard fields | feedparser (already in project) | feedparser handles encoding, date parsing, enclosure extraction, edge cases |
| Podcast 2.0 person extraction | Feedparser extension | stdlib xml.etree.ElementTree | feedparser's namespace support is broken for podcast namespace (GitHub issue #301); stdlib XML parsing is trivial for extracting a few tags |
| YouTube API pagination | Manual HTTP + token management | google-api-python-client | Handles auth, pagination tokens, retries, rate limit headers |
| Name matching in text | Regex NER or spaCy | Existing `name_matcher.py` `match_thinkers_in_text()` | Already tested and working; case-insensitive full name matching against known thinker list |
| Content deduplication | New dedup logic | Existing URL normalization + fingerprint + trigram | Already three layers of dedup in the pipeline; new sources just need to feed into the same Content model |
| Rate limiting for YouTube/Spotify | Custom rate limiter | Existing `rate_limiter.py` `check_and_acquire_rate_limit()` | Project already has DB-backed sliding-window rate limiting for external APIs |

**Key insight:** The existing ingestion pipeline (URL normalization, fingerprinting, content filtering, name matching, rate limiting) all work generically on Content rows regardless of source type. New source types (YouTube, Spotify) just need to create Content rows following the same patterns. The catalog-then-promote logic is a single new handler, not a rewrite.

## Common Pitfalls

### Pitfall 1: Breaking Existing fetch_podcast_feed Behavior

**What goes wrong:** Changing `status='pending'` to `status='cataloged'` in fetch_podcast_feed means existing `process_content` jobs that look for `status='pending'` will find nothing, and episodes from feeds that DON'T have thinker tracking (host-owned podcasts where every episode matters) would never get transcribed.

**Why it happens:** The current pipeline assumes ALL episodes from approved sources should be transcribed.

**How to avoid:** The scan_episodes_for_thinkers handler must handle TWO cases: (1) For "host" sources (where the source owner IS a tracked thinker), promote ALL episodes to `pending`. (2) For "guest" sources, only promote episodes where a tracked thinker name appears. Check `source_thinkers.relationship_type` to determine which behavior applies.

**Warning signs:** Zero `process_content` jobs being created after deployment; `pending` count drops to zero.

### Pitfall 2: YouTube API Quota Exhaustion

**What goes wrong:** A channel with 2000 videos requires 40 `playlistItems.list` pages (40 units) + 40 `videos.list` batch calls (40 units) = 80 units. With 10 YouTube sources, that is 800 units just for initial cataloging. If you accidentally use `search.list` (100 units per call), a single channel eats 4000 units and you hit the daily 10,000 limit with 3 channels.

**Why it happens:** YouTube API quota costs vary wildly by endpoint. Developers often reach for search.list first.

**How to avoid:** ALWAYS use `playlistItems.list` (1 unit/page) + `videos.list` (1 unit/batch) instead of `search.list` (100 units/call). Track quota usage in `api_usage` table. Add a `youtube_daily_quota_remaining` system_config check before each YouTube fetch.

**Warning signs:** `HTTP 403 quotaExceeded` errors in job logs.

### Pitfall 3: podcast:person Tag Adoption is Low

**What goes wrong:** Developer assumes all podcast feeds have `podcast:person` tags with guest names, builds the guest detection strategy around it, and finds that only ~5-10% of feeds actually use the Podcast 2.0 namespace.

**Why it happens:** Podcast 2.0 namespace is opt-in and relatively new. Major platforms (Spotify, Apple Podcasts) do NOT inject these tags into their hosted feeds. Only podcast-first hosts who use compatible hosting platforms include them.

**How to avoid:** Treat `podcast:person` as a HIGH-CONFIDENCE bonus signal (confidence=10 when role='guest'), but ALWAYS fall back to title/description name matching (the primary detection method). Never assume podcast:person will be present.

**Warning signs:** `podcast:person` extraction finding zero results across most feeds.

### Pitfall 4: Spotify API Does Not Expose Guest Data

**What goes wrong:** Developer expects Spotify's episode endpoint to return guest/host information like Podchaser does.

**Why it happens:** Spotify's podcast API is designed for playback, not metadata enrichment. Episode objects have title, description, duration, and images -- but NO structured guest credits.

**How to avoid:** Use Spotify ONLY for: (a) discovering which episodes exist in a show, and (b) extracting title + description text for name matching. Never expect structured guest metadata from Spotify.

**Warning signs:** Empty guest results from Spotify API calls.

### Pitfall 5: Retroactive Scan Missing Existing Content

**What goes wrong:** After deploying the new `cataloged` status, existing Content rows with `status='pending'` that were never thinker-tagged continue to enter transcription without guest detection.

**Why it happens:** Migration only affects new rows. Existing `pending` rows predate the catalog-then-promote flow.

**How to avoid:** Include a one-time migration/backfill step that: (1) Leaves existing `pending` rows alone (they are already in the pipeline). (2) For any existing `pending` rows from "guest" sources that don't have ContentThinker rows, retroactively scans them and demotes irrelevant ones to `cataloged`.

**Warning signs:** High transcription costs on episodes that have no thinker attribution after transcription.

## Code Examples

### Modifying fetch_podcast_feed to Use 'cataloged' Status

```python
# Source: src/thinktank/handlers/fetch_podcast_feed.py
# CHANGE: Lines where status is determined

# Before (current code):
if should_skip_by_duration(...) or should_skip_by_title(...):
    status = "skipped"
else:
    status = "pending"

# After (Phase 13):
if should_skip_by_duration(...) or should_skip_by_title(...):
    status = "skipped"
else:
    status = "cataloged"  # Episodes start as cataloged, not pending

# CHANGE: Instead of enqueuing tag_content_thinkers, enqueue scan_episodes_for_thinkers
if inserted_content:
    scan_job = Job(
        id=uuid.uuid4(),
        job_type="scan_episodes_for_thinkers",
        payload={
            "content_ids": [str(c.id) for c in inserted_content],
            "source_id": str(source_id),
            "descriptions": descriptions,
        },
        priority=3,
        status="pending",
        attempts=0,
        max_attempts=3,
        created_at=_now(),
    )
    session.add(scan_job)
```

### scan_episodes_for_thinkers Handler (Core Logic)

```python
# Source: NEW handler scan_episodes_for_thinkers.py

async def handle_scan_episodes_for_thinkers(
    session: AsyncSession, job: Job
) -> None:
    """Scan cataloged episodes for thinker matches and promote relevant ones.

    For host-owned sources: all episodes promoted (the host IS a thinker).
    For guest sources: only episodes mentioning a tracked thinker promoted.
    """
    content_ids = job.payload.get("content_ids", [])
    source_id = uuid.UUID(job.payload["source_id"])
    descriptions = job.payload.get("descriptions", {})

    source = await session.get(Source, source_id)

    # Determine if this is a host-owned source
    host_thinker_ids = await _get_host_thinker_ids(session, source_id)
    is_host_source = len(host_thinker_ids) > 0

    # Load thinker list for matching
    thinkers = await _load_active_thinkers(session)
    thinker_names = [{"id": t.id, "name": t.name} for t in thinkers]

    promoted_count = 0
    for content_id_str in content_ids:
        content = await session.get(Content, uuid.UUID(content_id_str))
        if content is None or content.status != "cataloged":
            continue

        description = descriptions.get(content_id_str, "")

        # If host-owned source, promote all episodes
        if is_host_source:
            content.status = "pending"
            promoted_count += 1
            # Tag the host as primary
            for host_id in host_thinker_ids:
                session.add(ContentThinker(
                    content_id=content.id,
                    thinker_id=host_id,
                    role="primary",
                    confidence=10,
                ))
            continue

        # For guest sources: match thinker names in title + description
        matches = match_thinkers_in_text(
            content.title, description, thinker_names, source.host_name,
        )

        if matches:
            content.status = "pending"
            promoted_count += 1
            for match in matches:
                session.add(ContentThinker(
                    content_id=content.id,
                    thinker_id=match["thinker_id"],
                    role=match["role"],
                    confidence=match["confidence"],
                ))
        # else: stays 'cataloged' -- never transcribed

    await session.commit()
```

### YouTube Channel Fetch Handler

```python
# Source: NEW handler fetch_youtube_channel.py

async def handle_fetch_youtube_channel(
    session: AsyncSession, job: Job
) -> None:
    """Fetch all video metadata from a YouTube channel, creating cataloged Content rows.

    Uses playlistItems.list (1 quota unit/page) + videos.list (1 unit/batch)
    instead of search.list (100 units/call).
    """
    source_id = uuid.UUID(job.payload["source_id"])
    source = await session.get(Source, source_id)

    youtube_api_key = await get_secret(session, "youtube_api_key")
    client = YouTubeClient(youtube_api_key)

    channel_id = source.external_id  # e.g., "UCxxxxxx"
    playlist_id = client.get_uploads_playlist_id(channel_id)

    # Rate limit check
    if not await check_and_acquire_rate_limit(session, "youtube", str(job.id)):
        raise ValueError("YouTube API rate limited")

    # Paginate through all videos
    inserted = []
    page_token = None
    while True:
        response = client.list_videos(playlist_id, page_token)
        video_ids = [item["snippet"]["resourceId"]["videoId"]
                     for item in response.get("items", [])]

        # Get full details (duration, description) in batches of 50
        if video_ids:
            details = client.get_video_details(video_ids)
            for video in details.get("items", []):
                content = _youtube_video_to_content(video, source)
                if content:
                    session.add(content)
                    inserted.append(content)

        page_token = response.get("nextPageToken")
        if not page_token:
            break

    # Enqueue scan_episodes_for_thinkers for the batch
    if inserted:
        await session.flush()
        scan_job = Job(
            job_type="scan_episodes_for_thinkers",
            payload={
                "content_ids": [str(c.id) for c in inserted],
                "source_id": str(source_id),
            },
            priority=3,
        )
        session.add(scan_job)

    await session.commit()
```

### Alembic Migration for New Status Value

```python
# No schema change needed for status values -- the content.status column is TEXT,
# not an enum. 'cataloged' is just a new string value.
#
# However, add an index for efficient queries:
def upgrade():
    op.create_index(
        "ix_content_status_cataloged",
        "content",
        ["status"],
        postgresql_where=text("status = 'cataloged'"),
    )
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Transcribe everything, filter later | Catalog first, transcribe selectively | This phase | ~90% reduction in transcription costs for guest-appearance sources |
| feedparser for all RSS metadata | feedparser + xml.etree for podcast:person | Podcast 2.0 adoption (2020+) | Structured guest data when available (high confidence) |
| YouTube search.list for channel videos | playlistItems.list + videos.list | YouTube quota system unchanged but search costs 100x more | 100x quota efficiency improvement |
| Single source type (podcast_rss) | Multiple source types (RSS, YouTube, Spotify) | This phase | Broader coverage of thinker appearances |

**Deprecated/outdated:**
- **Listen Notes for guest detection per episode:** Listen Notes is for discovering which PODCASTS a thinker appeared on (source-level), not which EPISODES. Per-episode detection uses title/description matching + podcast:person tags.
- **Spotify available_markets field:** Removed in February 2026 changelog. Not relevant to episode metadata extraction.

## Open Questions

1. **Should `cataloged` episodes ever be re-scanned when new thinkers are added?**
   - What we know: When a new thinker is approved and added, there may be existing `cataloged` episodes from past feed fetches that mention this thinker but were not promoted because the thinker was not yet tracked.
   - What's unclear: How expensive is a retroactive scan? Should it be automatic or manual?
   - Recommendation: Add a `rescan_cataloged_for_thinker` job type triggered when a thinker is approved. It queries `SELECT id FROM content WHERE status='cataloged' AND (title ILIKE '%thinker_name%' OR ...)` and promotes matches. This is cheap (no API calls, just DB queries) and ensures no episodes are missed.

2. **How to handle YouTube videos that are NOT audio/podcast content?**
   - What we know: A YouTube channel for a thinker may contain short clips, trailers, live streams, and non-interview content alongside the full interviews.
   - What's unclear: Are the existing duration and title pattern filters sufficient, or do we need YouTube-specific filtering?
   - Recommendation: Apply the same `min_duration_seconds` (600s default) and `skip_title_patterns` filters. YouTube channels often have many short clips (<10min) that the duration filter will catch. Add YouTube-specific skip patterns: "shorts", "#shorts", "highlights", "clip".

3. **Spotify episode ID stability for deduplication**
   - What we know: Spotify episodes have a Spotify ID. The same episode appearing on both RSS and Spotify will have different URLs.
   - What's unclear: Will the existing fingerprint dedup (title + date + duration) reliably catch cross-platform duplicates?
   - Recommendation: The existing `content_fingerprint = sha256(title + date + duration)` should catch these. Same episode title + same publish date + same duration = same fingerprint regardless of URL. No new dedup needed.

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest + pytest-asyncio |
| Config file | `pyproject.toml` (existing) |
| Quick run command | `pytest tests/unit/ -x --timeout=30` |
| Full suite command | `pytest tests/ --timeout=120` |

### Phase Requirements to Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| CATALOG-01 | fetch_podcast_feed creates Content with status='cataloged' (not 'pending') | unit + contract | `pytest tests/contract/test_fetch_podcast_feed.py -x` | Modify existing |
| CATALOG-02 | scan_episodes_for_thinkers promotes matched episodes to 'pending' | contract | `pytest tests/contract/test_scan_episodes_for_thinkers.py -x` | Wave 0 |
| CATALOG-03 | scan_episodes_for_thinkers leaves unmatched episodes as 'cataloged' | contract | `pytest tests/contract/test_scan_episodes_for_thinkers.py -x` | Wave 0 |
| CATALOG-04 | Host-owned sources promote ALL episodes (not just matches) | contract | `pytest tests/contract/test_scan_episodes_for_thinkers.py -x` | Wave 0 |
| CATALOG-05 | podcast:person XML parsing extracts guest names and roles | unit | `pytest tests/unit/test_podcast_person_parser.py -x` | Wave 0 |
| YOUTUBE-01 | YouTube client uses playlistItems.list + videos.list (not search.list) | unit | `pytest tests/unit/test_youtube_client.py -x` | Wave 0 |
| YOUTUBE-02 | fetch_youtube_channel creates Content rows with status='cataloged' | contract | `pytest tests/contract/test_fetch_youtube_channel.py -x` | Wave 0 |
| SPOTIFY-01 | Spotify client fetches show episodes with title/description | unit | `pytest tests/unit/test_spotify_client.py -x` | Wave 0 |
| EFFICIENCY-01 | End-to-end: 100 cataloged episodes, 10 promoted, 90 stay cataloged | integration | `pytest tests/integration/test_catalog_promote_flow.py -x` | Wave 0 |

### Sampling Rate

- **Per task commit:** `pytest tests/unit/ -x --timeout=30`
- **Per wave merge:** `pytest tests/ --timeout=120`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps

- [ ] `tests/contract/test_scan_episodes_for_thinkers.py` -- covers CATALOG-02, CATALOG-03, CATALOG-04
- [ ] `tests/unit/test_podcast_person_parser.py` -- covers CATALOG-05
- [ ] `tests/unit/test_youtube_client.py` -- covers YOUTUBE-01
- [ ] `tests/contract/test_fetch_youtube_channel.py` -- covers YOUTUBE-02
- [ ] `tests/unit/test_spotify_client.py` -- covers SPOTIFY-01
- [ ] `tests/integration/test_catalog_promote_flow.py` -- covers EFFICIENCY-01
- [ ] `tests/fixtures/youtube/` -- YouTube API response fixtures
- [ ] `tests/fixtures/spotify/` -- Spotify API response fixtures
- [ ] `tests/fixtures/rss/podcast_person.xml` -- RSS feed with podcast:person tags

## Efficiency Analysis

### Cost Savings from Catalog-Then-Promote

| Source | Total Episodes | Relevant (est.) | Without Phase 13 | With Phase 13 | Savings |
|--------|---------------|------------------|-------------------|---------------|---------|
| Joe Rogan Experience | ~2200 | ~30 AI thinkers | 2200 transcriptions | 30 transcriptions | 98.6% |
| Lex Fridman Podcast | ~430 | ~50 AI thinkers | 430 transcriptions | 50 transcriptions | 88.4% |
| Tim Ferriss Show | ~750 | ~15 AI/tech | 750 transcriptions | 15 transcriptions | 98.0% |
| Huberman Lab | ~300 | ~5 AI guests | 300 transcriptions | 5 transcriptions | 98.3% |
| All-In Podcast | ~350 | ~350 (hosts are thinkers) | 350 transcriptions | 350 transcriptions | 0% (host source) |

**Summary:** For guest-appearance sources (which are the majority), the catalog-then-promote approach saves ~90-99% of transcription costs. For host-owned sources, there is no change (all episodes are promoted). Overall estimated savings: **85-95% reduction in GPU transcription costs**.

### API Quota Budget (YouTube)

| Operation | Units per Call | Calls for 500-video channel | Total Units |
|-----------|--------------|----------------------------|-------------|
| playlistItems.list | 1 | 10 pages | 10 |
| videos.list (batch 50) | 1 | 10 batches | 10 |
| **Total per channel** | | | **20** |
| **10 channels daily** | | | **200** |
| **Daily budget** | | | **10,000** |
| **Utilization** | | | **2%** |

YouTube quota is not a concern with the efficient approach.

## Sources

### Primary (HIGH confidence)

- [Existing codebase] - `src/thinktank/handlers/fetch_podcast_feed.py`, `tag_content_thinkers.py`, `process_content.py`, `refresh_due_sources.py` (read in full)
- [Existing codebase] - `src/thinktank/models/content.py`, `source.py`, `thinker.py` (read in full)
- [Existing codebase] - `src/thinktank/ingestion/feed_parser.py`, `name_matcher.py` (read in full)
- [ThinkTank Specification] - `ThinkTank_Specification.md` Sections 3.7, 5.1-5.8, 6.6 (read in full)
- [Podcast 2.0 Namespace] - https://github.com/Podcastindex-org/podcast-namespace/blob/main/docs/tags/person.md -- podcast:person tag spec
- [YouTube Data API v3] - https://developers.google.com/youtube/v3/docs/playlistItems/list -- quota costs, pagination
- [feedparser GitHub Issue #301] - https://github.com/kurtmckee/feedparser/issues/301 -- podcast namespace NOT natively supported

### Secondary (MEDIUM confidence)

- [YouTube API Quota] - https://developers.google.com/youtube/v3/determine_quota_cost -- 10,000 units/day default, search=100, list=1
- [Spotify Web API] - https://developer.spotify.com/documentation/web-api/reference/get-a-shows-episodes -- episode metadata fields (title, description, duration, no guest credits)
- [Podcast Taxonomy] - https://github.com/Podcastindex-org/podcast-namespace/blob/main/taxonomy.json -- role values (host, guest, guest host)

### Tertiary (LOW confidence)

- [Podchaser API] - https://features.podchaser.com/api/ -- 16M+ guest credits alternative; paid API, not needed for v1 (title matching sufficient)

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH - All existing libraries confirmed by reading source code; new deps are official Google/stdlib
- Architecture: HIGH - Catalog-then-promote pattern is a surgical extension of existing pipeline; no rewrites needed
- Pitfalls: HIGH - All pitfalls derived from reading actual codebase behavior and API documentation
- YouTube/Spotify integration: MEDIUM - Based on official API docs, not hands-on testing
- podcast:person adoption rate: MEDIUM - feedparser issue #301 confirms lack of support; adoption rate estimate (~5-10%) is approximate

**Research date:** 2026-04-12
**Valid until:** 2026-05-12 (stable domain; YouTube/Spotify APIs rarely change; podcast:person spec is stable)
