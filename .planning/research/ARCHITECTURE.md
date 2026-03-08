# Architecture Patterns

**Domain:** Continuous content ingestion, transcription, and knowledge capture platform
**Researched:** 2026-03-08

---

## Recommended Architecture

ThinkTank is a **pipeline-oriented, job-driven ingestion system** deployed as four Railway services sharing a single PostgreSQL database. The architecture follows the "everything is a job" principle: every unit of work -- discovery, fetching, transcription, attribution, governance -- is a row in the `jobs` table, claimed by workers via `SELECT FOR UPDATE SKIP LOCKED`.

```
                                +-----------------+
                                |   Admin (HTMX)  |
                                |   Human oversight|
                                +--------+--------+
                                         |
                                    Reads/Writes
                                         |
+----------------+            +----------v----------+            +------------------+
|  External APIs |<---------->|    API Service       |<---------->|   PostgreSQL     |
|  (Listen Notes,|  webhooks  |    (FastAPI/uvicorn) |   asyncpg  |   (Railway)      |
|   PodcastIndex,|            +----------------------+            |                  |
|   YouTube,     |                                                |  - jobs          |
|   Twitter,     |            +----------------------+            |  - content       |
|   Anthropic)   |<---------->|  CPU Worker          |<---------->|  - thinkers      |
|                |  API calls |  (always-on, 4-6     |   asyncpg  |  - sources       |
+----------------+            |   concurrent tasks)  |            |  - llm_reviews   |
                              |                      |            |  - rate_limits   |
                              |  Runs:               |            |  - system_config |
                              |  - Discovery         |            +--------+---------+
                              |  - RSS fetching      |                     |
                              |  - Scraping          |                     |
                              |  - LLM Supervisor    |                     |
                              |  - GPU orchestration |                     |
                              |  - Attribution       |              asyncpg|
                              |  - Rate limiting     |                     |
                              +----------+-----------+            +--------v---------+
                                         |                        |  GPU Worker      |
                                   Railway API                    |  (on-demand L4)  |
                                   (scale 0<->1)                  |                  |
                                         |                        |  Runs:           |
                                         +----------------------->|  - Parakeet TDT  |
                                                                  |  - Audio->Text   |
                                                                  +------------------+
```

### Core Architectural Pattern: Pipeline of Autonomous Stages

The system is a **multi-stage pipeline** where each stage is a job type. Stages communicate exclusively through the database -- there are no direct inter-service RPC calls, no message brokers, no in-memory queues shared between services. This is the right pattern for ThinkTank because:

1. **Full crash recovery** -- Any service can restart and resume from its last committed state.
2. **Observability for free** -- Every unit of work has a database row with timestamps, status, errors, and worker attribution.
3. **Rate decoupling** -- Discovery can run faster than transcription without data loss; backpressure is managed via queue depth checks, not blocking calls.
4. **LLM governance** -- Jobs can be paused in `awaiting_llm` state without blocking the rest of the pipeline.

**Confidence: HIGH** -- This pattern is well-documented in production systems like Solid Queue (37signals), PGQueuer, and Procrastinate. PostgreSQL's `SELECT FOR UPDATE SKIP LOCKED` has been the standard for DB-backed job queues since Postgres 9.5 and is proven at scale.

---

## Component Boundaries

### Component 1: API Service

| Attribute | Detail |
|-----------|--------|
| **Responsibility** | HTTP interface for programmatic access and LLM Supervisor webhook entry |
| **Runtime** | FastAPI + uvicorn, always-on Railway standard service |
| **Communicates with** | PostgreSQL (asyncpg), external clients |
| **Owns** | Request validation, OpenAPI schema, Alembic migration runner (on startup) |
| **Does NOT own** | Job processing, worker coordination, GPU lifecycle |

The API service is intentionally thin. It reads state from the database and writes new jobs or configuration changes. It does not process jobs. This separation ensures the API stays responsive regardless of worker load.

**Key principle:** The API runs Alembic migrations on startup (`alembic upgrade head` in the entrypoint). This means the API service is the schema owner -- deploy it first, always.

### Component 2: CPU Worker

