# THINKTANK
## Global Intelligence Ingestion Platform — Full System Specification
*Version 1.0 · March 2026*

---

ThinkTank is a continuous ingestion and storage engine that discovers, fetches, and transcribes public content from the world's leading thinkers — podcasts, YouTube videos, Substacks, papers, and blogs — into a structured relational database. The corpus is designed to be the foundation for downstream intelligence extraction, semantic search, claim analysis, and global trend synthesis.

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Technology Stack](#2-technology-stack)
3. [Database Schema](#3-database-schema)
4. [Discovery & Scraping](#4-discovery--scraping)
5. [Job Types & Worker Handlers](#5-job-types--worker-handlers)
6. [Transcription Pipeline](#6-transcription-pipeline)
7. [Admin Dashboard](#7-admin-dashboard)
8. [Thinker Intelligence & Scoring](#8-thinker-intelligence--scoring)
9. [Deployment](#9-deployment)

---

## 1. System Overview

ThinkTank operates as two Railway services sharing one PostgreSQL database. The API service exposes management endpoints. The Worker service runs all discovery, scraping, and transcription jobs from a database-backed queue. There is no Redis, no separate message broker, no additional infrastructure beyond the database and the two services.

### 1.1 Core Design Principles

- **Everything is a job.** Every unit of work — discovering a feed, fetching episodes, transcribing audio — is a row in the jobs table. Workers claim and process jobs. The system is fully resumable after a crash.
- **Discovery compounds.** Every podcast show we touch becomes a source of candidate thinkers. The system surfaces unfamiliar names appearing repeatedly across episodes and flags them for review, growing the corpus organically.
- **Text first, audio last.** YouTube captions are fetched before any audio is downloaded. Existing transcripts from show websites are checked before Parakeet runs. The GPU fires only when no text source exists.
- **One database, full visibility.** The job queue, content, metrics, and candidate pipeline all live in Postgres. The admin dashboard reads from the same database — no separate monitoring stack.
- **Schema designed to scale** to thousands of thinkers and dozens of source types without structural changes.

### 1.2 Service Architecture

| Service | Description |
|---|---|
| **API** | FastAPI. Management endpoints: add thinkers, trigger refreshes, approve candidates, view stats. Standard Railway service. |
| **Worker** | Python async worker. Polls job queue, runs scrapers and transcription. Deployed on Railway GPU instance (NVIDIA L4) for Parakeet. |
| **Database** | Railway managed PostgreSQL. Stores all content, job queue, metrics, and admin state. |
| **Admin UI** | Separate Railway service. HTMX + FastAPI. Real-time dashboard for pipeline control and observability. Reads from same DB. |

---

## 2. Technology Stack

### 2.1 Infrastructure

| Component | Choice / Rationale |
|---|---|
| Cloud platform | Railway — single platform for all services, managed Postgres, GPU instances, volumes, environment management. |
| Database | PostgreSQL 16 (Railway managed) — job queue, content storage, metrics, admin state. No Redis needed. |
| GPU instance | Railway L4 (24GB VRAM) — runs Parakeet. Worker service only. Transcription concurrency: 4 parallel jobs. |
| Persistent volume | Railway volume mounted at `/app/.nemo_cache` — prevents 4GB Parakeet model re-downloading on every deploy. |
| Container base | `nvcr.io/nvidia/nemo:24.05` — CUDA, PyTorch, NeMo pre-installed. Only app deps added on top. |

### 2.2 Backend

| Component | Choice / Rationale |
|---|---|
| API framework | FastAPI — async, typed, auto-docs. Shared codebase with worker. |
| DB driver | asyncpg — native async PostgreSQL, fast connection pooling. |
| HTTP client | httpx — async, streaming downloads, connection pooling. |
| RSS parsing | feedparser — battle-tested, handles malformed feeds gracefully. |
| Audio extraction | yt-dlp — YouTube audio + captions. Handles anti-bot measures, regularly updated. |
| Transcription | Parakeet TDT 1.1B (NVIDIA NeMo) — state of the art ASR, runs on L4, ~40x real-time. |
| Audio processing | ffmpeg — format conversion and chunking. soundfile — WAV I/O for Parakeet. |

### 2.3 Admin UI

| Component | Choice / Rationale |
|---|---|
| Framework | FastAPI + Jinja2 templates — minimal footprint, same language as backend. |
| Frontend | HTMX — real-time updates via server-sent events and polling. No React build step. |
| Charts | Chart.js via CDN — throughput and queue depth visualisations. |
| Auth | HTTP Basic Auth or Railway private networking — internal tool only. |

### 2.4 External APIs

| API | Usage / Cost |
|---|---|
| Listen Notes | Podcast guest appearance search. Free tier: 10k requests/month. Paid: $50/mo for 1M. |
| Podcast Index | Podcast feed discovery. Completely free. Good complement to Listen Notes. |
| YouTube Data API v3 | Channel videos + appearance search. Free: 10,000 units/day. Sufficient for current scale. |
| ~~SerpAPI~~ | Removed. Listen Notes + Podcast Index cover discovery adequately without the $50/mo cost. |
| ~~OpenAI Whisper~~ | Removed. Replaced by local Parakeet. Zero per-minute transcription cost. |

---

## 3. Database Schema

Nine tables. Four core domain tables, two lookup/junction tables, one job queue, one candidate pipeline table, and one metrics snapshot table. All IDs are UUIDs. All timestamps are TIMESTAMPTZ.

### 3.1 `categories`

Hierarchical taxonomy of knowledge domains. Supports unlimited depth via `parent_id` self-reference. Allows querying at any granularity — `artificial_intelligence` returns all AI sub-categories.

| Column | Type | Description |
|---|---|---|
| `id` | UUID PK | |
| `slug` | TEXT UNIQUE | e.g. `ai_models`, `macro_economics` |
| `name` | TEXT | Display name |
| `parent_id` | UUID FK → categories | Null for top-level categories |
| `description` | TEXT | What belongs in this category |
| `created_at` | TIMESTAMPTZ | |

### 3.2 `thinkers`

One row per person. Handles and profile links stored here. Scoring and metrics are in separate tables to allow versioning over time.

| Column | Type | Description |
|---|---|---|
| `id` | UUID PK | |
| `name` | TEXT | Full name |
| `slug` | TEXT UNIQUE | URL-safe, e.g. `andrej-karpathy` |
| `tier` | SMALLINT | 1 = top tier, 2 = notable, 3 = emerging. Controls refresh frequency. |
| `bio` | TEXT | Short biography |
| `primary_affiliation` | TEXT | e.g. `OpenAI`, `MIT`, `Independent` |
| `twitter_handle` | TEXT | |
| `wikipedia_url` | TEXT | |
| `personal_site` | TEXT | |
| `active` | BOOLEAN | False = stop refreshing |
| `added_at` | TIMESTAMPTZ | |
| `last_refreshed` | TIMESTAMPTZ | Last time discovery ran |

### 3.3 `thinker_categories` (junction)

Many-to-many between thinkers and categories. A thinker can belong to multiple categories, each with an independent relevance score that informs weighting during analysis.

| Column | Type | Description |
|---|---|---|
| `thinker_id` | UUID FK → thinkers | |
| `category_id` | UUID FK → categories | |
| `relevance` | SMALLINT 1–10 | How central this category is to this thinker |
| `added_at` | TIMESTAMPTZ | |

### 3.4 `thinker_profiles`

Static biographical and credential data. JSONB fields for structured but variable data like education history and positions held. Updated manually or via enrichment jobs.

| Column | Type | Description |
|---|---|---|
| `id` | UUID PK | |
| `thinker_id` | UUID FK → thinkers | One-to-one |
| `education` | JSONB | `[{school, degree, field, year}]` |
| `positions_held` | JSONB | `[{title, organisation, from_year, to_year}]` |
| `notable_works` | JSONB | `[{type, title, year, url}]` — books, papers, companies, products |
| `awards` | JSONB | `[{name, org, year}]` |
| `updated_at` | TIMESTAMPTZ | |

### 3.5 `thinker_metrics`

Social footprint snapshots. New row on each refresh — keeps full history. Allows tracking follower growth over time and computing reach scores per platform per thinker.

| Column | Type | Description |
|---|---|---|
| `id` | UUID PK | |
| `thinker_id` | UUID FK → thinkers | |
| `platform` | TEXT | `youtube`, `twitter`, `instagram`, `linkedin`, `substack`, `podcast` |
| `handle` | TEXT | Platform-specific handle or ID |
| `followers` | BIGINT | |
| `avg_views` | BIGINT | Average views/listens per post where available |
| `post_count` | INT | Total posts/videos/episodes |
| `verified` | BOOLEAN | |
| `snapshotted_at` | TIMESTAMPTZ | When this row was captured |

### 3.6 `sources`

One row per content source — a YouTube channel, a podcast RSS feed, a Substack, a blog. Decoupled from individual content items. Stores discovery metadata and refresh scheduling. A thinker can have multiple sources of the same type (e.g. two podcasts).

| Column | Type | Description |
|---|---|---|
| `id` | UUID PK | |
| `thinker_id` | UUID FK → thinkers | |
| `source_type` | TEXT | `podcast_rss`, `youtube_channel`, `substack`, `blog_rss`, `arxiv`, `twitter` |
| `name` | TEXT | Human-readable name, e.g. `Lex Fridman Podcast` |
| `url` | TEXT UNIQUE | Canonical feed or channel URL |
| `external_id` | TEXT | YouTube channel ID, podcast GUID, etc. |
| `initial_backfill_days` | INT | How far back to go on first discovery. Default 365. |
| `backfill_complete` | BOOLEAN | True once historical fetch is done — prevents re-scanning history on refresh |
| `refresh_interval_hours` | INT | How often to check for new content. Tier 1 = 6, Tier 2 = 24, Tier 3 = 168 |
| `last_fetched` | TIMESTAMPTZ | |
| `item_count` | INT | Total items discovered from this source |
| `active` | BOOLEAN | False = stop fetching |
| `error_count` | INT | Consecutive fetch errors — triggers alert at 3 |
| `created_at` | TIMESTAMPTZ | |

### 3.7 `content`

One row per piece of content — a podcast episode, YouTube video, Substack post, blog article, or paper. All source types converge here. `body_text` holds the final text: transcript for audio/video, article text for written sources. Status tracks the pipeline stage.

| Column | Type | Description |
|---|---|---|
| `id` | UUID PK | |
| `source_id` | UUID FK → sources | Which source produced this |
| `thinker_id` | UUID FK → thinkers | Denormalised for query convenience |
| `content_type` | TEXT | `episode`, `video`, `article`, `paper`, `post` |
| `url` | TEXT UNIQUE | Canonical URL — deduplication key |
| `title` | TEXT | |
| `body_text` | TEXT | Final text — transcript or article body |
| `word_count` | INT | |
| `published_at` | TIMESTAMPTZ | Original publication date |
| `duration_seconds` | INT | Audio/video only. Null for text sources. |
| `show_name` | TEXT | For guest appearances: the show name |
| `host_name` | TEXT | For guest appearances: host name(s) |
| `thumbnail_url` | TEXT | |
| `transcription_method` | TEXT | `youtube_captions`, `parakeet`, `existing_transcript`, null for text sources |
| `status` | TEXT | `pending` \| `processing` \| `done` \| `error` \| `skipped` |
| `error_message` | TEXT | Last error if status = error |
| `discovered_at` | TIMESTAMPTZ | |
| `processed_at` | TIMESTAMPTZ | When body_text was populated |

### 3.8 `candidate_thinkers`

The growth pipeline. When scrapers encounter names appearing frequently across episodes they have not seen before, those names land here for human review. Approved candidates are automatically added to the thinkers table and queued for full discovery.

| Column | Type | Description |
|---|---|---|
| `id` | UUID PK | |
| `name` | TEXT | Name as it appeared in content |
| `appearance_count` | INT | Number of episodes/articles they appeared in |
| `first_seen_at` | TIMESTAMPTZ | |
| `last_seen_at` | TIMESTAMPTZ | |
| `sample_urls` | TEXT[] | Up to 5 content URLs where they appeared |
| `inferred_categories` | TEXT[] | Categories guessed from context |
| `suggested_twitter` | TEXT | Auto-matched handle if found |
| `suggested_youtube` | TEXT | Auto-matched channel if found |
| `status` | TEXT | `pending` \| `approved` \| `rejected` \| `duplicate` |
| `reviewed_by` | TEXT | Admin username |
| `reviewed_at` | TIMESTAMPTZ | |
| `thinker_id` | UUID FK → thinkers | Populated on approval |

### 3.9 `jobs`

DB-backed job queue. No Redis or external broker needed. Workers atomically claim jobs using `SELECT FOR UPDATE SKIP LOCKED`. Exponential backoff on failures. Full history retained for observability.

| Column | Type | Description |
|---|---|---|
| `id` | UUID PK | |
| `job_type` | TEXT | See Section 5 for full list |
| `payload` | JSONB | Input data for the handler |
| `status` | TEXT | `pending` \| `running` \| `done` \| `failed` \| `retrying` |
| `priority` | SMALLINT 1–10 | 1 = highest. Tier 1 discovery = 1, transcription = 3, guest feeds = 5 |
| `attempts` | SMALLINT | |
| `max_attempts` | SMALLINT | Default 3 |
| `error` | TEXT | Last error message |
| `worker_id` | TEXT | Which worker instance claimed this job |
| `scheduled_at` | TIMESTAMPTZ | Allows delayed/backoff scheduling |
| `started_at` | TIMESTAMPTZ | |
| `completed_at` | TIMESTAMPTZ | |
| `created_at` | TIMESTAMPTZ | |

---

## 4. Discovery & Scraping

### 4.1 How a Thinker Enters the System

Two paths: manual addition via the API (for seed thinkers), or approval of a candidate flagged by the scrapers. Either path results in a thinker row and a `discover_thinker` job queued at priority 1.

### 4.2 Per-Thinker Discovery Sequence

When `discover_thinker` runs, it fans out into parallel jobs for each content type:

- **Own podcast feeds** — fetch each registered RSS feed in sources table
- **Substack** — fetch RSS, extract free post body text directly from feed content
- **YouTube channel** — fetch all videos from channel via YouTube Data API
- **YouTube appearances** — search YouTube for `[name] interview`, `[name] podcast` filtered to past year
- **Podcast guest search** — query Listen Notes for `[name]`, `[name] interview`
- **Podcast Index search** — search by person name across podcast feeds
- **Metrics refresh** — snapshot current follower counts from YouTube API and Twitter API

### 4.3 Cascade Discovery — How the System Grows

This is the mechanism that makes the corpus compound over time:

- Every podcast show encountered during guest discovery is logged as a candidate source
- When a show is fetched, all episode titles and descriptions are scanned for known thinker names
- Names appearing in 3 or more episodes but not in the thinkers table are logged in `candidate_thinkers`
- The admin reviews candidates and approves them — triggering full discovery automatically
- After 6 months of operation, the corpus grows primarily via cascade rather than manual addition

### 4.4 Source Type Details

| Source Type | Discovery Method / Notes |
|---|---|
| **Podcast (own)** | RSS feed. Enclosure tag for audio URL. `itunes:duration` for length. Fetch all episodes within backfill window on first run, then only new items on refresh. |
| **Podcast (guest)** | Listen Notes API + Podcast Index. Fuzzy name match in episode title and description. Guest feeds fetched as RSS and filtered for name mentions. |
| **YouTube (channel)** | YouTube Data API v3 `/search` endpoint. Fetches all videos published after cutoff. Batch `/videos` endpoint for duration details. |
| **YouTube (guest)** | YouTube Data API search for `[name] interview`, `[name] podcast`. Filter: `videoDuration=long` (>20 min). Verify name appears in title or description. |
| **Substack** | RSS feed at `{slug}.substack.com/feed`. Full article body included for free posts in RSS content field. Paywalled posts stored with empty body_text and a paywalled flag. |
| **Blog/Personal site** | RSS feed if available. Otherwise periodic HTML fetch with article extraction (phase 2). |
| **arXiv / papers** | Semantic Scholar API search by author name. Phase 2. |

### 4.5 Deduplication

- **Content:** unique on `(thinker_id, url)`. `ON CONFLICT DO NOTHING` on insert.
- **Sources:** unique on `url`. Prevents same podcast feed being registered twice.
- **Candidate thinkers:** fuzzy name match before inserting — normalise to lowercase, strip titles (Dr., Prof.), check Levenshtein distance < 2.

### 4.6 Refresh Scheduling

Refresh frequency is determined by thinker tier, applied to `refresh_interval_hours` on each source:

| Tier | Refresh Interval |
|---|---|
| Tier 1 — Top | Every 6 hours |
| Tier 2 — Notable | Every 24 hours |
| Tier 3 — Emerging | Every 168 hours (weekly) |

A scheduled job (`refresh_due_sources`) runs every hour, queries sources where `last_fetched + refresh_interval_hours < NOW()`, and enqueues fetch jobs. Backfill-complete sources only check for items newer than `last_fetched` — no re-scanning of history.

---

## 5. Job Types & Worker Handlers

All work flows through the jobs table. Each job type maps to a handler function in the worker. Workers claim jobs atomically and release them with status `done` or `retrying`.

| Job Type | Description / Priority |
|---|---|
| `discover_thinker` | Fan-out all discovery jobs for a thinker. Runs on add or manual refresh. Priority 1. |
| `fetch_podcast_feed` | Parse an RSS feed, insert new episodes, enqueue transcription. Priority 2. |
| `fetch_guest_feed` | Fetch a podcast feed known to have guest appearances. Filter by thinker name. Priority 4. |
| `discover_guests_listennotes` | Search Listen Notes for thinker name. Priority 5. |
| `discover_guests_podcastindex` | Search Podcast Index for thinker name. Priority 5. |
| `search_youtube_appearances` | Search YouTube for thinker guest appearances. Priority 5. |
| `fetch_youtube_channel` | Fetch all recent videos from a thinker's own YouTube channel. Priority 2. |
| `scrape_substack` | Fetch Substack RSS, insert posts with body text. Priority 2. |
| `transcribe_content` | Transcription pipeline: YT captions → Parakeet. Priority 3. |
| `refresh_due_sources` | Hourly scheduler. Finds sources past their refresh interval and enqueues fetch jobs. Priority 1. |
| `snapshot_metrics` | Pull follower counts from YouTube and Twitter APIs for a thinker. Priority 8. |
| `scan_for_candidates` | After a podcast feed is processed, scan episode descriptions for unknown names. Priority 7. |

### 5.1 Worker Configuration

| Parameter | Value / Rationale |
|---|---|
| Total workers | 6 concurrent. Railway L4 has 24GB VRAM — Parakeet uses ~4GB, so 4–5 concurrent transcriptions are safe. |
| Transcription workers | 2 dedicated. Other 4 handle discovery and scraping. Prevents transcription from starving the queue. |
| Poll interval | 2 seconds when active. Backs off to 30 seconds max when queue is empty. |
| Job timeout | 30 minutes. Jobs running longer are released back to the queue and retried. |
| Max attempts | 3 for most jobs. Transcription: 2 (expensive). Feed fetches: 4 (transient errors common). |
| Backoff | Exponential: 2^attempts minutes. Failure 1: 2 min, Failure 2: 4 min, Failure 3: 8 min. |

---

## 6. Transcription Pipeline

Three-pass pipeline. Each pass is attempted in order. The cheapest and fastest option is always tried first. Parakeet only runs if no text source is found.

### 6.1 Pass 1 — YouTube Captions

`yt-dlp` fetches auto-generated or manual captions using `--write-auto-sub --skip-download`. Covers approximately 80% of YouTube content at zero cost and near-instant speed. VTT files are parsed and deduplicated (auto-captions repeat lines heavily). Rejected if output is fewer than 100 words.

### 6.2 Pass 2 — Existing Transcripts

Before downloading audio, the scraper checks the show's website and episode page for a published transcript link. Many major shows (Lex Fridman, Tim Ferriss, etc.) publish full transcripts. These are fetched as HTML and cleaned to plain text. Phase 2 feature — requires per-show configuration.

### 6.3 Pass 3 — Parakeet TDT 1.1B

Fires only when no text source is available. Audio is downloaded (podcast) or extracted via `yt-dlp` (YouTube). Converted to 16kHz mono WAV with ffmpeg. Parakeet model is loaded once at worker startup and held in VRAM — subsequent transcriptions pay only inference cost (~40x real-time on L4). Files over 60 minutes are chunked into 45-minute segments to keep GPU memory bounded. Segments are concatenated into a single `body_text`.

### 6.4 Storage

Transcript text stored directly in `content.body_text`. `transcription_method` field records which pass succeeded. `word_count` stored for quick corpus statistics. Audio files deleted immediately after transcription — only text is persisted.

---

## 7. Admin Dashboard

Separate Railway service, same repository. FastAPI + Jinja2 + HTMX. Reads from the same PostgreSQL database. Accessible only via Railway private networking — not public-facing. Three sections.

### 7.1 Pipeline Control

- Start / pause / resume all workers globally
- Adjust worker concurrency on the fly (writes to a config table, workers poll it)
- Pause transcription jobs independently — useful during testing or cost management
- Trigger manual `discover_thinker` for any thinker
- Set per-thinker backfill depth and refresh interval overrides
- Manually add a thinker (form that calls the API)
- Add a source to an existing thinker (podcast feed URL, YouTube channel, Substack)

### 7.2 Live Observability

- **Queue depth by job type** — bar chart, updates every 10 seconds via HTMX polling
- **Active jobs table** — job type, thinker name, duration running, worker ID
- **Throughput metrics** — content items discovered per hour, transcriptions completed per hour
- **GPU utilisation** — Parakeet active / idle indicator
- **Error log** — failed jobs with type, thinker, error message, attempt count, retry time
- **Source health table** — sources with consecutive errors highlighted in red

### 7.3 Content Intelligence

- **Thinker status table** — for each thinker: sources registered, content discovered, transcribed, pending, errored
- **Category coverage map** — which categories have strong thinker coverage vs gaps
- **Candidate thinker review queue** — name, appearance count, sample episodes, inferred categories, approve / reject buttons
- **Corpus growth chart** — total content items and word count over time
- **Per-thinker deep view** — all sources, all content, processing status, metrics history

### 7.4 Candidate Approval Flow

When a candidate is approved in the admin UI, a single API call creates the thinker row, pre-fills categories from `inferred_categories`, links back the `candidate_thinkers` row, and enqueues `discover_thinker` at priority 1. The new thinker appears in the pipeline within seconds of approval.

---

## 8. Thinker Intelligence & Scoring

Scoring is designed to be populated incrementally. The schema is in place from day one. The actual computation starts thin and deepens as the corpus grows. No scoring logic blocks the ingestion pipeline.

### 8.1 Reach Score (0–100)

Computed from `thinker_metrics` snapshots. Normalised follower counts across platforms, weighted by platform relevance per category. A YouTube following counts more for `ai_education`; a Twitter following more for `macro`. Updated each time metrics are snapshotted.

### 8.2 Credential Score (0–100)

Computed from `thinker_profiles`. Weighted sum of: papers published, books, companies founded, positions held at recognised institutions, awards. Weights are configurable per category. Updated when profile data changes.

### 8.3 Accuracy Score (0–100)

The most valuable score and the last to be populated. Requires claim extraction (phase 2) and verdict tagging. Starts null. Fills in as claims are extracted from the corpus and verdicts assigned — manually at first, eventually via automated fact-checking. Aggregated to a `thinker_scores` table from individual claim verdicts.

### 8.4 Composite Score

Configurable weighted formula per category:

```
composite = (reach × 0.3) + (credential × 0.4) + (accuracy × 0.3)
```

Weights are adjustable per category or globally. The accuracy weight increases as more verdicts accumulate. Composite score determines thinker ranking in downstream analysis queries.

---

## 9. Deployment

### 9.1 Railway Services

| Service | Configuration |
|---|---|
| `postgres` | Railway managed PostgreSQL. Auto-backups enabled. Connection string injected as `DATABASE_URL`. |
| `api` | Standard Railway service. Dockerfile CMD: `uvicorn src.api.main:app`. No GPU needed. |
| `worker` | Railway GPU service (L4). CMD override: `python -m scripts.run_worker --workers 6`. Volume mounted at `/app/.nemo_cache`. |
| `admin` | Standard Railway service. CMD: `uvicorn src.admin.main:app`. Private networking only. |

### 9.2 Environment Variables

| Variable | Description |
|---|---|
| `DATABASE_URL` | Injected automatically by Railway from the PostgreSQL service |
| `LISTENNOTES_API_KEY` | Listen Notes API. Free tier sufficient to start. |
| `PODCASTINDEX_API_KEY` | Podcast Index key. Free. |
| `PODCASTINDEX_API_SECRET` | Podcast Index secret. Free. |
| `YOUTUBE_API_KEY` | YouTube Data API v3. Free tier: 10k units/day. |
| `WORKER_CONCURRENCY` | Total concurrent workers. Default 6. |
| `TRANSCRIPTION_CONCURRENCY` | Workers reserved for transcription. Default 2. |
| `MAX_EPISODE_AGE_DAYS` | How far back initial backfill goes. Default 365. |
| `NEMO_CACHE_DIR` | Parakeet model cache. Set to Railway volume mount path. |
| `AUDIO_TMP_DIR` | Temporary audio file storage. Default `/tmp/thinktank_audio`. |

### 9.3 Deployment Sequence

1. Create Railway project
2. Add PostgreSQL service — copy `DATABASE_URL`
3. Deploy API service — runs schema init on startup
4. Run seed script: `python -m scripts.seed_thinkers`
5. Deploy worker service on GPU instance — Parakeet model downloads to volume on first start (~15 min)
6. Deploy admin service
7. Verify via admin dashboard — queue should show initial discovery jobs running

### 9.4 Cost Estimate

| Item | Est. Monthly Cost |
|---|---|
| Railway PostgreSQL | ~$20/mo (5GB storage) |
| Railway API service | ~$5/mo (always on, minimal CPU) |
| Railway Worker (GPU L4) | ~$400–600/mo (always on) |
| Railway Admin service | ~$5/mo |
| Railway Volume (model cache) | ~$5/mo (25GB) |
| Listen Notes (paid) | $50/mo if free tier exceeded |
| YouTube Data API | $0 (free tier sufficient) |
| **Total** | **~$500–700/mo fully operational** |

> **Note:** The GPU worker can be paused when the transcription queue is empty and restarted manually or on a schedule. Running discovery-only (no GPU) costs ~$100/mo — a practical mode for the early corpus-building phase.

---

*End of Specification*
