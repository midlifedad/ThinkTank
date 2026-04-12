# Phase 13: Efficient Episode Cataloging and Thinker Guest Detection - Context

**Gathered:** 2026-04-12
**Status:** Ready for planning

<domain>
## Phase Boundary

Restructure the content ingestion pipeline so episodes are first cataloged (metadata only) with `status='cataloged'`, then selectively promoted to `status='pending'` for transcription based on thinker guest detection. Add YouTube channel support alongside the existing RSS pipeline. Save 85-95% of transcription costs by only transcribing episodes featuring tracked thinkers.

</domain>

<decisions>
## Implementation Decisions

### Content Status Lifecycle
- **D-01:** ALL sources use the `cataloged` status — both host-owned and guest-appearance sources. Every new episode starts as `status='cataloged'`. A unified `scan_episodes_for_thinkers` handler promotes: all episodes for host sources, only thinker matches for guest sources.
- **D-02:** Unmatched episodes stay `cataloged` permanently. They remain in the DB (preventing re-discovery on next feed refresh via dedup) but never enter the transcription queue. They can be promoted later via retroactive scanning when new thinkers are added.
- **D-03:** Replace `tag_content_thinkers` handler with `scan_episodes_for_thinkers`. The new handler does everything the old one did (name matching, ContentThinker attribution) PLUS status promotion from `cataloged` to `pending`. Simpler pipeline: `fetch` -> `scan_episodes_for_thinkers` -> `process_content`.

### Retroactive Scanning
- **D-04:** Auto-rescan on thinker approval. When a thinker moves to `approved` status, automatically enqueue a `rescan_cataloged_for_thinker` job. It queries the DB for cataloged episodes matching the thinker's name in title/description. Cheap (no API calls, just DB queries).
- **D-05:** Leave existing `pending` episodes alone during migration. Episodes already in the pipeline with `status='pending'` keep going through transcription. Only NEW episodes from future feed fetches start as `cataloged`. Clean cutover, no retroactive demotion.

### Source Type Expansion
- **D-06:** RSS + YouTube in Phase 13. Spotify deferred to a future phase (Spotify has no structured guest credits — just title/description text, same as RSS. Limited added value).
- **D-07:** YouTube channels added via manual source addition only. Operator adds channels through the existing admin source form (URL = youtube.com/channel/UCxxx, source_type='youtube_channel'). No auto-discovery of YouTube channels.
- **D-08:** YouTube content filtering uses YouTube-specific content type detection. Use the YouTube Data API's `snippet.categoryId` and `contentDetails.duration` (ISO 8601) to filter. Category IDs identify music, gaming, etc. to skip. Duration filtering still applies on top. This is MORE than just reusing the RSS skip title patterns — use the API's structured metadata for smarter filtering.

### Guest Detection Signals
- **D-09:** Parse `podcast:person` XML tags as a bonus high-confidence signal. Use stdlib `xml.etree.ElementTree` (feedparser doesn't support Podcast 2.0 namespace). When present, gives confidence=10 guest names. When absent, fall back to title/description matching. Low effort, high value.
- **D-10:** YouTube guest detection uses video title + description matching only. Same approach as RSS — match thinker names in text. No comment analysis (too noisy, extra API quota cost).
- **D-11:** Keep candidate discovery separate from guest detection. `scan_episodes_for_thinkers` only matches against KNOWN approved thinkers. Candidate creation from arbitrary text names stays in the existing `scan_for_candidates` handler from Phase 6.

### Claude's Discretion
- Pipeline job priority assignment for scan_episodes_for_thinkers and rescan jobs
- YouTube API quota tracking implementation details (how to store/check daily quota usage)
- Whether to batch content_ids in scan job payloads or process per-source

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Pipeline Architecture
- `src/thinktank/handlers/fetch_podcast_feed.py` -- Current RSS fetch handler (must be modified to set status='cataloged')
- `src/thinktank/handlers/tag_content_thinkers.py` -- Handler being REPLACED by scan_episodes_for_thinkers
- `src/thinktank/ingestion/name_matcher.py` -- Existing name matching logic (reuse for guest detection)
- `src/thinktank/ingestion/feed_parser.py` -- RSS parsing (extend for podcast:person XML supplement)
- `src/thinktank/ingestion/content_filter.py` -- Duration and title pattern filtering

### Models
- `src/thinktank/models/content.py` -- Content model (status field, no schema changes needed — TEXT column)
- `src/thinktank/models/source.py` -- Source model with SourceThinker junction
- `src/thinktank/models/thinker.py` -- Thinker model
- `src/thinktank/models/job.py` -- Job model (new job types: scan_episodes_for_thinkers, fetch_youtube_channel, rescan_cataloged_for_thinker)

### LLM/Decision Integration
- `src/thinktank/llm/decisions.py` -- Where thinker approval triggers new jobs (add rescan trigger here)

### Research
- `.planning/phases/13-efficient-episode-cataloging-and-thinker-guest-detection/13-RESEARCH.md` -- Full research including YouTube API quota costs, podcast:person spec, efficiency analysis

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `match_thinkers_in_text()` in `name_matcher.py`: Already matches thinker names in title/description with confidence scoring. Reuse directly in scan_episodes_for_thinkers.
- `check_and_acquire_rate_limit()` in `rate_limiter.py`: DB-backed sliding window rate limiter. Use for YouTube API calls.
- `normalize_url()` and `compute_fingerprint()`: Existing dedup layers work for YouTube URLs too.
- `get_source_filter_config()` in `config_reader.py`: Source-specific filter overrides. Extend for YouTube category filtering.

### Established Patterns
- Handler protocol: `async def handle_X(session: AsyncSession, job: Job) -> None` — all handlers follow this.
- Job chaining: fetch_podcast_feed enqueues tag_content_thinkers at end. New pattern: fetch handlers enqueue scan_episodes_for_thinkers.
- Status determination: Lines 209-215 of fetch_podcast_feed.py — `if should_skip: 'skipped' else: 'pending'`. Change to `else: 'cataloged'`.

### Integration Points
- `src/thinktank/queue/handler_registry.py`: Register new handler types (scan_episodes_for_thinkers, fetch_youtube_channel, rescan_cataloged_for_thinker)
- `src/thinktank/llm/decisions.py` `apply_thinker_decision()`: Add rescan trigger when thinker approved
- `src/thinktank/admin/routers/sources.py`: YouTube source type in add form
- `src/thinktank/agent/system_prompt.py`: Update schema description for new status and job types

</code_context>

<specifics>
## Specific Ideas

- YouTube filtering should use the API's structured metadata (categoryId, content details) rather than just reusing RSS-style title pattern matching. The API gives us richer signals for free.
- The pipeline simplification (replacing tag_content_thinkers with scan_episodes_for_thinkers) is a nice cleanup — one handler that does scanning + attribution + promotion.

</specifics>

<deferred>
## Deferred Ideas

- **Spotify show support**: Spotify API provides episode titles/descriptions but no structured guest credits. Same signal as RSS title/description matching. Lower priority than YouTube. Phase 14+.
- **YouTube comment analysis for guest detection**: Too noisy, extra API quota cost (commentThreads.list). Title + description matching is sufficient.
- **Auto-discovery of YouTube channels for thinkers**: Search YouTube for channels matching thinker names. Risk of finding fan/tribute channels. Manual addition is safer for now.

</deferred>

---

*Phase: 13-efficient-episode-cataloging-and-thinker-guest-detection*
*Context gathered: 2026-04-12*