| Attribute | Detail |
|-----------|--------|
| **Responsibility** | All non-GPU job processing: discovery, fetching, scraping, LLM calls, attribution, scheduling |
| **Runtime** | Python asyncio event loop with 4-6 concurrent worker tasks, always-on |
| **Communicates with** | PostgreSQL (asyncpg), external APIs (httpx), Railway API (for GPU scaling), Anthropic API (for LLM Supervisor) |
| **Owns** | Job claim loop, rate limit coordination, stale job reclamation, GPU orchestration, backpressure management |
| **Does NOT own** | Transcription, schema migrations, HTTP request serving |

The CPU worker is the **heartbeat of the system**. It runs three internal loops that are NOT jobs-table jobs (to avoid circular dependency):

1. **Job claim loop** -- polls for pending jobs every 2s (active) to 30s (idle)
2. **Stale job reclaimer** -- runs every 5 minutes, reclaims jobs stuck in `running` state
3. **GPU scaling manager** -- runs every 5 minutes, scales GPU service based on `process_content` queue depth

The LLM Supervisor is logically separate but physically co-located on the CPU worker instance. It runs as scheduled tasks (health checks every 6h, daily digest at 07:00 UTC) and event-driven approval checks triggered by `awaiting_llm` jobs.

**Worker architecture pattern:** Each worker task is an async coroutine that:
1. Claims a job via `SELECT ... FOR UPDATE SKIP LOCKED`
2. Updates status to `running` with its `worker_id`
3. Executes the handler for that `job_type`
4. Updates status to `done` or `failed` on completion
5. Returns to the claim loop

This is the standard asyncio worker pool pattern. Using asyncio (not multiprocessing) is correct here because CPU worker jobs are I/O-bound: HTTP requests, database queries, API calls. The GIL is not a bottleneck for I/O-bound work.

### Component 3: GPU Worker

| Attribute | Detail |
|-----------|--------|
| **Responsibility** | Audio transcription using Parakeet TDT 1.1B |
| **Runtime** | Python process with 2-4 concurrent workers, on-demand Railway L4 GPU |
| **Communicates with** | PostgreSQL (asyncpg) |
| **Owns** | Parakeet model lifecycle, audio download/conversion, transcription execution |
| **Does NOT own** | Job scheduling, content discovery, rate limiting, API calls |

The GPU worker is a **single-purpose transcription service**. It only claims `process_content` jobs. Its lifecycle is managed externally by the CPU worker via Railway API:

- **Scale up:** When `process_content` pending count > `gpu_queue_threshold` (default 5)
- **Scale down:** When `process_content` pending+running count = 0 for `gpu_idle_minutes_before_shutdown` (default 30 min)

**Model lifecycle:** Parakeet TDT 1.1B is loaded from a Railway persistent volume (`/app/.nemo_cache`) into VRAM on startup. The model stays in VRAM across all jobs -- it is never unloaded between transcriptions. This amortizes the 2-5 minute model load cost across potentially hundreds of transcriptions per session.

**Concurrency model:** GPU workers use a hybrid approach:
- Audio download and database I/O are async (asyncio + httpx + asyncpg)
- Transcription inference is synchronous (PyTorch releases GIL during GPU compute)
- With 2-4 workers, one can download audio while another transcribes, keeping the GPU pipeline full

**Audio handling:** Audio is downloaded to a temp directory, converted to 16kHz mono WAV via ffmpeg, chunked if > 60 min, transcribed, and immediately deleted. No audio is persisted -- only the transcript text.

### Component 4: Admin Dashboard

| Attribute | Detail |
|-----------|--------|
| **Responsibility** | Human oversight, LLM decision override, system configuration |
| **Runtime** | FastAPI + HTMX, private networking only |
| **Communicates with** | PostgreSQL (asyncpg) |
| **Owns** | Dashboard views, override UI, kill switch |
| **Does NOT own** | Job processing, API endpoints, worker coordination |

The admin dashboard is a **read-heavy, write-rare** interface. Most of its value comes from real-time queue visibility (10-second HTMX polling) and the ability to override LLM decisions. It sits on Railway private networking -- not exposed to the public internet.

