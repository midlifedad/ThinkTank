# THINKTANK
## Global Intelligence Ingestion Platform — Full System Specification
*Version 1.0 · March 2026*

---

ThinkTank is a continuous ingestion and storage engine that discovers, fetches, and transcribes public content from the world's leading thinkers — **primarily podcasts**, with extremely limited YouTube usage — into a structured relational database. The corpus is designed to be the foundation for downstream intelligence extraction, semantic search, claim analysis, and global trend synthesis **once ingestion is perfected**.

A core design constraint: **no consequential action runs without LLM approval.** Workers operate autonomously only within pre-approved parameters. Every expansion of scope — a new thinker, a new source, a promoted candidate — requires explicit sign-off from the LLM Supervisor before execution.

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Technology Stack](#2-technology-stack)
3. [Database Schema](#3-database-schema)
4. [Bootstrap & Initial Parameters](#4-bootstrap--initial-parameters)
5. [Discovery & Scraping](#5-discovery--scraping)
6. [Job Types & Worker Handlers](#6-job-types--worker-handlers)
7. [Transcription Pipeline](#7-transcription-pipeline)
8. [LLM Supervisor](#8-llm-supervisor)
9. [Admin Dashboard](#9-admin-dashboard)
10. [Deployment](#10-deployment)

---

## 1. System Overview

### 1.1 Core Design Principles

- **Everything is a job.** Every unit of work is a row in the jobs table. Workers claim and process jobs. The system is fully resumable after a crash.
- **Discovery compounds.** Every podcast show touched becomes a source of candidate thinkers. The system surfaces unfamiliar names for review, growing the corpus organically.
- **Text first, audio last, YouTube last.** Podcasts are the primary ingestion path. YouTube used only when no podcast source exists.
- **One database, full visibility.** Job queue, content, metrics, candidate pipeline, and LLM review log all live in Postgres.
- **LLM approval at every decision boundary.** Workers are autonomous within approved parameters only. Any expansion of scope — new thinker, new source, promoted candidate — requires LLM Supervisor sign-off before execution. Workers cannot self-authorise growth.
- **Operational robustness without external dependencies.** Rate limiting, stale job reclamation, backpressure, and GPU orchestration are all coordinated through Postgres — no Redis, no external schedulers. Workers self-heal: stuck jobs are reclaimed, rate limits are enforced cooperatively, and queue depth governs GPU scaling automatically.

### 1.2 Service Architecture

| Service | Description |
|---|---|
| **API** | FastAPI. Management endpoints and LLM Supervisor webhook receiver. |
| **Worker (CPU)** | Always-on. Discovery, scraping, RSS, API calls. Operates only on approved jobs. |
| **Worker (GPU)** | On-demand Railway L4. Transcription only. Scaled by CPU worker via Railway API based on queue depth. |
| **LLM Supervisor** | Scheduled Claude API calls. Approval engine + health monitor. Runs on CPU worker instance. |
| **Database** | Railway managed PostgreSQL. Single source of truth for all state. |
| **Admin UI** | HTMX + FastAPI. Human oversight layer above the LLM Supervisor. |

### 1.3 Approval vs Autonomous Boundary

Workers operate **autonomously** (no approval needed) for:
- Fetching episodes from an already-approved source
- Inserting content items from an approved source
- Transcribing content from an approved source
- Snapshotting metrics for an existing thinker
- Retrying failed jobs within normal backoff limits

Workers require **LLM approval** before:
- Running `discover_thinker` for a new thinker
- Registering a new source against any thinker
- Promoting a candidate to a full thinker
- Resuming a source that has hit 3+ consecutive errors
- Any operation that would expand the corpus beyond approved parameters

---

## 2. Technology Stack

### 2.1 Infrastructure

| Component | Choice / Rationale |
|---|---|
| Cloud platform | Railway — single platform for all services. |
| Database | PostgreSQL 16 (Railway managed) — all state including transcripts, job queue, LLM review log. |
| GPU instance | Railway L4 (24GB VRAM) — Parakeet only. On-demand. |
| Persistent volume | Railway volume at `/app/.nemo_cache` — Parakeet model cache. |
| Container base | `nvcr.io/nvidia/nemo:24.05` — CUDA, PyTorch, NeMo pre-installed. |

### 2.2 Backend

| Component | Choice / Rationale |
|---|---|
| API framework | FastAPI |
| DB driver | asyncpg |
| DB migrations | Alembic — incremental schema migrations from day one. |
| HTTP client | httpx |
| RSS parsing | feedparser |
| Audio extraction | yt-dlp (own channels only) |
| Transcription | Parakeet TDT 1.1B (NVIDIA NeMo) |
| Audio processing | ffmpeg + soundfile |
| LLM Supervisor | Anthropic Claude API (claude-sonnet-4-20250514) |

### 2.3 External APIs

| API | Usage / Cost |
|---|---|
| Listen Notes | Primary podcast guest discovery. Free tier: 10k req/mo. |
| Podcast Index | Primary podcast feed discovery. Free. |
| YouTube Data API v3 | Own channels only, heavily throttled. |
| X/Twitter API v2 | Metrics snapshots only. |
| Anthropic Claude API | LLM Supervisor. Approvals + health checks. ~$10–30/mo at current cadence. |

---

## 3. Database Schema

### 3.1 `categories`

| Column | Type | Description |
|---|---|---|
| `id` | UUID PK | |
| `slug` | TEXT UNIQUE | e.g. `ai_models`, `macro_economics` |
| `name` | TEXT | Display name |
| `parent_id` | UUID FK → categories | Null for top-level |
| `description` | TEXT | What belongs here |
| `created_at` | TIMESTAMPTZ | |

### 3.2 `thinkers`

| Column | Type | Description |
|---|---|---|
| `id` | UUID PK | |
| `name` | TEXT | |
| `slug` | TEXT UNIQUE | e.g. `andrej-karpathy` |
| `tier` | SMALLINT | 1 = top, 2 = notable, 3 = emerging |
| `bio` | TEXT | |
| `primary_affiliation` | TEXT | |
| `twitter_handle` | TEXT | |
| `wikipedia_url` | TEXT | |
| `personal_site` | TEXT | |
| `approval_status` | TEXT | `pending_llm` \| `approved` \| `rejected`. New thinkers start as `pending_llm` until supervisor approves. |
| `approved_backfill_days` | INT | Approved by LLM at review time. Workers cannot exceed this. |
| `approved_source_types` | TEXT[] | Which source types are approved for this thinker, e.g. `['podcast_rss','substack']` |
| `active` | BOOLEAN | False = stop all activity |
| `added_at` | TIMESTAMPTZ | |
| `last_refreshed` | TIMESTAMPTZ | |

### 3.3 `thinker_categories` (junction)

| Column | Type | Description |
|---|---|---|
| `thinker_id` | UUID FK → thinkers | |
| `category_id` | UUID FK → categories | |
| `relevance` | SMALLINT 1–10 | |
| `added_at` | TIMESTAMPTZ | |

### 3.4 `thinker_profiles`

| Column | Type | Description |
|---|---|---|
| `id` | UUID PK | |
| `thinker_id` | UUID FK → thinkers | |
| `education` | JSONB | `[{school, degree, field, year}]` |
| `positions_held` | JSONB | `[{title, organisation, from_year, to_year}]` |
| `notable_works` | JSONB | `[{type, title, year, url}]` |
| `awards` | JSONB | `[{name, org, year}]` |
| `updated_at` | TIMESTAMPTZ | |

### 3.5 `thinker_metrics`

| Column | Type | Description |
|---|---|---|
| `id` | UUID PK | |
| `thinker_id` | UUID FK → thinkers | |
| `platform` | TEXT | `youtube`, `twitter`, `instagram`, `linkedin`, `substack`, `podcast` |
| `handle` | TEXT | |
| `followers` | BIGINT | |
| `avg_views` | BIGINT | |
| `post_count` | INT | |
| `verified` | BOOLEAN | |
| `snapshotted_at` | TIMESTAMPTZ | |

### 3.6 `sources`

| Column | Type | Description |
|---|---|---|
| `id` | UUID PK | |
| `thinker_id` | UUID FK → thinkers | |
| `source_type` | TEXT | `podcast_rss`, `youtube_channel`, `substack`, `blog_rss`, `arxiv` |
| `name` | TEXT | |
| `url` | TEXT UNIQUE | |
| `external_id` | TEXT | |
| `config` | JSONB | Per-source overrides: auth headers, date format hints, transcript URL patterns, episode title skip patterns. Workers check before processing. Default `{}`. |
| `approval_status` | TEXT | `pending_llm` \| `approved` \| `rejected`. Workers will not fetch until `approved`. |
| `approved_backfill_days` | INT | Set by LLM at approval. Hard cap workers cannot exceed. |
| `backfill_complete` | BOOLEAN | True once historical fetch done. Workers only fetch new items after this. |
| `refresh_interval_hours` | INT | Tier 1 = 6, Tier 2 = 24, Tier 3 = 168 |
| `last_fetched` | TIMESTAMPTZ | Cutoff for incremental refreshes after backfill complete. |
| `item_count` | INT | |
| `active` | BOOLEAN | |
| `error_count` | INT | Consecutive errors. At 3: source paused, LLM notified. |
| `created_at` | TIMESTAMPTZ | |

### 3.7 `content`

| Column | Type | Description |
|---|---|---|
| `id` | UUID PK | |
| `source_id` | UUID FK → sources | |
| `source_owner_id` | UUID FK → thinkers | The thinker whose source produced this content. For guest appearances, this is the host — not the guest. All thinker attribution goes through `content_thinkers`. |
| `content_type` | TEXT | `episode`, `video`, `article`, `paper`, `post` |
| `url` | TEXT | Original URL as discovered. |
| `canonical_url` | TEXT UNIQUE | Normalized URL: forced https, stripped www/tracking params, YouTube IDs canonicalized. Deduplication key. |
| `content_fingerprint` | TEXT UNIQUE | `sha256(lowercase(title) \|\| date_trunc('day', published_at) \|\| coalesce(duration_seconds, 0))`. Catches same content at different URLs (e.g. same episode on Apple Podcasts and Spotify). Null for content without a title yet. |
| `title` | TEXT | |
| `body_text` | TEXT | Full transcript or article body in Postgres |
| `word_count` | INT | |
| `published_at` | TIMESTAMPTZ | |
| `duration_seconds` | INT | Audio/video only |
| `show_name` | TEXT | |
| `host_name` | TEXT | |
| `thumbnail_url` | TEXT | |
| `transcription_method` | TEXT | `youtube_captions`, `parakeet`, `existing_transcript` |
| `status` | TEXT | `pending` \| `processing` \| `done` \| `error` \| `skipped` |
| `error_message` | TEXT | |
| `discovered_at` | TIMESTAMPTZ | |
| `processed_at` | TIMESTAMPTZ | |

**Deduplication strategy:** Insert checks `canonical_url` first (exact match), then `content_fingerprint` (fuzzy match). If fingerprint matches an existing row, the new URL is logged as an alias but no duplicate content row is created. See Section 5.5.

### 3.8 `content_thinkers` (junction)

Links thinkers to content with role attribution. Populated by the `tag_content_thinkers` job — see Section 6.6.
| Column | Type | Description |
|---|---|---|
| `content_id` | UUID FK → content | |
| `thinker_id` | UUID FK → thinkers | |
| `role` | TEXT | `primary`, `host`, `guest`, `co_host`, `panelist`, `mentioned` |
| `confidence` | SMALLINT 1–10 | How confident the attribution is. 10 = exact name match in title. 5 = fuzzy match in description. Used to filter low-confidence attributions in downstream analysis. |
| `added_at` | TIMESTAMPTZ | |

### 3.9 `candidate_thinkers`

| Column | Type | Description |
|---|---|---|
| `id` | UUID PK | |
| `name` | TEXT | Name as it appeared in content |
| `normalized_name` | TEXT | Lowercased, titles stripped (Dr., Prof., Ph.D.), unicode normalized. Used for dedup comparisons. |
| `appearance_count` | INT | |
| `first_seen_at` | TIMESTAMPTZ | |
| `last_seen_at` | TIMESTAMPTZ | |
| `sample_urls` | TEXT[] | Up to 5 content URLs |
| `inferred_categories` | TEXT[] | |
| `suggested_twitter` | TEXT | |
| `suggested_youtube` | TEXT | |
| `status` | TEXT | `pending_llm` \| `approved` \| `rejected` \| `duplicate`. **Candidates go to LLM first, not admin.** |
| `llm_review_id` | UUID FK → llm_reviews | The review that processed this candidate |
| `reviewed_by` | TEXT | `llm` or admin username if overridden |
| `reviewed_at` | TIMESTAMPTZ | |
| `thinker_id` | UUID FK → thinkers | Populated on approval |

### 3.10 `jobs`

| Column | Type | Description |
|---|---|---|
| `id` | UUID PK | |
| `job_type` | TEXT | |
| `payload` | JSONB | |
| `status` | TEXT | `pending` \| `awaiting_llm` \| `running` \| `done` \| `failed` \| `retrying` \| `rejected_by_llm` |
| `priority` | SMALLINT 1–10 | |
| `attempts` | SMALLINT | |
| `max_attempts` | SMALLINT | |
| `error` | TEXT | |
| `error_category` | TEXT | `rss_parse`, `youtube_rate_limit`, `transcription_failed`, etc. |
| `last_error_at` | TIMESTAMPTZ | |
| `worker_id` | TEXT | |
| `llm_review_id` | UUID FK → llm_reviews | If this job required LLM approval |
| `scheduled_at` | TIMESTAMPTZ | |
| `started_at` | TIMESTAMPTZ | |
| `completed_at` | TIMESTAMPTZ | |
| `created_at` | TIMESTAMPTZ | |

### 3.11 `llm_reviews`

Every LLM Supervisor decision is logged here — approvals, rejections, health checks, and scheduled audits. This is the full audit trail.

| Column | Type | Description |
|---|---|---|
| `id` | UUID PK | |
| `review_type` | TEXT | `thinker_approval`, `source_approval`, `candidate_approval`, `error_resume`, `health_check`, `daily_digest`, `quota_check` |
| `trigger` | TEXT | `scheduled`, `job_gate`, `error_threshold`, `manual` |
| `context_snapshot` | JSONB | What the LLM was shown: queue state, thinker data, candidate data, error log, etc. |
| `prompt_used` | TEXT | Full prompt sent to Claude |
| `llm_response` | TEXT | Full raw response |
| `decision` | TEXT | `approved`, `rejected`, `approved_with_modifications`, `escalate_to_human`, `no_action` |
| `decision_reasoning` | TEXT | Extracted reasoning from LLM response |
| `modifications` | JSONB | Any parameter changes the LLM specified (e.g. reduced backfill_days, restricted source types) |
| `flagged_items` | JSONB | Items flagged for human review |
| `overridden_by` | TEXT | Admin username if this decision was overridden. Null otherwise. |
| `overridden_at` | TIMESTAMPTZ | |
| `override_reasoning` | TEXT | Admin's reasoning for the override. |
| `model` | TEXT | Claude model used |
| `tokens_used` | INT | |
| `duration_ms` | INT | |
| `created_at` | TIMESTAMPTZ | |

### 3.12 `system_config`

Global operational parameters. Workers read these on each job claim. LLM Supervisor can modify them. Admin can override.

| Column | Type | Description |
|---|---|---|
| `key` | TEXT UNIQUE | Parameter name |
| `value` | JSONB | Parameter value |
| `set_by` | TEXT | `llm`, `admin`, `seed` |
| `updated_at` | TIMESTAMPTZ | |

**Default config entries:**

| Key | Default Value | Description |
|---|---|---|
| `workers_active` | `true` | Global kill switch. LLM or admin can halt all workers. |
| `max_candidates_per_day` | `20` | Maximum new candidates the cascade can surface per day before workers pause and request LLM review. |
| `max_new_sources_per_day` | `10` | Maximum new sources that can be queued for approval per day. |
| `max_episodes_per_thinker_per_run` | `50` | Cap on episodes fetched per thinker per discovery run. |
| `default_backfill_days` | `365` | Starting backfill depth proposed to LLM at thinker approval. |
| `gpu_queue_threshold` | `5` | `process_content` queue depth that triggers GPU worker deployment. |
| `gpu_idle_minutes_before_shutdown` | `30` | Minutes of empty `process_content` queue before GPU service is scaled to 0. |
| `listennotes_calls_per_hour` | `100` | Rate limit cap applied by workers. |
| `youtube_calls_per_hour` | `50` | YouTube Data API budget per hour. |
| `health_check_interval_hours` | `6` | How often the scheduled health check runs. |
| `daily_digest_hour_utc` | `7` | Hour (UTC) the daily digest runs. |
| `min_duration_seconds` | `600` | Episodes shorter than this are auto-skipped. Filters ads, trailers, and promos. |
| `skip_title_patterns` | `["trailer", "teaser", "best of", "rerun", "rebroadcast", "ad break", "bonus:", "announcement"]` | Case-insensitive title substrings. Matching episodes get `status = 'skipped'`. |
| `max_pending_transcriptions` | `500` | When `process_content` queue exceeds this, discovery job priority is demoted by +3. Prevents unbounded queue growth. |
| `llm_timeout_hours` | `2` | Jobs in `awaiting_llm` longer than this are auto-escalated to human review in the admin dashboard. |
| `stale_job_timeout_minutes` | `30` | Jobs in `running` state longer than this are reclaimed and returned to queue. |

### 3.13 `rate_limit_usage`
Sliding-window rate limit coordination between concurrent workers. Workers acquire a slot before calling external APIs. No external dependencies — pure Postgres.

| Column | Type | Description |
|---|---|---|
| `id` | UUID PK | |
| `api_name` | TEXT | `listennotes`, `youtube`, `podcastindex`, `twitter`, `anthropic` |
| `worker_id` | TEXT | Which worker made the call |
| `called_at` | TIMESTAMPTZ | Timestamp of the API call |

**Index:** `(api_name, called_at)` — enables fast sliding-window COUNT queries.

Workers check before calling:

```sql
SELECT COUNT(*) FROM rate_limit_usage
WHERE api_name = $1
  AND called_at > NOW() - INTERVAL '1 hour'
```

If count >= limit from `system_config`, the worker backs off for 30 seconds and retries the check. On successful acquisition, the worker inserts a row before making the API call. Rows older than 2 hours are purged by the hourly `refresh_due_sources` job.

### 3.14 `api_usage`
Aggregated API usage for cost monitoring and dashboard reporting. Rolled up hourly from `rate_limit_usage` plus internal tracking of unit costs.

| Column | Type | Description |
|---|---|---|
| `id` | UUID PK | |
| `api_name` | TEXT | `listennotes`, `youtube`, `podcastindex`, `twitter`, `anthropic` |
| `endpoint` | TEXT | Specific endpoint or operation, e.g. `search`, `channels.list`, `messages.create` |
| `period_start` | TIMESTAMPTZ | Start of the aggregation window (truncated to hour) |
| `call_count` | INT | Number of calls in this window |
| `units_consumed` | INT | API-specific units (e.g. YouTube quota units — a search costs 100, a videos.list costs 1). Null for APIs without unit-based billing. |
| `estimated_cost_usd` | NUMERIC(10,4) | Estimated cost in USD. Null if not applicable. |

**Index:** `(api_name, period_start)` — enables dashboard time-series queries.

A background task rolls up `rate_limit_usage` rows into `api_usage` hourly and purges the raw rows. The daily digest and weekly audit include `api_usage` summaries.

---

## 4. Bootstrap & Initial Parameters

This section specifies exactly how the system is initialised — what must exist before any workers run, what parameters govern the first jobs, and what "ready to run" looks like.

### 4.1 Bootstrap Sequence

Bootstrap must be completed in this order. Nothing in a later step runs until the prior step is verified.

**Step 1 — Schema migration**Run `alembic upgrade head` to apply the full schema. The initial Alembic migration contains the complete schema. All subsequent schema changes are incremental Alembic migrations — never raw DDL on a database with data.

**Step 2 — Category taxonomy**
The category tree must be seeded before any thinkers are added. A thinker cannot be approved without at least one category assignment. Initial taxonomy (expandable at any time):

```
knowledge
├── artificial_intelligence
│   ├── ai_models
│   ├── ai_safety
│   ├── ai_infrastructure
│   └── ai_applications
├── exponential_technology
│   ├── biotech
│   ├── energy_tech
│   ├── space
│   └── robotics
├── economics
│   ├── macro
│   ├── crypto
│   └── future_of_work
├── society
│   ├── philosophy
│   ├── governance
│   └── media
└── business
    ├── venture
    ├── investing
    └── entrepreneurship
```

**Step 3 — System config defaults**
All `system_config` entries seeded with their default values. `workers_active` seeded as `false` — no workers start until explicitly flipped after LLM review.

**Step 4 — Seed thinker list**
Initial thinkers loaded from `seed_thinkers.py`. Each thinker is inserted with `approval_status = 'pending_llm'`. No discovery runs until approved.

**Step 5 — LLM Supervisor first run**
The LLM Supervisor processes the full pending thinker list in a single batch review before any workers activate. It approves, rejects, or modifies each thinker's parameters. Only after this first review does `workers_active` flip to `true`.

**Step 6 — Workers activate**
CPU workers start. They only process jobs for thinkers and sources with `approval_status = 'approved'`.

### 4.2 Minimum Required Data Per Thinker

A thinker record cannot be submitted for LLM approval without:

| Field | Required | Notes |
|---|---|---|
| `name` | Yes | Full name |
| `tier` | Yes | 1, 2, or 3 |
| Categories | Yes | At least one category with relevance score |
| At least one source | Yes | Must have a resolvable URL |
| `proposed_backfill_days` | Yes | Proposed value. LLM may modify downward. |

Optional but strongly preferred: `twitter_handle`, `wikipedia_url`, `primary_affiliation`. The LLM Supervisor uses these to verify identity and assess relevance.

### 4.3 Per-Thinker Worker Parameters

These are set at approval time and stored on the thinker and source records. Workers treat them as hard limits:

| Parameter | Where Stored | Description |
|---|---|---|
| `approved_backfill_days` | `thinkers` | Maximum days back workers can fetch. Immutable after approval without a new LLM review. |
| `approved_source_types` | `thinkers` | Which source types are active for this thinker. Workers skip unapproved types. |
| `refresh_interval_hours` | `sources` | Set by tier at approval. Can be modified by LLM in a health check. |
| `approved_backfill_days` | `sources` | Per-source override. Source-level limit ≤ thinker-level limit. |

### 4.4 What "Ready to Run" Looks Like

The system is considered bootstrapped and ready when:

- Alembic migrations applied successfully (`alembic current` matches head)- Category taxonomy is populated (minimum 5 top-level categories)
- At least 10 thinkers have `approval_status = 'approved'`
- At least one approved source exists per approved thinker
- `workers_active = true` in `system_config`
- At least one successful LLM health check has completed and been logged in `llm_reviews`
- Admin dashboard is accessible and showing queue depth

---

## 5. Discovery & Scraping

### 5.1 Per-Thinker Discovery Sequence

When `discover_thinker` runs (only for approved thinkers), it fans out jobs for approved source types only:

- **Own podcast feeds** — fetch each approved RSS source
- **Substack** — if `substack` in `approved_source_types`
- **YouTube channel** — Tier 1 only, if `youtube_channel` in `approved_source_types`
- **Podcast guest search** — Listen Notes + Podcast Index, within rate limits from `system_config`
- **Metrics refresh** — YouTube API + X/Twitter API v2 follower snapshots

### 5.2 YouTube Policy

- Own channels for Tier 1 thinkers only, and only if explicitly in `approved_source_types`
- Guest appearances discovered exclusively via Listen Notes and Podcast Index
- `yt-dlp` limited to own-channel audio extraction when captions unavailable
- Third-party YouTube transcript services evaluated Month 2 only if podcast coverage insufficient

### 5.3 Cascade Discovery

- Every podcast show encountered is logged as a candidate source
- Episode titles and descriptions scanned for names not in `thinkers` table
- Names appearing in 3+ episodes logged in `candidate_thinkers` with `status = 'pending_llm'`
- **Candidates go to LLM Supervisor first — not to the admin queue**
- If `max_candidates_per_day` is reached, cascade discovery pauses and a `quota_check` review is triggered
- Workers do not surface more candidates until the LLM has reviewed the existing queue

### 5.4 Source Type Details

| Source Type | Notes |
|---|---|
| **Podcast (own)** | RSS feed. Full backfill within `approved_backfill_days` on first run. Incremental after `backfill_complete = true`. |
| **Podcast (guest)** | Listen Notes + Podcast Index. Fuzzy name match. Guest feeds fetched as RSS and filtered. |
| **YouTube (channel)** | Tier 1 own channels only. YouTube Data API v3. |
| **Substack** | RSS feed. Full body in RSS for free posts. |
| **Blog/Personal site** | RSS if available. HTML extraction Phase 2. |
| **arXiv / papers** | Phase 2. |

### 5.5 Deduplication
Three layers of deduplication, applied in order:

**Layer 1 — URL normalization**
Before any uniqueness check, URLs are normalized to a canonical form:
- Force `https://`
- Strip `www.`
- Strip tracking parameters (`utm_*`, `ref`, `fbclid`, `gclid`)
- YouTube: extract video ID, canonicalize to `https://youtube.com/watch?v={id}`
- Podcast URLs: strip session tokens and CDN parameters from enclosure URLs
- Store both `url` (original) and `canonical_url` (normalized) on the content row. `canonical_url` carries the UNIQUE constraint.

**Layer 2 — Content fingerprinting**
Same content often appears at different URLs across platforms. After URL dedup passes, check the content fingerprint:

```
fingerprint = sha256(lowercase(title) || date_trunc('day', published_at) || coalesce(duration_seconds, 0))
```

If fingerprint matches an existing content row:
- Skip insertion — do not create a duplicate
- Optionally log the alias URL for provenance tracking
- If the new URL came from a higher-priority source (own podcast > guest discovery), update `source_id` on the existing row

`content_fingerprint` carries a UNIQUE constraint. Null fingerprints (content without a title yet) are excluded from uniqueness checks.

**Layer 3 — Source and candidate dedup**
- Sources: unique on `url` (no normalization needed — source URLs are manually entered or API-returned).
- Candidates: normalize name before comparison — lowercase, strip titles (Dr., Prof., Ph.D., Jr., Sr., III), normalize unicode (NFD → NFC), collapse whitespace. Compare `normalized_name` using trigram similarity (`pg_trgm`) with threshold 0.7 instead of Levenshtein. Also check existing `thinkers.name` to prevent candidates that already exist.

### 5.6 Refresh Scheduling

A dedicated `refresh_due_sources` job runs every hour:

```sql
SELECT * FROM sources
WHERE active = true
  AND approval_status = 'approved'
  AND last_fetched + (refresh_interval_hours * INTERVAL '1 hour') < NOW()
```

**Backfill vs incremental:**
- First run: fetches all items within `approved_backfill_days`. Sets `backfill_complete = true` when done.
- All subsequent runs: only fetches items published after `last_fetched`. History is never re-scanned.
- This means each historical episode is processed exactly once, ever.

| Tier | Refresh Interval |
|---|---|
| Tier 1 | Every 6 hours |
| Tier 2 | Every 24 hours |
| Tier 3 | Every 168 hours (weekly) |

### 5.7 Content Filtering
Not all discovered content is worth transcribing. Workers apply filters before inserting content or enqueuing transcription:

**Duration filter:**
Episodes with `duration_seconds < min_duration_seconds` (default 600 = 10 min) are inserted with `status = 'skipped'`. This filters ads, trailers, promos, and short announcements that waste GPU time and pollute the corpus.

**Title pattern filter:**
Episodes whose title contains any substring from `skip_title_patterns` (case-insensitive) are inserted with `status = 'skipped'`. Default patterns: `trailer`, `teaser`, `best of`, `rerun`, `rebroadcast`, `ad break`, `bonus:`, `announcement`.

**Per-source overrides:**
The `sources.config` JSONB field can override global filters for specific sources:
```json
{
  "min_duration_override": 300,
  "skip_title_patterns_override": ["trailer"],
  "additional_skip_patterns": ["q&a mailbag"]
}
```

Skipped content retains its row in the database (for auditing and to prevent re-discovery on the next refresh) but never enters the transcription queue.

### 5.8 Backpressure
When transcription cannot keep up with discovery, the system applies automatic backpressure to prevent unbounded queue growth:

**Mechanism:**
- Every time a CPU worker claims a discovery or fetch job, it checks the `process_content` pending queue depth.
- If depth > `max_pending_transcriptions` (default 500), the worker **demotes** the current job's effective priority by +3 before executing.
- This means discovery jobs naturally slow down as the transcription backlog grows — fetch jobs run less frequently, giving GPU workers time to drain the queue.
- When queue depth drops below 80% of the threshold (400), normal priority resumes.

**Why priority demotion instead of pausing:**
Pausing discovery entirely risks missing time-sensitive content (new episodes published during the pause window). Demotion slows discovery proportionally without halting it. Critical jobs (LLM approvals, health checks) at priority 1 are unaffected.

The daily digest includes backpressure status: whether it was triggered, for how long, and current queue depth trend.

---

## 6. Job Types & Worker Handlers

| Job Type | Worker | Needs LLM Approval | Priority |
|---|---|---|---|
| `discover_thinker` | CPU | **Yes** — first run only. Subsequent refreshes autonomous. | 1 |
| `refresh_due_sources` | CPU | No | 1 |
| `reclaim_stale_jobs` | CPU | No | 1 | |
| `manage_gpu_scaling` | CPU | No | 1 | |
| `fetch_podcast_feed` | CPU | No | 2 |
| `scrape_substack` | CPU | No | 2 |
| `fetch_youtube_channel` | CPU | No | 2 |
| `process_content` | **GPU** | No | 3 |
| `tag_content_thinkers` | CPU | No | 3 | |
| `fetch_guest_feed` | CPU | No | 4 |
| `discover_guests_listennotes` | CPU | No | 5 |
| `discover_guests_podcastindex` | CPU | No | 5 |
| `search_youtube_appearances` | CPU | No — last resort only | 6 |
| `scan_for_candidates` | CPU | No — output goes to `pending_llm` queue | 7 |
| `rollup_api_usage` | CPU | No | 7 | |
| `snapshot_metrics` | CPU | No | 8 |
| `llm_approval_check` | CPU | N/A — this IS the approval | 1 |
| `llm_health_check` | CPU | N/A — this IS the health check | 1 |

### 6.1 Approval Flow for Gated Jobs

When a gated job is created (e.g. `discover_thinker` for a new thinker):

1. Job inserted with `status = 'awaiting_llm'`
2. `llm_approval_check` job created at priority 1, referencing the pending job
3. LLM Supervisor runs, reads context, returns decision
4. Decision logged to `llm_reviews`
5. If `approved`: pending job moves to `status = 'pending'`, worker claims it normally
6. If `approved_with_modifications`: parameters updated on thinker/source record, then job moves to `pending`
7. If `rejected`: job moves to `status = 'rejected_by_llm'`. Admin notified.
8. If `escalate_to_human`: job stays in `awaiting_llm`. Admin sees it in dashboard with LLM's reasoning.

### 6.2 Worker Configuration

| Parameter | Value |
|---|---|
| CPU workers | 4–6 concurrent, always-on |
| GPU workers | 2–4 concurrent. On-demand — see Section 6.5 for orchestration details. |
| Poll interval | 2s active, 30s max idle |
| Job timeout | 30 minutes (configurable via `stale_job_timeout_minutes`) |
| Max attempts | 3 default. `process_content`: 2. Feed fetches: 4. |
| Backoff | Exponential: 2^attempts minutes |

### 6.3 Stale Job Reclamation
Workers can crash, lose network, or hang. Without reclamation, stuck jobs block the queue indefinitely.

**`reclaim_stale_jobs`** runs every 5 minutes as an internal scheduled task on the CPU worker (not a jobs-table job — it runs in the worker's event loop directly to avoid circular dependency).

```sql
UPDATE jobs
SET status = 'retrying',
    worker_id = NULL,
    attempts = attempts + 1,
    error = 'Reclaimed: exceeded stale_job_timeout_minutes',
    error_category = 'worker_timeout',
    last_error_at = NOW(),
    scheduled_at = NOW() + (POWER(2, attempts) * INTERVAL '1 minute')
WHERE status = 'running'
  AND started_at < NOW() - (SELECT (value::int) * INTERVAL '1 minute'
                            FROM system_config WHERE key = 'stale_job_timeout_minutes')
RETURNING id, job_type, worker_id
```

Reclaimed jobs follow normal backoff. If `attempts >= max_attempts` after reclamation, the job moves to `failed`. The health check includes reclamation events in its error summary — frequent reclamation signals a systemic issue.

### 6.4 Rate Limit Coordination
Concurrent workers must cooperate to stay under external API rate limits. Coordination happens through Postgres — no Redis or in-memory state.

**Before every external API call**, the worker executes:

```sql
-- Check current usage within the sliding window
SELECT COUNT(*) FROM rate_limit_usage
WHERE api_name = $1
  AND called_at > NOW() - INTERVAL '1 hour'
```

If count >= the limit from `system_config` (e.g. `listennotes_calls_per_hour`):
1. Worker backs off for 30 seconds
2. Retries the check (up to 3 times)
3. If still at limit, the job is rescheduled with `scheduled_at = NOW() + INTERVAL '10 minutes'`

If count < limit:
1. Worker inserts a `rate_limit_usage` row (api_name, worker_id, called_at)
2. Proceeds with the API call

**Cleanup:** The `refresh_due_sources` hourly job purges `rate_limit_usage` rows older than 2 hours. The `rollup_api_usage` hourly job aggregates raw rows into `api_usage` before purge.

**Why not `SELECT FOR UPDATE`:** Rate limit checks are advisory, not transactional. A brief window where two workers both see count=99 and both proceed to 101 is acceptable — external APIs have their own enforcement. The goal is to stay comfortably under limits, not to be exactly at them.

### 6.5 GPU Worker Orchestration
The GPU worker (Railway L4) runs on-demand to minimize cost. The CPU worker manages its lifecycle via the Railway API.

**Scaling up:**
The `manage_gpu_scaling` task runs every 5 minutes in the CPU worker's event loop:

```sql
SELECT COUNT(*) FROM jobs
WHERE job_type = 'process_content'
  AND status = 'pending'
```

If count > `gpu_queue_threshold` (default 5) AND the GPU service is currently scaled to 0 replicas:
1. CPU worker calls Railway API: scale `worker-gpu` service to 1 replica
2. Logs the action to `api_usage` (Railway API call)
3. GPU worker starts, loads Parakeet model from volume cache (~2–5 min for model load into VRAM, volume persists across deploys)
4. GPU worker begins claiming `process_content` jobs

**Scaling down:**
The same `manage_gpu_scaling` task checks:

```sql
SELECT COUNT(*) FROM jobs
WHERE job_type = 'process_content'
  AND status IN ('pending', 'running')
```

If count = 0 for `gpu_idle_minutes_before_shutdown` consecutive checks (default 30 min):
1. CPU worker calls Railway API: scale `worker-gpu` service to 0 replicas
2. GPU service shuts down. Volume persists — next start only pays model-load cost, not download cost.

**Failure handling:**
- If the Railway API call to scale up fails, the task retries on next 5-minute cycle
- If the GPU worker crashes mid-transcription, the stale job reclaimer (Section 6.3) returns the job to the queue
- If the GPU service stays unhealthy for 3 consecutive scale-up attempts, the health check flags it for human review

### 6.6 Content Attribution Pipeline
The `content_thinkers` junction table is populated by the `tag_content_thinkers` job, which runs automatically after content is inserted by any fetch job.

**When it runs:**
Every `fetch_podcast_feed`, `fetch_guest_feed`, `scrape_substack`, and `fetch_youtube_channel` handler enqueues a `tag_content_thinkers` job for each batch of newly inserted content items.

**How attribution works:**

1. **Source owner** — The thinker who owns the source is tagged with `role = 'primary'`, `confidence = 10`.

2. **Title matching** — Episode title is checked against all active thinker names (exact match, case-insensitive). Matches are tagged with `role = 'guest'`, `confidence = 9`.

3. **Description matching** — Episode description is checked against thinker names. Matches are tagged with `role = 'guest'`, `confidence = 6`. Partial matches (first name + last initial, or last name only in context) get `confidence = 4`.

4. **Host extraction** — If the source is a podcast, the RSS feed's `<itunes:author>` or `<itunes:owner>` is extracted once (stored in `sources.config.host_name`) and tagged as `role = 'host'`, `confidence = 10` on all episodes from that source.

5. **Known show mapping** — For well-known shows (maintained in `sources.config.known_guests` or populated by the LLM during source approval), pre-mapped guest names are checked against episode metadata.

This is deliberately simple string matching for v1. LLM-assisted NER for ambiguous attributions is deferred to Phase 2. Low-confidence attributions (< 5) are excluded from downstream analysis by default but retained for future improvement.

---

## 7. Transcription Pipeline

Three-pass pipeline. Parakeet fires only when no text source found. Speaker diarization deferred to Month 2.

### 7.1 Pass 1 — YouTube Captions

`yt-dlp --write-auto-sub --skip-download`. Own channels only. Rejected if < 100 words.

### 7.2 Pass 2 — Existing Transcripts

Check episode page for published transcript before downloading audio. Requires per-show config in `sources.config.transcript_url_pattern`. Expanded as shows are onboarded.

### 7.3 Pass 3 — Parakeet TDT 1.1B

Audio downloaded or extracted via `yt-dlp`. Converted to 16kHz mono WAV. Model held in VRAM across jobs (~40x real-time on L4). Files > 60 min chunked into 45-min segments. Audio deleted immediately after transcription.

### 7.4 Storage

Full transcript in `content.body_text`. `transcription_method` records which pass succeeded. Audio deleted immediately.

---

## 8. LLM Supervisor

The LLM Supervisor is the governance layer of ThinkTank. It runs on two tracks: **event-driven approvals** (triggered when a job needs sign-off) and **scheduled checks** (running on a fixed clock regardless of queue state). All decisions and observations are logged to `llm_reviews`.

### 8.1 Approval Track

Triggered when a job enters `status = 'awaiting_llm'`. The supervisor runs an `llm_approval_check` job, reads the relevant context, and returns a structured decision.

**Thinker approval**

Context provided to LLM:
- Proposed thinker: name, tier, categories, sources, proposed backfill depth
- Estimated content volume based on source scan
- How they were added (manual seed vs candidate promotion)
- Current corpus stats: total thinkers, total content, queue depth

LLM decides:
- Approve as proposed
- Approve with modified parameters (e.g. reduce backfill from 365 to 90 days, restrict source types)
- Reject with reasoning
- Escalate to human (if identity is ambiguous or category fit is unclear)

**Source approval**

Context provided:
- Source URL, type, name
- Thinker it's being registered against
- Sample of episode titles/descriptions from the feed
- Estimated item count within proposed backfill window

LLM decides:
- Approve with a specific `approved_backfill_days` value
- Approve with reduced backfill
- Reject (off-topic, low quality, already covered by another source)

**Candidate promotion**

Context provided:
- Candidate name, appearance count, first/last seen dates
- Sample episode titles and descriptions where they appeared
- Inferred categories
- Auto-matched social handles if found
- Current candidate queue depth

LLM decides:
- Approve as a new thinker (specifies tier, categories, initial sources)
- Reject (not a genuine thinker, already exists under different name, insufficient signal)
- Mark as duplicate of existing thinker
- Request more appearances before deciding (sets a higher threshold)

**Error resume approval**

Triggered when a source hits 3 consecutive errors and normal backoff is exhausted.

Context provided:
- Source details, error category, error messages, timestamps
- How many content items were previously fetched successfully
- Whether other sources for the same thinker are healthy

LLM decides:
- Resume with current parameters (transient error)
- Resume with modified parameters (e.g. increase refresh interval)
- Deactivate source permanently
- Escalate to human for investigation

### 8.2 Scheduled Check Track

Four scheduled check types. All run regardless of queue state. All write to `llm_reviews`.

**Health check — every 6 hours**

Context provided:
- Jobs table summary: counts by status and job_type in the last 6 hours
- Error log: all failed jobs with error_category in the last 6 hours
- Source health: sources with error_count > 0
- Worker status: any jobs in `running` state for > 20 minutes (potential hangs)
- Queue depth by job type
- GPU worker status: online/offline, queue depth
- Stale job reclamation events in the last 6 hours- Rate limit headroom: current usage vs limits for each external API- Backpressure status: whether discovery demotion is active
LLM checks for:
- Error rate spikes on any job type (> 20% failure rate triggers alert)
- Hung workers (job running > 30 min)
- Sources with repeated failures of the same category
- Queue imbalance (e.g. transcription queue growing while discovery stalls)
- GPU worker deployed but queue already empty (cost waste)
- Frequent stale job reclamation (signals worker instability)- Rate limits approaching capacity (> 80% usage)- Any `system_config` values that appear to need adjustment

LLM outputs:
- `no_action` if system healthy
- Specific flags with recommended actions if issues detected
- Can directly modify `system_config` values within defined bounds (e.g. adjust rate limits)
- Escalates to human for anything requiring structural changes

**Daily digest — every day at 07:00 UTC**

Context provided:
- Last 24 hours: content items discovered, transcribed, failed
- New thinkers approved/rejected
- Candidates surfaced and their status
- Source health summary
- Corpus totals: thinkers, sources, content items, word count
- Top 5 most active thinkers by new content
- Any anomalies detected in the last 24 hours
- API usage summary: calls and estimated cost per API in the last 24 hours- Backpressure summary: hours active, peak queue depth
LLM outputs a structured summary stored in `llm_reviews.llm_response` and surfaced in the admin dashboard as the day's digest. Human-readable, concise. Flags anything needing attention.

**Quota check — triggered when daily limits are approached**

Triggered when `max_candidates_per_day` or `max_new_sources_per_day` are within 20% of their limits.

Context provided:
- Candidates or sources pending approval
- Current queue state
- How much of the daily quota has been used

LLM decides:
- Raise the limit for today (if quality is high and system is healthy)
- Hold at current limit (review the queue, don't expand)
- Pause cascade discovery entirely until tomorrow

**Weekly audit — every Monday at 07:00 UTC**

Context provided:
- Full week summary: all content ingested, all LLM decisions made
- Thinkers with zero new content in 7 days
- Sources with consistently high error rates
- Candidate queue backlog
- Corpus growth rate vs prior weeks
- Cost estimate: API calls consumed, GPU hours used, total estimated spend
LLM outputs:
- Recommendations for thinkers to deactivate (no content, no active sources)
- Sources to retire (persistent errors, no successful fetches in 7 days)
- Assessment of whether `system_config` quota limits are appropriately tuned
- Any structural observations for human review

### 8.3 LLM Supervisor Prompt Design

Every prompt follows the same structure:

```
SYSTEM:
You are the ThinkTank Supervisor, responsible for governing a content ingestion
pipeline. Your job is to make careful, conservative decisions that keep the system
focused, efficient, and under control. You prefer approving less over approving more.
When uncertain, escalate to human review rather than guessing.

Always respond in valid JSON matching the schema for this review_type. Never include
prose outside the JSON structure.

CONTEXT:
{serialised context snapshot as JSON}

TASK:
{specific decision or check type with expected output schema}
```

All LLM responses are parsed as JSON. If parsing fails, the job is marked `escalate_to_human` automatically and the raw response is logged.

### 8.4 LLM Supervisor Constraints

The LLM Supervisor can:
- Approve, reject, or modify parameters for pending jobs
- Update `system_config` values within pre-defined bounds
- Flag items for human review
- Deactivate sources or thinkers

The LLM Supervisor cannot:
- Permanently delete any data
- Approve thinkers without at least one verified source
- Set `approved_backfill_days` higher than the human-defined maximum in `system_config`
- Disable admin override capability

### 8.5 Human Override

Any LLM decision can be overridden by an admin in the dashboard. The override is logged with the admin username, timestamp, and reasoning in `llm_reviews` (fields: `overridden_by`, `overridden_at`, `override_reasoning`). The LLM Supervisor is notified of overrides in the next health check context so it can learn from the pattern.

### 8.6 Availability & Fallback
The LLM Supervisor depends on the Anthropic API. When the API is unavailable, the system must degrade gracefully rather than stall.

**Timeout escalation:**
Jobs in `awaiting_llm` status are checked every 15 minutes by the CPU worker:

```sql
SELECT * FROM jobs
WHERE status = 'awaiting_llm'
  AND created_at < NOW() - (SELECT (value::int) * INTERVAL '1 hour'
                            FROM system_config WHERE key = 'llm_timeout_hours')
```

Jobs exceeding `llm_timeout_hours` (default 2 hours) are automatically escalated:
- Job status changes to `awaiting_llm` (unchanged) but a `needs_human_review` flag is set in the payload
- The admin dashboard highlights these jobs prominently with a banner: **"LLM unavailable — N jobs awaiting human review"**
- The admin can approve or reject directly, bypassing the LLM entirely

**API failure handling:**
When an `llm_approval_check` or `llm_health_check` job fails (API error, timeout, rate limit):
1. Normal retry with exponential backoff (up to `max_attempts`)
2. After all retries exhausted: the referenced job is flagged for human review (not failed — the underlying work is still valid)
3. A `llm_reviews` row is created with `decision = 'escalate_to_human'` and `decision_reasoning = 'LLM API unavailable after N attempts'`

**Scheduled checks during outage:**
If a scheduled health check or daily digest fails due to API unavailability:
- It is rescheduled for 1 hour later
- After 3 consecutive failures, the admin dashboard shows a persistent warning: **"LLM Supervisor offline since {timestamp}"**
- Workers continue operating autonomously on already-approved thinkers and sources — no new approvals, but existing pipeline is unaffected

**Recovery:**
When the API becomes available again, the next successful health check clears the warning. Any accumulated `awaiting_llm` jobs that haven't been human-reviewed are processed in FIFO order. The recovery health check includes a summary of the outage period in its context.

---

## 9. Admin Dashboard

### 9.1 Pipeline Control

- Global kill switch (writes `workers_active = false` to `system_config`)
- Adjust CPU worker concurrency
- Pause `process_content` jobs (GPU cost control)
- Trigger manual `discover_thinker` for any approved thinker
- Override any LLM decision with logged reasoning
- Add/edit thinkers and sources (submitted to LLM approval queue)

### 9.2 Live Observability

- **Queue depth by job type** — bar chart, 10-second HTMX refresh
- **Active jobs** — job type, thinker, duration, worker ID
- **GPU status** — online/offline, current replica count, queue depth, estimated cost this month- **Throughput** — items discovered/hr, transcriptions/hr
- **Error log** — failed jobs with `error_category`, thinker, message, retry time
- **Source health** — sources with error_count > 0 highlighted, `backfill_complete` status visible
- **Rate limit gauges** — Listen Notes, YouTube, Podcast Index, Anthropic: calls/hr vs `system_config` limits, with color coding (green < 60%, yellow 60–80%, red > 80%)- **API cost tracker** — estimated spend per API for current billing period, from `api_usage` table- **LLM Supervisor status** — online/offline indicator, last successful check timestamp, outage banner if applicable- **Backpressure indicator** — whether discovery demotion is active, current `process_content` queue depth vs threshold- **Stale job reclamation log** — jobs reclaimed in last 24 hours, grouped by worker_id (frequent reclamation per worker signals instability)
### 9.3 LLM Supervisor Panel

- **Latest digest** — most recent daily digest displayed in full
- **Pending approvals** — jobs in `awaiting_llm` state with LLM's decision and reasoning. Jobs exceeding `llm_timeout_hours` highlighted with human-review prompt.- **Recent decisions** — last 20 `llm_reviews` entries with decision type and outcome
- **Override log** — all human overrides with timestamps and reasoning
- **Health check history** — last 7 days of health checks, any flags raised

### 9.4 Content Intelligence

- **Thinker status table** — sources, content discovered, transcribed, pending, errored per thinker
- **Multi-thinker content view** — episodes in `content_thinkers` with multiple roles, showing confidence scores- **Category coverage map** — thinker density per category
- **Candidate queue** — `pending_llm` candidates with LLM assessment visible, human approve/reject available
- **Corpus growth chart** — total items and word count over time
- **Content dedup log** — fingerprint matches caught in last 7 days, showing what would have been duplicated
---

## 10. Deployment

### 10.1 Railway Services

| Service | Configuration |
|---|---|
| `postgres` | Railway managed PostgreSQL. Auto-backups. |
| `api` | Standard. CMD: `uvicorn src.api.main:app`. Runs Alembic migrations on startup. |
| `worker-cpu` | Standard. CMD: `python -m scripts.run_worker --mode cpu --workers 6`. Always on. |
| `worker-gpu` | GPU (L4). CMD: `python -m scripts.run_worker --mode gpu --workers 4`. On-demand — managed by CPU worker via Railway API. |
| `admin` | Standard. CMD: `uvicorn src.admin.main:app`. Private networking only. |

### 10.2 Environment Variables

| Variable | Description |
|---|---|
| `DATABASE_URL` | Railway auto-injected |
| `ANTHROPIC_API_KEY` | LLM Supervisor — Claude API |
| `LISTENNOTES_API_KEY` | Primary podcast discovery |
| `PODCASTINDEX_API_KEY` | Podcast feed search |
| `PODCASTINDEX_API_SECRET` | Podcast Index auth |
| `YOUTUBE_API_KEY` | YouTube Data API v3 |
| `TWITTER_BEARER_TOKEN` | X/Twitter metrics |
| `RAILWAY_API_KEY` | Allows CPU worker to manage GPU service scaling |
| `RAILWAY_GPU_SERVICE_ID` | Service ID for the GPU worker. Used by `manage_gpu_scaling` to call Railway API. |
| `NEMO_CACHE_DIR` | Parakeet model cache path |
| `AUDIO_TMP_DIR` | Temporary audio storage |

### 10.3 Bootstrap Deployment Sequence

1. Create Railway project, add PostgreSQL
2. Deploy `api` service — runs `alembic upgrade head` on startup, applying full schema3. Seed category taxonomy: `python -m scripts.seed_categories`
4. Seed system config defaults: `python -m scripts.seed_config`
5. Seed initial thinkers (all land as `approval_status = 'pending_llm'`): `python -m scripts.seed_thinkers`
6. Run first LLM batch review: `python -m scripts.run_initial_llm_review` — processes all pending thinkers, sets `workers_active = true` on completion
7. Deploy `worker-cpu` — begins processing approved jobs immediately. Starts `reclaim_stale_jobs` and `manage_gpu_scaling` internal loops.8. GPU worker is **not deployed manually** — the CPU worker's `manage_gpu_scaling` task will deploy it automatically when `process_content` queue exceeds threshold9. Deploy `admin` service
10. Verify: admin dashboard shows approved thinkers, queue filling with discovery jobs, rate limit gauges active, first health check scheduled
### 10.4 Cost Estimate

| Item | Est. Monthly Cost |
|---|---|
| Railway PostgreSQL | ~$20/mo |
| Railway API service | ~$5/mo |
| Railway CPU worker | ~$15/mo |
| Railway GPU (on-demand) | ~$100–200/mo |
| Railway Admin service | ~$5/mo |
| Railway Volume | ~$5/mo |
| Listen Notes (paid) | $50/mo if free tier exceeded |
| X/Twitter API | <$10/mo |
| Anthropic Claude API (supervisor) | ~$10–30/mo |
| **Total** | **~$220–340/mo fully operational** |

---

## Phase 2 — Explicitly Deferred

- Speaker diarization (NeMo) — Month 2
- S3 transcript offload — only if Postgres storage pressure becomes real
- Knowledge graph / claim extraction
- pgvector embeddings and semantic search
- Accuracy scoring
- Advanced blog and paper scraping
- Third-party YouTube transcript services
- LLM-assisted content attribution (NER for ambiguous guest detection)
*End of Specification*
