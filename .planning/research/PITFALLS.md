# Domain Pitfalls

**Domain:** Podcast/knowledge ingestion infrastructure with DB-backed job queue, on-demand GPU transcription, LLM governance, and RSS feed scraping at scale
**Researched:** 2026-03-08

---

## Critical Pitfalls

Mistakes that cause rewrites, data loss, or major operational failures.

---

### Pitfall 1: Feedparser Hangs Indefinitely on Malformed or Unresponsive Feeds

**What goes wrong:** `feedparser.parse()` calls a URL that never responds (dead server, infinite redirect, malformed chunked encoding) and blocks the worker thread forever. Because feedparser does not have a built-in timeout parameter, a single bad RSS feed can permanently consume a worker slot. With 4-6 CPU workers, losing even one to a hang degrades throughput by 15-25%. At scale with hundreds of sources, this becomes a recurring event, not an edge case.

**Why it happens:** Python's default socket timeout is `None` (wait forever). Feedparser delegates to urllib and inherits this default. Podcast RSS feeds are hosted on every quality level of infrastructure imaginable -- hobby servers, abandoned WordPress installs, CDNs with misconfigured timeouts. The spec calls for scraping hundreds of sources from diverse hosts.

**Consequences:**
- Worker slots permanently consumed by hung connections
- Stale job reclaimer eventually reclaims the *job*, but the worker thread/coroutine may still be blocked on the socket
- If multiple feeds hang simultaneously, the system effectively stops processing
- No error is raised -- the failure is silent

**Prevention:**
1. Set `socket.setdefaulttimeout(30)` at worker startup as a global safety net
2. Wrap all `feedparser.parse()` calls in `asyncio.wait_for()` with a 60-second timeout (or use `httpx` with explicit timeout to fetch the feed content first, then pass the raw bytes to `feedparser.parse()`)
3. Track per-source fetch duration; sources that consistently take >30s should have their `refresh_interval_hours` increased automatically
4. Use the httpx async client to download RSS content with explicit connect/read timeouts, then feed the response body to feedparser for parsing (separating network I/O from XML parsing)

**Detection:**
- Monitor worker slot utilization: if active workers = max workers for >5 minutes with low job throughput, a hang is likely
- Log start/end timestamps for every feed fetch; alert on any fetch exceeding 120 seconds
- The health check should report workers with jobs running >10 minutes