### Component 5: PostgreSQL Database

| Attribute | Detail |
|-----------|--------|
| **Responsibility** | Single source of truth for ALL state |
| **Runtime** | Railway-managed PostgreSQL 16 |
| **Communicates with** | All services via asyncpg |
| **Owns** | Schema, data integrity, job queue coordination, rate limit state |

PostgreSQL is not just a data store -- it is the **coordination layer**. It replaces Redis, message brokers, and external schedulers by providing:

1. **Job queue** via `SELECT FOR UPDATE SKIP LOCKED`
2. **Rate limiting** via sliding-window COUNT queries on `rate_limit_usage`
3. **Configuration** via `system_config` table (read by workers on each job claim)
4. **Audit trail** via `llm_reviews` table
5. **Backpressure signaling** via queue depth queries

This is an opinionated choice that simplifies infrastructure at the cost of theoretical throughput ceiling. For ThinkTank's scale (hundreds of jobs/hour, not millions), this is the right tradeoff.

### Component 6: LLM Supervisor (Logical Component)

| Attribute | Detail |
|-----------|--------|
| **Responsibility** | Governance over corpus expansion decisions |
| **Runtime** | Runs on CPU worker instance (not a separate service) |
| **Communicates with** | Anthropic Claude API (httpx), PostgreSQL |
| **Owns** | Approval/rejection decisions, health monitoring, daily digests, system config tuning |
| **Does NOT own** | Job execution, content processing |

The LLM Supervisor is a **logical component** that physically runs on the CPU worker. It is not a separate service because:

1. It shares the same database connection pool
2. Its workload is infrequent (a few API calls per hour at most)
3. Separate deployment would double infrastructure cost for minimal benefit

It operates on two tracks:
- **Event-driven:** Triggered when jobs enter `awaiting_llm` state (thinker approval, source approval, candidate promotion, error resume)
- **Scheduled:** Health checks (6h), daily digest (07:00 UTC), weekly audit (Monday 07:00 UTC), quota checks (triggered by threshold proximity)

**Fallback behavior:** When the Anthropic API is unavailable, jobs accumulate in `awaiting_llm` state. After `llm_timeout_hours` (default 2h), they are escalated to the admin dashboard for human review. Workers continue processing already-approved thinkers and sources -- the pipeline degrades gracefully, not catastrophically.

---

## Data Flow

### Primary Ingestion Flow

```
1. BOOTSTRAP
   seed_thinkers.py → thinkers (pending_llm) → LLM batch review → approved thinkers

2. DISCOVERY (CPU Worker, repeats on schedule)
   refresh_due_sources → discover_thinker jobs → fan-out:
   ├── fetch_podcast_feed (RSS parse → content rows, status=pending)
   ├── fetch_guest_feed (Listen Notes/Podcast Index → content rows)
   ├── scrape_substack (RSS parse → content rows)
   ├── fetch_youtube_channel (Tier 1 only → content rows)
   ├── discover_guests_listennotes → candidate_thinkers (pending_llm)
   ├── discover_guests_podcastindex → candidate_thinkers (pending_llm)
   └── snapshot_metrics → thinker_metrics rows

3. CONTENT FILTERING (during fetch, CPU Worker)
   Each content item checked against:
   ├── Duration filter (< 600s → status=skipped)
   ├── Title pattern filter (trailer, teaser, etc. → status=skipped)
   └── Dedup (canonical_url → fingerprint → trigram on candidates)

4. TRANSCRIPTION (GPU Worker, on-demand)
   content (status=pending) → process_content job:
   ├── Pass 1: YouTube captions (yt-dlp, own channels only)
   ├── Pass 2: Existing transcript (per-source config pattern)
   └── Pass 3: Parakeet TDT 1.1B (download → ffmpeg → inference → delete audio)
   → content.body_text populated, status=done

5. ATTRIBUTION (CPU Worker, after each fetch batch)
   tag_content_thinkers job:
   ├── Source owner → role=primary, confidence=10
   ├── Title name match → role=guest, confidence=9
   ├── Description name match → role=guest, confidence=4-6
   └── Host extraction → role=host, confidence=10
   → content_thinkers junction rows

6. GOVERNANCE (LLM Supervisor on CPU Worker)
   ├── Event-driven: awaiting_llm jobs → Claude API → decision → llm_reviews
   └── Scheduled: health check (6h), daily digest, weekly audit, quota check
   → llm_reviews rows, system_config updates, thinker/source status changes

7. CASCADE (CPU Worker, continuous)
   scan_for_candidates → candidate_thinkers (pending_llm) → LLM review:
   ├── approved → new thinker → new discover_thinker job → back to step 2
   ├── rejected → candidate archived
   └── duplicate → merged with existing thinker
```

### Data Flow Direction Rules

1. **All state changes go through PostgreSQL** -- no service stores mutable state locally.
2. **Services communicate via shared database state** -- no direct inter-service calls (except CPU worker calling Railway API for GPU scaling).
3. **Jobs flow downhill by priority** -- discovery (P1-2) creates content rows that become transcription jobs (P3), which trigger attribution jobs (P3).
4. **Governance gates are inline** -- jobs requiring LLM approval enter `awaiting_llm` and block until approved; they do not bypass the queue.
5. **Backpressure flows uphill** -- when `process_content` queue is deep, discovery job priority is demoted, slowing content discovery without stopping it.

### State Machine: Job Lifecycle

```
                    ┌─────────┐
                    │ pending │◄──────────────────────────────┐
                    └────┬────┘                               │
                         │                                    │
            ┌────────────┼────────────────┐                   │
            │            │                │                   │
            v            v                v                   │
   ┌──────────────┐  ┌────────┐  ┌──────────────┐            │
   │ awaiting_llm │  │running │  │   retrying   │────────────┘
   └──────┬───────┘  └───┬────┘  └──────────────┘    (scheduled_at
          │              │                             in future)
    LLM decides          │
          │         ┌────┼─────┐
    ┌─────┼─────┐   │    │     │
    v     v     v   v    v     v
┌──────┐┌────┐┌─────────┐┌──────┐┌───────────────┐
│reject││pend││  done   ││failed││ escalate_human│
│ed_llm││ing ││         ││      ││               │
└──────┘└────┘└─────────┘└──────┘└───────────────┘
```

### State Machine: Content Lifecycle

```
                    ┌─────────┐
     discovered ──> │ pending │
                    └────┬────┘
                         │
              ┌──────────┼──────────┐
              v          v          v
         ┌─────────┐┌──────────┐┌───────┐
         │ skipped ││processing││ error │
         │(filtered)│└────┬─────┘└───┬───┘
         └─────────┘     │          │
                         v          │ (retry)
                    ┌────────┐      │
                    │  done  │◄─────┘
                    │(text in│
                    │body_text)
                    └────────┘
```

---

## Patterns to Follow

### Pattern 1: Atomic Job Claim with SKIP LOCKED

**What:** Workers claim jobs using a single atomic SQL statement that selects, locks, and updates in one transaction.

**When:** Every job claim in both CPU and GPU workers.

**Why:** Eliminates race conditions between concurrent workers without external locking infrastructure. Multiple workers can poll simultaneously without claiming the same job.

```python
async def claim_job(session: AsyncSession, job_types: list[str], worker_id: str) -> Job | None:
    stmt = (
        select(Job)
        .where(
            Job.status.in_(["pending", "retrying"]),
            Job.job_type.in_(job_types),
            Job.scheduled_at <= func.now(),
        )
        .order_by(Job.priority.asc(), Job.created_at.asc())
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    result = await session.execute(stmt)
    job = result.scalar_one_or_none()
    if job:
        job.status = "running"
        job.worker_id = worker_id
        job.started_at = func.now()
        await session.commit()
    return job
```

**Index requirement:** Composite index on `(status, priority, scheduled_at, created_at)` is critical for performance.

**Confidence: HIGH** -- This exact pattern is documented in PostgreSQL official docs, Vlad Mihalcea's guide, and used by Solid Queue, PGQueuer, and Procrastinate.

### Pattern 2: Cooperative Rate Limiting via Database