**Confidence:** HIGH -- feedparser hanging is a well-documented, long-standing issue (GitHub issues #76, #245, #263)

**Phase relevance:** Must be addressed in the RSS/discovery implementation phase. This is day-one infrastructure, not something to retrofit.

---

### Pitfall 2: PostgreSQL Connection Pool Exhaustion Under Concurrent Job Workers

**What goes wrong:** With 4-6 CPU workers and 2-4 GPU workers all polling the jobs table via `SELECT FOR UPDATE SKIP LOCKED`, plus rate limit checks, content inserts, and LLM review logging happening concurrently, the asyncpg connection pool silently exhausts. New job claims stall, health checks fail to write, and the system appears frozen despite all services being "healthy."

**Why it happens:** SQLAlchemy + asyncpg has a known issue where connections are not returned to the pool when async tasks are cancelled (SQLAlchemy issues #6652, #8145). Worker cancellation during shutdown, timeout, or error handling leaks connections. The default `pool_size=5` is insufficient for the concurrent access pattern described in the spec. Additionally, `SELECT FOR UPDATE SKIP LOCKED` acquires row-level locks that are held for the duration of the transaction -- long-running job processing within the same transaction bleeds connection hold time.

**Consequences:**
- All workers block waiting for connections, creating a deadlock-like state
- Job queue appears frozen; no new jobs claimed, no progress logged
- Health checks may also fail if they share the same pool
- GPU workers (expensive) sit idle burning cost while waiting for connections

**Prevention:**
1. Separate connection pools for job claiming (short-lived transactions) vs. job execution (potentially long-lived)
2. Set `pool_size=10`, `max_overflow=5`, `pool_timeout=30`, `pool_recycle=1800` explicitly
3. Job claiming transaction must be committed immediately after the `SELECT FOR UPDATE SKIP LOCKED` -- never hold the advisory lock while processing the job
4. Use `pool_pre_ping=True` to detect stale connections from Railway's managed PostgreSQL (which may silently close idle connections)
5. Instrument pool checkout/checkin with structured logging so pool exhaustion is visible before it causes a full stall

**Detection:**
- Log pool statistics (checked out, available, overflow) every 60 seconds
- Alert when available connections drop to 0 for >30 seconds
- Monitor job claim latency; sudden spikes indicate pool contention

**Confidence:** HIGH -- multiple SQLAlchemy/asyncpg issues document this exact failure mode

**Phase relevance:** Foundation phase (job queue implementation). The connection pool design must be correct from day one.

---

### Pitfall 3: LLM Supervisor Cost Spiral from Unbounded Context Snapshots

**What goes wrong:** The spec defines rich context snapshots for every LLM review: queue state, thinker data, candidate data, error logs, corpus stats. As the corpus grows, these context snapshots grow linearly. A health check that started at 2K tokens grows to 20K tokens as the system scales to hundreds of thinkers and thousands of content items. With health checks every 6 hours, daily digests, quota checks, and event-driven approvals, the monthly LLM bill escalates from the estimated $10-30/mo to $100-300/mo without any visible "spike" -- just gradual growth.

**Why it happens:** The spec correctly logs full context snapshots for auditability (`context_snapshot` JSONB in `llm_reviews`). But the prompt construction likely serializes entire query results rather than bounded summaries. The daily digest includes "last 24 hours" aggregates, but the weekly audit includes "full week summary: all content ingested, all LLM decisions made" -- which grows without bound.

**Consequences:**
- Monthly costs 5-10x the estimate, with no single event to trigger an alert
- Context windows may be exceeded, causing API errors and escalations
- Larger context = slower responses = longer `llm_approval_check` jobs = more connection hold time
- Cost pressure leads to switching to cheaper models, degrading governance quality

**Prevention:**
1. Cap context snapshots with hard limits: max 50 thinkers summarized, max 100 error entries, max 20 candidates per review
2. Use aggregated summaries (counts, top-N) instead of full row dumps for scheduled checks
3. Track `tokens_used` per review type and set alerts at 2x the baseline (the spec already has `tokens_used` in `llm_reviews` -- use it)
4. Implement prompt budgeting: measure the prompt size before sending, truncate if it exceeds a per-review-type limit
5. Cache repetitive context (thinker list, category tree) using Anthropic's prompt caching to reduce per-call cost
6. Set a hard monthly budget cap in `system_config` with automatic degradation (reduce health check frequency, skip non-critical reviews)

**Detection:**
- Dashboard API cost tracker (already specified) must have alerting thresholds, not just display
- Weekly audit should include LLM cost trend vs. prior weeks
- Alert when any single review exceeds 50K tokens

**Confidence:** HIGH -- this is a well-documented pattern with LLM-in-the-loop systems. The spec's cost estimate of $10-30/mo is realistic only for the first month with <50 thinkers.

**Phase relevance:** LLM Supervisor implementation phase. Context budgeting must be designed into the prompt construction, not bolted on after cost overruns.

---

### Pitfall 4: Content Fingerprint Collisions Causing Silent Data Loss

**What goes wrong:** The fingerprint `sha256(lowercase(title) || date_trunc('day', published_at) || coalesce(duration_seconds, 0))` has several collision vectors that cause legitimate unique content to be silently dropped:

1. **Same guest on multiple shows the same day** with similar episode titles: "Interview with [Thinker Name]" on two different podcasts, same date, similar duration -- fingerprints collide
2. **Multi-part episodes**: "Episode 301 Part 1" and "Episode 301 Part 2" published same day with duration=0 (before duration is populated) -- fingerprints collide
3. **Rebranded/renamed shows**: A podcast changes its name but keeps the same RSS feed URL -- old and new episodes may produce different `canonical_url` but identical fingerprints if titles match
4. **Duration=0 fallback**: `coalesce(duration_seconds, 0)` means any content without duration data has weaker fingerprints, increasing collision risk for all text-only content

**Why it happens:** The fingerprint design is a reasonable heuristic, but podcast metadata is messier than the spec assumes. Episode titles are not globally unique -- interview-style podcasts frequently produce near-identical titles. The `date_trunc('day')` granularity is intentional (to catch same-day cross-platform duplicates) but also the source of false positives.

**Consequences:**
- Legitimate content silently not inserted -- no error, no retry, just a skipped row
- The content that "wins" depends on insertion order, which depends on job scheduling, not content quality
- Gaps in thinker coverage are invisible until someone manually audits
- The dedup log (specified in admin dashboard) only shows fingerprint matches -- not whether they were true or false positives

**Prevention:**
1. Add `source_id` to the fingerprint: `sha256(lowercase(title) || date_trunc('day', published_at) || coalesce(duration_seconds, 0) || source_id)` -- different sources should never collide
2. Actually, reconsider: the fingerprint's purpose is to catch *cross-source* duplicates (same episode on Apple Podcasts and Spotify). Instead, add a secondary check: when a fingerprint collision is detected, compare `show_name` and `source_id`. If they differ, this is likely a cross-source duplicate (correct dedup). If they match, it is likely a false positive (different episode from the same show).
3. Log all fingerprint-based dedup events with both the existing and the rejected content metadata so false positives can be audited
4. Never dedup when `content_fingerprint` would be null (already handled by the spec's "null fingerprints excluded from uniqueness checks")
5. Add a periodic "dedup audit" job that checks for suspiciously similar fingerprints and flags them for human review

**Detection:**
- Track fingerprint collision rate per source. A source that consistently triggers fingerprint dedup (>10% of its episodes) is likely producing false positives.
- Admin dashboard dedup log should show side-by-side metadata for the collision pair
- Monitor for thinkers with unexpectedly low content counts relative to their source count

**Confidence:** MEDIUM -- the specific collision vectors are inferred from real-world podcast metadata patterns, not from documented failures of this exact fingerprint scheme. The general problem of content fingerprint false positives is well-known in web crawling literature.

**Phase relevance:** Content ingestion phase (deduplication implementation). Must be designed with auditability from the start.

---

### Pitfall 5: Railway GPU Cold Start Costs More Than Expected Due to NeMo Container + Model Load Time

**What goes wrong:** The spec estimates GPU cold start at "2-5 min for model load into VRAM." In practice, the full cold start sequence on Railway is:

1. Railway provisions the GPU instance (30-60 seconds)
2. Docker container starts from the `nvcr.io/nvidia/nemo:24.05` base image (this is a multi-GB image -- even with caching, container startup takes 30-60 seconds)
3. Python process starts, imports NeMo framework (30-60 seconds of import time -- NeMo is heavy)
4. Parakeet TDT 1.1B model loads from volume cache into VRAM (60-120 seconds)
5. First inference request warms the CUDA context (10-20 seconds)

Total realistic cold start: 3-6 minutes. During this time, you are billed for GPU compute. If the GPU scales down aggressively (30 min idle) and the transcription queue is bursty (5-10 items arrive in a batch, are processed in <15 minutes, then nothing for 2 hours), you pay cold start costs repeatedly.

**Why it happens:** The NeMo container is designed for training and research, not for fast cold starts. The `nemo:24.05` image includes CUDA, PyTorch, NeMo, and many unused dependencies. Railway's GPU provisioning adds platform overhead on top of the raw container startup.

**Consequences:**
- GPU cost 2-3x higher than estimated due to cold start overhead on bursty workloads
- The `gpu_idle_minutes_before_shutdown` default of 30 minutes may be too aggressive
- Workers that enqueue small batches of transcription jobs trigger expensive cold starts for minimal work
- The estimated $100-200/mo GPU cost could balloon to $300-500/mo with frequent scaling events

**Prevention:**
1. Increase `gpu_idle_minutes_before_shutdown` to 60 minutes to reduce cold start frequency
2. Increase `gpu_queue_threshold` from 5 to 15-20 to batch more work before spinning up the GPU
3. Build a minimal Docker image with only Parakeet inference dependencies instead of the full NeMo training image (can reduce image size from ~15GB to ~5GB)
4. Pre-warm the CUDA context with a dummy inference on startup before claiming jobs
5. Track actual cold start duration and GPU utilization percentage; optimize threshold based on real data
6. Consider a "keep-warm" schedule during peak discovery hours (e.g., keep GPU running during daily digest processing window when many new episodes are likely queued)

**Detection:**
- Log GPU service lifecycle events: scale-up time, first-job-claimed time, scale-down time
- Calculate "effective GPU utilization" = time_processing / time_billed
- Alert when utilization drops below 40% (meaning >60% of GPU cost is idle/startup time)

**Confidence:** MEDIUM -- Railway GPU cold start specifics are not well-documented publicly. The NeMo container size and import time are verifiable facts. The 3-6 minute total estimate is based on comparable setups on other platforms.

**Phase relevance:** GPU orchestration phase. The threshold tuning and container optimization are implementation details, but the *decision to track cold start metrics* must be designed upfront.

---

### Pitfall 6: Alembic Migrations Run Concurrently on Multi-Service Deploy, Corrupting Schema State

**What goes wrong:** The spec says the API service runs `alembic upgrade head` on startup. If Railway deploys a new version and starts multiple API replicas simultaneously (or if the API service restarts while the worker-cpu service is also starting), two processes attempt `alembic upgrade head` concurrently. Alembic does not acquire an advisory lock by default. Two concurrent migrations can:

1. Both see the same "current" revision and both attempt to apply the same migration
2. One succeeds, one fails with a "relation already exists" error
3. The Alembic version table may be left in an inconsistent state
4. Subsequent migrations fail because the revision chain is broken

**Why it happens:** Alembic assumes single-writer migration execution. Railway's deployment model starts services independently -- there is no orchestration guarantee that the API starts and completes migrations before workers start. The spec does not mention migration locking.

**Consequences:**
- Broken migration chain prevents all future schema changes
- Manual intervention required to fix `alembic_version` table
- Potential partial schema application (table created but index not, column added but constraint not)
- Downtime during fix

**Prevention:**
1. Add a PostgreSQL advisory lock wrapper around Alembic migrations:
   ```python
   # In migration runner
   with engine.connect() as conn:
       conn.execute(text("SELECT pg_advisory_lock(1)"))
       try:
           alembic.command.upgrade(config, "head")
       finally:
           conn.execute(text("SELECT pg_advisory_unlock(1)"))
   ```
2. Only run migrations from the API service, never from workers. Workers should check that migrations are current on startup and fail-fast if not.
3. Use Railway's deployment ordering if available, or implement a "migration complete" flag in `system_config` that workers check before starting their event loops
4. Every migration must be idempotent where possible (use `IF NOT EXISTS` for `CREATE TABLE`, `ADD COLUMN IF NOT EXISTS` for column additions)
5. Never use `ACCESS EXCLUSIVE` locks in migrations for tables that are actively queried (use `CREATE INDEX CONCURRENTLY` instead of `CREATE INDEX`)

**Detection:**
- Log migration execution with timing: start, end, revision applied
- Alert on migration failures (any non-zero exit from `alembic upgrade head`)
- Check `alembic_version` table consistency on every health check

**Confidence:** HIGH -- concurrent Alembic migration failures are well-documented in multi-replica deployments. The advisory lock pattern is the standard solution.

**Phase relevance:** Foundation phase (project setup and deployment). Must be implemented before any schema beyond the initial migration.

---

## Moderate Pitfalls

Mistakes that cause degraded performance, increased cost, or require significant debugging time.

---

### Pitfall 7: RSS Feed Date Parsing Produces Incorrect or Null Dates, Breaking Backfill Logic

**What goes wrong:** Feedparser's date parsing handles many formats but has known failures: 2-digit years parsed incorrectly (2017 becomes 300), timezone abbreviations not recognized (returns None), missing publication dates entirely. The spec's backfill logic depends on `published_at` to determine whether an episode falls within `approved_backfill_days`. Null or incorrect dates cause episodes to either be skipped (date appears outside the window) or over-ingested (date is null, so no date filter applies).

**Prevention:**
1. Never trust `entry.published_parsed` blindly. Implement a date normalization function that:
   - Falls back to `entry.updated_parsed` if `published_parsed` is None
   - Falls back to the RSS feed's `<lastBuildDate>` as a final resort
   - Rejects dates more than 20 years old or in the future
   - Logs every date parsing failure with the raw date string for debugging
2. Store the raw date string from the RSS feed alongside the parsed `published_at` for forensic analysis
3. For episodes with no parseable date, insert with `status = 'needs_review'` rather than guessing

**Detection:**
- Track null `published_at` rates per source. A source with >5% null dates likely has a parsing issue
- Alert when episodes are inserted with dates more than `approved_backfill_days * 2` in the past

**Confidence:** HIGH -- feedparser date parsing bugs are well-documented (GitHub issues #113, #114)

**Phase relevance:** RSS feed ingestion phase.

---

### Pitfall 8: `SELECT FOR UPDATE SKIP LOCKED` Performance Degrades with Large Jobs Table

**What goes wrong:** As the jobs table grows (the spec retains all completed and failed jobs for auditability), the `SELECT FOR UPDATE SKIP LOCKED` query scans more rows to find claimable work. Without a partial index, every job claim query touches the entire table. With 100K+ historical job rows, claim latency increases from <1ms to 50-100ms, and CPU usage spikes on the database.

**Prevention:**
1. Create a partial index: `CREATE INDEX idx_jobs_claimable ON jobs (priority, scheduled_at) WHERE status IN ('pending', 'retrying') AND scheduled_at <= NOW()`
2. Partition the jobs table by status or by month. Move completed/failed jobs to an archive partition.
3. Alternatively, implement a "reaper" job that moves jobs with `status IN ('done', 'failed', 'rejected_by_llm')` older than 30 days to a `jobs_archive` table
4. The claim query must use `LIMIT 1` to avoid locking more rows than necessary

**Detection:**
- Monitor job claim latency (time from query start to row returned)
- Track jobs table row count; alert when total rows exceed 50K
- PostgreSQL `pg_stat_user_tables` shows sequential scan vs. index scan ratio

**Confidence:** HIGH -- documented in PostgreSQL performance literature and the Postgres Professional mailing list thread on CPU hogging with SKIP LOCKED

**Phase relevance:** Job queue implementation phase. The partial index must be part of the initial schema. Archival strategy can come later but should be planned.

---

### Pitfall 9: Audio Download Failures from CDN Rate Limiting and Geo-Restrictions

**What goes wrong:** Podcast audio files are served from CDNs (Megaphone, Acast, Libsyn, Podbean, etc.) that implement rate limiting, geo-restrictions, and bot detection. When the system downloads audio for transcription at scale (potentially dozens of files per hour during backfill), CDNs may:
- Return 403 Forbidden after N downloads from the same IP
- Return 302 redirects to CAPTCHA pages
- Throttle download speed to 50KB/s, causing 60-minute downloads for a 100MB file
- Serve different content based on User-Agent (ad-injected versions, truncated previews)

**Prevention:**
1. Set a respectful User-Agent string identifying the service (not a browser UA): `ThinkTank/1.0 (podcast-ingestion; contact@example.com)`
2. Rate limit audio downloads per CDN domain (not just per API): max 5 downloads per domain per 10 minutes
3. Implement download timeouts: 10 minutes max per file, with resume support (`Range` headers) for large files
4. Retry with exponential backoff on 429/503 responses, but do not retry on 403 (likely permanent)
5. Log download speed; flag sources where average download speed is <100KB/s
6. Delete audio files immediately after transcription (already in spec) to avoid accumulating storage

**Detection:**
- Track download failure rates by CDN domain
- Monitor disk usage in `AUDIO_TMP_DIR` -- if files accumulate, downloads are succeeding but transcription is not keeping up
- Health check should include "audio files older than 2 hours" as a warning indicator

**Confidence:** MEDIUM -- specific CDN behaviors vary, but rate limiting of automated downloads is standard practice across podcast hosting platforms

**Phase relevance:** Transcription pipeline phase.

---

### Pitfall 10: TOAST Bloat from Frequent Transcript Updates in Content Table

**What goes wrong:** The `content.body_text` column stores full transcripts (potentially 50K-200K characters per episode). PostgreSQL stores these in TOAST tables (out-of-line). If transcripts are ever updated (re-transcription, correction, format change), the old TOAST chunks become dead tuples. With thousands of content rows, TOAST bloat can consume 2-5x the actual data size, degrading query performance and increasing storage costs.

**Prevention:**
1. Design the content table as append-only for `body_text`. If re-transcription is needed, update the row but understand the TOAST implications.
2. Configure `autovacuum_vacuum_scale_factor` lower for the content table (e.g., 0.05 instead of the default 0.2) to trigger more aggressive vacuuming
3. Schedule weekly `VACUUM` on the content table during low-activity periods
4. Monitor table size vs. `pg_total_relation_size` (which includes TOAST). If TOAST size > 2x visible data, bloat is accumulating.
5. If re-transcription is common, consider storing transcripts in a separate `transcripts` table with versioning, keeping only the latest version's ID on the content row

**Detection:**
- Query `pg_total_relation_size('content')` vs `pg_relation_size('content')` weekly
- Alert when TOAST overhead exceeds 50% of total relation size
- Monitor disk usage trends on Railway PostgreSQL

**Confidence:** MEDIUM -- TOAST bloat is well-documented for tables with large text columns. The severity depends on how often transcripts are updated, which should be rare in this system (write-once pattern).

**Phase relevance:** Database schema design phase. The `autovacuum` configuration should be set at table creation time.

---

### Pitfall 11: Candidate Thinker Name Dedup via pg_trgm Produces False Matches

**What goes wrong:** The spec uses `pg_trgm` with a 0.7 similarity threshold for candidate name deduplication. This threshold is too aggressive for short names. Examples:
- "Sam Harris" vs "Sam Harris-Smith" = similarity 0.75 (false positive)
- "John Lee" vs "John Li" = similarity 0.72 (false positive -- different people)
- "Dr. James Smith" vs "James Smith" = similarity ~0.8 after normalization (correct dedup, but what if they are different people?)
- "AI Smith" vs "Al Smith" = similarity 0.85 (false positive -- trigrams are character-based)

**Prevention:**
1. Raise the similarity threshold to 0.85 for names shorter than 15 characters
2. Add a secondary check: if trigram similarity > threshold, also verify at least one `sample_url` overlaps or `inferred_categories` overlap before deduplicating
3. Never auto-merge candidates. Candidates flagged as potential duplicates should be sent to the LLM Supervisor (or human review) with both names and their context
4. Create a GIN index on `candidate_thinkers.normalized_name` using `gin_trgm_ops` for performance
5. Also check against `thinkers.name` (already in spec) but with the same elevated threshold

**Detection:**
- Log all trigram-based dedup decisions with the similarity score and both names
- Track "duplicate" status candidates and periodically audit a sample for false positives
- Monitor for thinkers who appear in search results but are not in the system (possible false dedup victims)

**Confidence:** MEDIUM -- the specific similarity scores are computed from known pg_trgm behavior on short strings. The 0.7 threshold is a common default but inappropriate for person names.

**Phase relevance:** Cascade discovery phase (candidate thinker pipeline).

---

### Pitfall 12: Listen Notes Free Tier Rate Limit Silently Degrades Discovery

**What goes wrong:** Listen Notes free tier provides 10K requests/month. The spec estimates this is sufficient but does not account for:
- Backfill phase: discovering guest appearances for 50+ thinkers requires 5-20 API calls per thinker
- Pagination: each search result page is a separate API call
- Failed requests that still count against the quota
- The cascade discovery loop that surfaces new candidates, which triggers more guest searches

During the initial backfill of 50+ thinkers, the system can exhaust the monthly quota in 1-2 weeks. After exhaustion, discovery silently stops (429 errors are retried but never succeed until the quota resets).

**Prevention:**
1. Implement quota tracking at the monthly level (not just hourly rate limiting). Track cumulative Listen Notes calls in `api_usage` and alert at 50%, 75%, 90% of the monthly budget
2. Prioritize Listen Notes calls by thinker tier: Tier 1 first, Tier 3 last. If quota pressure builds, skip Tier 3 guest discovery entirely
3. Cache Listen Notes results aggressively -- guest appearance results for a thinker should be cached for 30 days
4. Plan for the paid tier ($50/mo in the cost estimate) from the start if the initial thinker list exceeds ~30
5. Implement Podcast Index as a true parallel path, not just a supplement. Podcast Index is free and has no rate limits beyond reasonable use

**Detection:**
- Rate limit gauges in dashboard (already specified) must include monthly cumulative view, not just hourly
- Alert when monthly cumulative usage exceeds 75% with >7 days remaining in the billing cycle
- Health check should flag "Listen Notes quota < 20% remaining"

**Confidence:** MEDIUM -- the exact rate depends on backfill depth and thinker count, both of which are configurable. But the free tier is genuinely limited for the described use case.

**Phase relevance:** Discovery implementation phase. Quota management must be built alongside the discovery jobs.

---

## Minor Pitfalls

Mistakes that cause friction, minor bugs, or unnecessary debugging time.

---

### Pitfall 13: yt-dlp Version Drift Breaks Audio Extraction

**What goes wrong:** yt-dlp releases new versions frequently (sometimes weekly) to keep up with YouTube's anti-bot changes. Pinning to an old version risks YouTube breakage. Not pinning risks format changes (recent issue: DASH audio-only formats disappeared in yt-dlp 2026.03.03). Either way, audio extraction breaks unpredictably.

**Prevention:**
1. Pin yt-dlp to a specific version in requirements and test before upgrading
2. Wrap yt-dlp calls in a try/except that catches format-not-found errors and logs them with the yt-dlp version
3. Since the spec limits YouTube to Tier 1 own channels only, this has limited blast radius -- but it still affects transcription for those channels
4. Consider using the yt-dlp Python API instead of subprocess calls for better error handling

**Detection:** Track yt-dlp error rates separately from general transcription errors

**Confidence:** HIGH -- yt-dlp version breakage is a recurring reality documented in their issue tracker

**Phase relevance:** Transcription pipeline phase.

---

### Pitfall 14: Backpressure Priority Demotion Creates Starvation for Low-Priority Jobs

**What goes wrong:** The spec's backpressure mechanism demotes discovery job priority by +3 when the transcription queue is deep. But priority is a SMALLINT 1-10 field. A priority-5 discovery job demoted to priority-8 competes with metrics snapshots (priority 8). A priority-7 candidate scan demoted to priority-10 may never run if the queue is perpetually deep. Over time, certain job types become permanently starved.

**Prevention:**
1. Implement a "floor priority" per job type: no job type can be demoted below its floor
2. Track how long each job type has been starved (no successful completion in N hours) and temporarily boost priority
3. Consider a separate "discovery" queue vs "processing" queue rather than a single priority-ordered queue -- this avoids priority inversion entirely
4. The stale job reclaimer should also check for jobs that have been in `pending` state for >24 hours despite having an overdue `scheduled_at`

**Detection:**
- Dashboard should show job completion rate by job type over time
- Alert when any job type has 0 completions in 24 hours while having pending jobs

**Confidence:** MEDIUM -- the exact behavior depends on workload distribution, but priority starvation is a well-known queuing theory problem

**Phase relevance:** Job queue implementation phase.

---

### Pitfall 15: Podcast RSS Feed Pagination and Episode Limits Cause Incomplete Backfill

**What goes wrong:** Many podcast hosts (Apple Podcasts, Libsyn, Podbean) paginate their RSS feeds, serving only the most recent 100-300 episodes per feed request. The spec assumes RSS feeds contain the full episode history within `approved_backfill_days`. For prolific podcasts (daily shows with 1000+ episodes), the RSS feed URL returns only recent episodes. The backfill marks `backfill_complete = true` after processing 100 episodes, missing years of history.

**Prevention:**
1. Detect feed pagination: if the oldest episode in the feed is more recent than `approved_backfill_days` ago, the feed is likely paginated
2. Check for `<atom:link rel="next">` pagination links in the RSS feed
3. For known podcast platforms, use their API endpoints (if available) instead of RSS for historical backfill
4. Log the oldest episode date found in each feed; flag sources where this date is much more recent than `approved_backfill_days`
5. Store `episodes_in_feed` count and compare to external sources (Listen Notes episode count) to detect truncation

**Detection:**
- Compare episodes discovered per source vs. episodes reported by Listen Notes/Podcast Index
- Flag sources where `backfill_complete = true` but episode count is suspiciously low

**Confidence:** MEDIUM -- RSS feed truncation is standard practice for large podcasts, but the severity depends on the specific shows being tracked

**Phase relevance:** RSS feed ingestion phase.

---

### Pitfall 16: ffmpeg Audio Conversion Failures on Edge-Case Formats

**What goes wrong:** The spec requires converting audio to "16kHz mono WAV" for Parakeet. Some podcast episodes use uncommon audio codecs (Opus in WebM containers, AAC-HE v2, variable bitrate MP3 with bad headers). ffmpeg can usually handle these, but edge cases cause silent failures: output file is created but contains silence, or is truncated, or has the wrong sample rate. The transcription runs on garbage audio and produces garbage text that is stored as the canonical transcript.

**Prevention:**
1. After ffmpeg conversion, verify the output file: check file size (should be proportional to duration), sample rate (must be 16000), channel count (must be 1), and duration (must be within 5% of source duration)
2. Reject transcripts with suspiciously low word count relative to duration (e.g., <10 words per minute)
3. Log the ffmpeg stderr output for every conversion -- it contains warnings about format issues
4. Maintain a list of known-problematic audio formats and test against them

**Detection:**
- Word-count-to-duration ratio: healthy podcasts produce 120-180 words per minute. <50 words/minute suggests transcription failure
- Track transcription quality metrics per source; flag sources with consistently low word counts

**Confidence:** MEDIUM -- ffmpeg edge cases are real but rare. The verification step is the critical prevention measure.

**Phase relevance:** Transcription pipeline phase.

---

## Phase-Specific Warnings

| Phase Topic | Likely Pitfall | Mitigation |
|-------------|---------------|------------|
| Foundation / DB schema | Alembic concurrent migration (#6), connection pool exhaustion (#2), jobs table bloat (#8) | Advisory lock on migrations, separate pools for claiming vs execution, partial index on jobs |
| RSS feed ingestion | Feedparser hangs (#1), date parsing (#7), feed pagination (#15) | httpx for network I/O with timeouts, date normalization with fallbacks, pagination detection |
| Job queue implementation | SKIP LOCKED performance (#8), priority starvation (#14) | Partial indexes, floor priorities per job type |
| Transcription pipeline | GPU cold start costs (#5), audio download failures (#9), ffmpeg edge cases (#16), yt-dlp breakage (#13) | Higher queue threshold, per-CDN rate limiting, output verification, pinned versions |
| Content deduplication | Fingerprint collisions (#4), candidate name dedup (#11) | Source-aware fingerprinting, raised trigram threshold for short names |
| LLM Supervisor | Cost spiral (#3), context snapshot growth | Bounded context with aggregated summaries, prompt budgeting, monthly cost cap |
| Discovery pipeline | Listen Notes quota (#12) | Monthly quota tracking, Podcast Index as parallel path, per-tier prioritization |
| Content storage | TOAST bloat (#10) | Aggressive autovacuum, append-only design for body_text |

---

## Sources

- [Feedparser hanging issue #76](https://github.com/kurtmckee/feedparser/issues/76)
- [Feedparser parse() does not return, issue #263](https://github.com/kurtmckee/feedparser/issues/263)
- [Feedparser timeout issue #245](https://github.com/HaveF/feedparser/issues/245)
- [Feedparser date parsing bugs, issues #113, #114](https://github.com/kurtmckee/feedparser/issues/113)
- [PostgreSQL SKIP LOCKED CPU hogging thread](https://postgrespro.com/list/thread-id/2505440)
- [The Unreasonable Effectiveness of SKIP LOCKED](https://www.inferable.ai/blog/posts/postgres-skip-locked)
- [SQLAlchemy asyncpg connection leak on cancellation, issue #6652](https://github.com/sqlalchemy/sqlalchemy/issues/6652)
- [SQLAlchemy async connection pool exhaustion, issue #5546](https://github.com/sqlalchemy/sqlalchemy/issues/5546)
- [Alembic migrations without downtime](https://medium.com/exness-blog/alembic-migrations-without-downtime-a3507d5da24d)
- [The Hidden Bias of Alembic Migrations](https://atlasgo.io/blog/2025/02/10/the-hidden-bias-alembic-django-migrations)
- [PostgreSQL TOAST optimization](https://www.percona.com/blog/unlocking-the-secrets-of-toast-how-to-optimize-large-column-storage-in-postgresql-for-top-performance-and-scalability/)
- [Medium-size text performance impact in PostgreSQL](https://hakibenita.com/sql-medium-text-performance)
- [NVIDIA Parakeet TDT blog post](https://developer.nvidia.com/blog/turbocharge-asr-accuracy-and-speed-with-nvidia-nemo-parakeet-tdt/)
- [yt-dlp DASH format changes, issue #16128](https://github.com/yt-dlp/yt-dlp/issues/16128)
- [Railway scaling documentation](https://docs.railway.com/reference/scaling)
- [Anthropic API pricing and cost optimization](https://www.finout.io/blog/anthropic-api-pricing)
- [FinOps for Claude: managing API costs at scale](https://www.cloudzero.com/blog/finops-for-claude/)
- [PostgreSQL pg_trgm documentation](https://www.postgresql.org/docs/current/pgtrgm.html)
- [URL normalization for de-duplication of web pages](https://www.cs.cornell.edu/~hema/papers/sp0955-agarwalATS.pdf)