**What:** Workers check a sliding-window count in `rate_limit_usage` before making external API calls and insert a row to claim a slot.

**When:** Before every call to Listen Notes, YouTube, Podcast Index, Twitter, or Anthropic APIs.

**Why:** Multiple concurrent workers must cooperate on rate limits without a central broker. The database is the shared state.

```python
async def acquire_rate_limit(session: AsyncSession, api_name: str, worker_id: str, limit: int) -> bool:
    count_stmt = select(func.count()).where(
        RateLimitUsage.api_name == api_name,
        RateLimitUsage.called_at > func.now() - text("INTERVAL '1 hour'"),
    )
    result = await session.execute(count_stmt)
    current_count = result.scalar()

    if current_count >= limit:
        return False

    session.add(RateLimitUsage(api_name=api_name, worker_id=worker_id, called_at=func.now()))
    await session.commit()
    return True
```

**Tradeoff:** This is advisory, not transactional. Two workers checking simultaneously could both see count=99 and both proceed to 101. This is acceptable -- external APIs have their own enforcement, and the goal is to stay comfortably under limits, not exactly at them.

**Confidence: HIGH** -- Standard pattern for cooperative rate limiting without Redis.

### Pattern 3: Queue-Depth-Driven GPU Scaling

**What:** CPU worker periodically checks `process_content` queue depth and scales the GPU service via Railway API.

**When:** Every 5 minutes via internal CPU worker loop.

**Why:** GPU instances cost approximately $1-3/hour. Running one continuously when the transcription queue is empty wastes $720-2,160/month. On-demand scaling reduces this to actual usage.

```python
async def manage_gpu_scaling(session: AsyncSession, railway_client: RailwayClient):
    pending_count = await session.execute(
        select(func.count()).where(
            Job.job_type == "process_content",
            Job.status == "pending",
        )
    )
    count = pending_count.scalar()
    gpu_status = await railway_client.get_service_replicas(GPU_SERVICE_ID)

    threshold = await get_config(session, "gpu_queue_threshold")
    idle_minutes = await get_config(session, "gpu_idle_minutes_before_shutdown")

    if count > threshold and gpu_status.replicas == 0:
        await railway_client.scale_service(GPU_SERVICE_ID, replicas=1)
    elif count == 0 and gpu_status.replicas > 0:
        if gpu_status.idle_duration_minutes >= idle_minutes:
            await railway_client.scale_service(GPU_SERVICE_ID, replicas=0)
```

**Confidence: MEDIUM** -- Railway's GraphQL API supports replica scaling, but specific mutation names need to be discovered via their GraphiQL playground (the docs don't enumerate them). The pattern itself is sound; the API integration will need exploration during implementation.

### Pattern 4: Three-Layer Deduplication

**What:** Content is deduplicated at three levels: URL normalization, content fingerprinting, and trigram similarity on candidates.

**When:** During every content insertion.

**Why:** The same podcast episode appears at multiple URLs (Apple Podcasts, Spotify, direct RSS). Without multi-layer dedup, the corpus fills with duplicates that waste transcription GPU time.

```
Layer 1: Canonical URL (UNIQUE constraint)
  https://www.podcasts.apple.com/podcast/ep-42?utm_source=twitter
  → https://podcasts.apple.com/podcast/ep-42

Layer 2: Content Fingerprint (UNIQUE constraint)
  sha256(lowercase("AI Safety with Yoshua Bengio") || "2025-11-15" || "3600")
  Catches: same episode on different platforms with different URLs

Layer 3: Trigram Similarity (pg_trgm, threshold 0.7)
  "Dr. John Smith Ph.D." → "john smith" (normalized)
  Applied to candidate_thinkers and thinker name matching
```

**Confidence: HIGH** -- URL normalization and content fingerprinting are standard practices in web crawlers (Scrapy, newspaper3k). pg_trgm for fuzzy name matching is PostgreSQL-native and well-proven.

### Pattern 5: Backpressure via Priority Demotion

**What:** When the transcription queue is deep, discovery jobs are automatically demoted in priority so they run less frequently.

**When:** `process_content` pending count exceeds `max_pending_transcriptions` (default 500).

**Why:** Pausing discovery entirely risks missing time-sensitive content. Priority demotion slows discovery proportionally without stopping it. When the GPU drains the queue, normal priority resumes automatically.

**Confidence: HIGH** -- Priority-based backpressure is simpler and more robust than pause/resume mechanisms. No external coordination needed.

### Pattern 6: LLM-as-Governance-Gate

**What:** Jobs that expand the corpus scope (new thinkers, new sources, candidate promotions) are blocked in `awaiting_llm` state until the LLM Supervisor approves them.

**When:** Any job that would grow the system's data surface area.

**Why:** Without governance, an automated crawler will inevitably accumulate garbage -- off-topic thinkers, duplicate sources, spam podcasts. The LLM provides semantic judgment that rule-based filters cannot.

**Key design decisions:**
- LLM decisions are structured JSON (not prose) for machine-parseable output
- Every decision is logged to `llm_reviews` with full context snapshot for audit
- Failed LLM calls escalate to human (admin dashboard), never auto-approve
- Workers continue processing already-approved content during LLM downtime

**Confidence: MEDIUM** -- This is an emerging pattern. The governance-gate concept is proven (approval workflows are decades old), but using an LLM as the approver is newer. The fallback-to-human-on-failure design mitigates the risk.

---

## Anti-Patterns to Avoid

### Anti-Pattern 1: Shared In-Memory State Between Workers

**What:** Using Python globals, class-level caches, or shared dictionaries to coordinate between concurrent worker tasks.

**Why bad:** Workers may run across different processes (CPU vs GPU) or even different machines. In-memory state does not survive restarts, cannot be observed externally, and creates subtle concurrency bugs.

**Instead:** All coordination goes through PostgreSQL. Rate limits, configuration, job status, backpressure signals -- everything is a database query.

### Anti-Pattern 2: Synchronous Job Processing on the API Service

**What:** Processing jobs inline during HTTP request handling (e.g., "submit a thinker and wait for LLM approval before returning").

**Why bad:** LLM calls take 5-30 seconds. Rate-limited API calls can take longer. Tying these to HTTP request lifecycles creates timeouts, unresponsive APIs, and cascading failures.

**Instead:** API writes a job row and returns immediately. Workers process asynchronously. Clients poll status or use webhooks.

### Anti-Pattern 3: Using Celery/Redis for Job Queue

**What:** Introducing Celery + Redis as a message broker alongside PostgreSQL.

**Why bad for ThinkTank:** Adds operational complexity (two stateful services), splits state across two systems (queue state in Redis, results in PostgreSQL), and provides throughput far beyond what ThinkTank needs. At hundreds of jobs per hour, PostgreSQL `SKIP LOCKED` is sufficient and keeps everything in one place.

**When Redis would be warranted:** If job throughput exceeds ~10,000 jobs/minute, or if sub-second job latency is required. ThinkTank has neither requirement.

### Anti-Pattern 4: Loading Parakeet Model Per-Job

**What:** Loading the 1.1B parameter model from disk into VRAM for each transcription job, then unloading.

**Why bad:** Model load takes 2-5 minutes. With an average transcription taking 1-3 minutes (at 40x real-time), the overhead would exceed the work time. A 2-hour podcast at 40x real-time takes ~3 minutes to transcribe but 2-5 minutes to load the model.

**Instead:** Load model once at GPU worker startup. Keep it in VRAM across all jobs. Scale the entire service to 0 replicas when idle (Railway volume persists the cached model weights for fast reload).

### Anti-Pattern 5: Fan-Out Without Backpressure

**What:** Discovery jobs creating unlimited content rows and `process_content` jobs with no regard for transcription capacity.

**Why bad:** A single thinker with 500 episodes at initial backfill creates 500 `process_content` jobs. 50 thinkers at bootstrap = 25,000 pending transcriptions. Without backpressure, the GPU worker will never catch up, and the queue becomes effectively unbounded.

**Instead:** Priority demotion when `process_content` queue exceeds threshold. Cap per-thinker episode fetch with `max_episodes_per_thinker_per_run`. LLM Supervisor can reduce `approved_backfill_days` at approval time.

---

## Scalability Considerations

| Concern | At 50 thinkers (launch) | At 500 thinkers | At 5,000 thinkers |
|---------|------------------------|------------------|---------------------|
| **Job queue throughput** | PostgreSQL SKIP LOCKED handles easily | Still well within PostgreSQL capacity | May need connection pooling (PgBouncer) |
| **Transcription backlog** | GPU clears queue in hours | May need 2 GPU replicas or longer runtimes | Need autoscaling policy based on queue depth |
| **Database size** | < 1 GB (text is small per-row) | 10-50 GB with full transcripts | Consider partitioning `content` table by `published_at` |
| **Rate limits (external APIs)** | Well within free tiers | Listen Notes paid tier, YouTube quota management | Multiple API keys per service, rotating |
| **LLM Supervisor load** | A few calls/day | Dozens of calls/day, batch reviews | Token optimization, possibly cheaper model for routine approvals |
| **Discovery concurrency** | 4-6 CPU workers sufficient | May need 8-10 workers | Split discovery across multiple CPU services |

### Scale-Critical Design Decisions (Already in Spec)

1. **Content text in PostgreSQL, not S3** -- Correct for v1. Transcripts are ~50KB each. At 50,000 transcripts, that is ~2.5 GB -- well within PostgreSQL's comfort zone. S3 offload is explicitly deferred to Phase 2.

2. **Single PostgreSQL instance** -- Correct for v1. Railway-managed PostgreSQL handles the load. Read replicas would be the first scaling step if admin dashboard queries start competing with worker writes.

3. **On-demand GPU** -- Critical for cost. At $1-3/hour for L4, always-on GPU would cost $720-2,160/month. On-demand with 30-min idle timeout brings this to ~$100-200/month.

---

## Suggested Build Order (Dependencies Between Components)

The architecture has clear dependency chains that dictate build order:

### Layer 0: Foundation (build first, everything depends on it)

1. **Database schema + Alembic migrations** -- Every other component reads/writes the database.
2. **SQLAlchemy models** -- Python representations of the schema.
3. **Shared configuration module** -- Database URL, environment variables, logging setup.
4. **Structured logging** -- Required by STANDARDS.md from line one.

**Rationale:** Nothing can be tested or developed without models and migrations. The schema IS the architecture.

### Layer 1: Job Infrastructure (build second, workers depend on it)

5. **Job queue claim/complete/fail primitives** -- The `SELECT FOR UPDATE SKIP LOCKED` claim function, status transitions, error handling.
6. **Worker base class** -- Asyncio event loop, job dispatch by type, graceful shutdown.
7. **Rate limit coordination** -- `rate_limit_usage` table operations, sliding window check.
8. **Stale job reclamation** -- Internal loop for reclaiming stuck jobs.

**Rationale:** Every job handler depends on the queue infrastructure. Building this before any specific handler ensures a solid foundation.

### Layer 2: Core Pipeline (build third, in this order)

9. **RSS/feed fetching handlers** -- `fetch_podcast_feed`, the most common job type.
10. **Content deduplication** -- URL normalization, fingerprinting, uniqueness checks.
11. **Content filtering** -- Duration filter, title pattern filter, per-source overrides.
12. **Discovery orchestration** -- `discover_thinker`, `refresh_due_sources`, fan-out logic.

**Rationale:** Feed fetching is the primary content source. Dedup and filtering must work before content flows to transcription. Discovery orchestration ties it together.

### Layer 3: Transcription (build fourth, depends on content existing)

13. **Transcription pipeline** -- Three-pass approach: YouTube captions, existing transcripts, Parakeet.
14. **GPU worker** -- Separate entrypoint, model loading, audio handling.
15. **GPU orchestration** -- Railway API integration for scale up/down.

**Rationale:** Transcription depends on content rows existing. The GPU worker is a separate deployment target and can be developed independently once the job infrastructure exists.

### Layer 4: Governance (build fifth, depends on pipeline working)

16. **LLM Supervisor prompts** -- Structured JSON prompts for each review type.
17. **Approval flow** -- `awaiting_llm` status, `llm_approval_check` job handler, decision application.
18. **Scheduled checks** -- Health check, daily digest, weekly audit, quota check.
19. **Fallback/escalation** -- Timeout detection, human review escalation.

**Rationale:** The LLM Supervisor adds governance to an already-working pipeline. Building the pipeline first allows testing without LLM dependency, then adding the governance layer.

### Layer 5: API + Admin (build sixth, depends on everything else)

20. **API endpoints** -- CRUD for thinkers, sources, content, jobs, system config.
21. **Admin dashboard** -- HTMX views for queue monitoring, LLM review panel, overrides.
22. **Bootstrap scripts** -- Seed categories, config, thinkers, initial LLM review.

**Rationale:** The API and admin are read-heavy views over existing data. They depend on the pipeline being operational but do not block pipeline development.

### Layer 6: Advanced Features (build last, incremental value)

23. **Cascade discovery** -- `scan_for_candidates`, candidate pipeline, promotion flow.
24. **Guest discovery** -- Listen Notes + Podcast Index integration.
25. **Content attribution** -- `tag_content_thinkers` with name matching.
26. **Metrics snapshots** -- Twitter/YouTube follower counts.
27. **API usage rollup** -- Hourly aggregation, cost tracking.

**Rationale:** These features compound value but are not required for the core ingestion loop. The system is useful with just approved thinkers, feed fetching, and transcription.

---

## Key Architectural Boundaries to Preserve

1. **Workers never call the API service.** All inter-component communication goes through the database.
2. **The API service never processes jobs.** It writes job rows and returns.
3. **The GPU worker only handles `process_content`.** It does not make API calls, run LLM checks, or do discovery.
4. **The LLM Supervisor is a logical component, not a service.** It runs on the CPU worker instance.
5. **External API calls are always rate-limited.** No worker makes an external call without checking `rate_limit_usage` first.
6. **All LLM decisions are logged.** The `llm_reviews` table is append-only and contains the full context snapshot, prompt, and response.
7. **Audio is never persisted.** Temp files are created, transcribed, and deleted within a single job execution.

---

## Sources

- [PostgreSQL SKIP LOCKED for job queues](https://www.inferable.ai/blog/posts/postgres-skip-locked) -- HIGH confidence
- [PostgreSQL FOR UPDATE SKIP LOCKED guide](https://www.dbpro.app/blog/postgresql-skip-locked) -- HIGH confidence
- [SQLAlchemy discussion on SKIP LOCKED](https://github.com/sqlalchemy/sqlalchemy/discussions/10460) -- HIGH confidence
- [PGQueuer: PostgreSQL-backed async job queue](https://github.com/janbjorge/pgqueuer) -- HIGH confidence
- [Procrastinate: PostgreSQL task queue for Python](https://github.com/procrastinate-org/procrastinate) -- HIGH confidence
- [NVIDIA Parakeet TDT 1.1B model card](https://huggingface.co/nvidia/parakeet-tdt-1.1b) -- HIGH confidence
- [NVIDIA Parakeet TDT technical blog](https://developer.nvidia.com/blog/turbocharge-asr-accuracy-and-speed-with-nvidia-nemo-parakeet-tdt/) -- HIGH confidence
- [Railway Scaling documentation](https://docs.railway.com/reference/scaling) -- MEDIUM confidence (GPU-specific scaling docs sparse)
- [Railway Public API documentation](https://docs.railway.com/integrations/api) -- MEDIUM confidence (mutation names need GraphiQL exploration)
- [Airflow-orchestrated podcast transcription pipeline (MDPI 2025)](https://www.mdpi.com/3042-6308/2/1/1) -- MEDIUM confidence
- [Vlad Mihalcea: Database job queue with SKIP LOCKED](https://vladmihalcea.com/database-job-queue-skip-locked/) -- HIGH confidence
- [Neon Postgres queue system guide](https://neon.com/guides/queue-system) -- HIGH confidence
- [LLM governance and approval gate patterns](https://www.zenml.io/blog/what-1200-production-deployments-reveal-about-llmops-in-2025) -- MEDIUM confidence
