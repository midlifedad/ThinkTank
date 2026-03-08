# Feature Landscape

**Domain:** Podcast ingestion and knowledge infrastructure engine
**Researched:** 2026-03-08
**Overall confidence:** HIGH

ThinkTank is not a podcast app or hosting platform. It is a knowledge infrastructure layer that continuously discovers, fetches, and transcribes expert content (primarily podcasts) into a structured relational store, forming the foundation for downstream claim extraction and knowledge analysis. The feature landscape below is evaluated through that lens: what does a robust content ingestion and corpus management pipeline need?

---

## Table Stakes

Features the system must have to function as a reliable, autonomous ingestion pipeline. Without these, the system either fails to operate, produces garbage data, or becomes unmanageable.

| # | Feature | Why Expected | Complexity | Notes |
|---|---------|--------------|------------|-------|
| T1 | **RSS feed polling and episode extraction** | Core ingestion path. Without reliable RSS parsing, no content enters the system. Every podcast ingestion system starts here. feedparser is battle-tested. | Low | feedparser handles most RSS edge cases. Per-source config overrides needed for non-standard feeds. |
| T2 | **Multi-pass transcription pipeline** | The entire system's value depends on converting audio to text. Three-pass approach (YouTube captions, existing transcripts, Parakeet GPU inference) is the right layered strategy -- cheapest first, GPU last. | High | Parakeet TDT 1.1B delivers ~6% WER at 40x real-time on L4 GPU. The three-pass hierarchy minimizes GPU spend. Audio chunking for episodes >60 min is non-trivial but well-understood. |
| T3 | **URL normalization and canonical dedup** | Same episode appears on Apple Podcasts, Spotify, podcast website, etc. Without URL normalization (strip UTMs, force HTTPS, canonicalize YouTube IDs), the corpus fills with duplicates. | Medium | Layer 1 of the 3-layer dedup strategy. Standard web crawling practice. The `canonical_url` UNIQUE constraint catches exact duplicates. |
| T4 | **Content fingerprinting** | Catches same content at different URLs. `sha256(title + date + duration)` is simple and effective for podcast episodes where title+date+duration is a strong identity signal. | Low | Layer 2 of dedup. Must handle null fingerprints (content without title yet). UNIQUE constraint with null exclusion. |
| T5 | **DB-backed job queue with priority and retry** | Everything is a job. The queue is the system's nervous system. PostgreSQL `SELECT FOR UPDATE SKIP LOCKED` handles up to ~50k jobs/sec which is orders of magnitude beyond what ThinkTank needs. Eliminates Redis dependency. | High | Complex to get right: priority ordering, stale job reclamation, exponential backoff, max attempts per job type, `awaiting_llm` status. But this is the architectural backbone. |
| T6 | **Stale job reclamation** | Workers crash, hang, or lose network. Without reclamation, stuck jobs permanently block queue progress. Every production job queue needs this. | Medium | Periodic check for `running` jobs exceeding `stale_job_timeout_minutes`. Returns them to queue with incremented attempts. Standard pattern. |
| T7 | **Rate limiting for external APIs** | Listen Notes (10k/mo free), YouTube Data API (quota-based), Podcast Index (free but rate-limited). Without cooperative rate limiting, concurrent workers blow through quotas in minutes. | Medium | Sliding-window counter in Postgres via `rate_limit_usage` table. Workers acquire a slot before calling. Advisory, not transactional -- acceptable because external APIs enforce their own limits as a backstop. |
| T8 | **Content filtering (duration + title patterns)** | Trailers, promos, "best of" compilations, ad breaks -- these waste GPU time and pollute the corpus with non-substantive content. Filtering is essential for corpus quality. | Low | `min_duration_seconds` (default 600s) and `skip_title_patterns` list. Per-source overrides via `sources.config`. Skipped content retains its row to prevent re-discovery. |
| T9 | **Thinker-source-content hierarchy** | Core data model. Thinkers own sources, sources produce content. Without this hierarchy, the corpus is an unstructured pile of transcripts. | Medium | Schema design is already well-specified. Categories, tiers, approval status, and junction tables create the organizational backbone. |
| T10 | **Health endpoint per service** | Standard operational requirement. Every service needs `GET /health` that verifies DB connectivity and key dependencies. Without it, Railway (or any platform) cannot properly manage service lifecycle. | Low | FastAPI makes this trivial. Return 200 if DB connected and worker loop running. |
| T11 | **Structured logging** | JSON logs with correlation IDs, timestamps, service name, job ID. Without structured logging, debugging a multi-service async pipeline is impossible. | Low | Standard practice. `structlog` or Python's built-in logging with JSON formatter. Correlation ID propagated per job. |
| T12 | **Environment-based configuration with DB overrides** | Secrets in env vars, operational parameters in `system_config` table. Feature toggles changeable without redeploy. Standard 12-factor app practice. | Low | Clear precedence: env vars > DB config > code defaults. Already specified in STANDARDS.md. |
| T13 | **Alembic migrations from day one** | Forward-only, reversible schema migrations. No raw DDL against a database with data. Standard for any Postgres-backed system. | Low | Initial migration contains full schema. Incremental from there. Runs on deploy automatically. |
| T14 | **Source approval workflow** | Sources cannot be fetched until approved. Prevents garbage sources from entering the pipeline. Without gating, the corpus grows uncontrollably with low-quality content. | Medium | `approval_status` on sources table. Workers only process `approved` sources. Approval can come from LLM or admin. |
| T15 | **API for system state and content access** | Programmatic interface to the system. Needed for admin dashboard, monitoring, and eventual downstream consumers. FastAPI with auto-generated OpenAPI docs. | Medium | RESTful endpoints for thinkers, sources, content, jobs, system config. Read-heavy initially. |

---

## Differentiators

Features that make ThinkTank uniquely valuable compared to generic podcast tools or simple transcription services. These represent the system's competitive advantage as a knowledge infrastructure platform.

| # | Feature | Value Proposition | Complexity | Notes |
|---|---------|-------------------|------------|-------|
| D1 | **LLM Supervisor governing all corpus expansion** | The single most differentiating feature. Every new thinker, source, and candidate goes through Claude for approval. This prevents garbage-in at the gate rather than cleaning up after. Most ingestion systems use manual curation or no curation -- neither scales. | High | Event-driven approvals + scheduled health checks + daily digests + weekly audits. Full audit trail in `llm_reviews`. Structured JSON prompts with decision schemas. Requires Anthropic API reliability handling (timeout escalation, fallback to human). |
| D2 | **Cascade discovery of candidate thinkers** | The system grows itself. Every podcast episode scanned for names not in the thinkers table. After 3+ appearances, names surface as candidates for LLM review. This is how the corpus compounds organically without manual curation of every person. | High | Name extraction from titles/descriptions, normalized dedup against existing thinkers (`pg_trgm` similarity at 0.7 threshold), daily quota limits, LLM batch review. This is where ThinkTank transitions from a manual tool to an autonomous system. |
| D3 | **Tiered thinker hierarchy with tier-based refresh** | Not all thinkers are equal. Tier 1 (top) refreshes every 6 hours, Tier 3 (emerging) weekly. This focuses resources on the highest-value content while still capturing the long tail. Most systems treat all sources equally. | Low | Simple `refresh_interval_hours` on sources, set by tier at approval time. The complexity is in the LLM deciding appropriate tiers, not the mechanism itself. |
| D4 | **Category-organized taxonomy with relevance scoring** | Thinkers are placed in a hierarchical category tree with relevance scores. Enables "show me all AI safety thinkers" or "who covers macro economics?" queries. This organizational structure is what makes the corpus navigable for downstream analysis. | Medium | Junction table with `relevance` score (1-10). Category tree with parent-child relationships. LLM assigns categories during thinker approval. Expandable taxonomy. |
| D5 | **On-demand GPU worker scaling via Railway API** | GPU costs $100-200/mo if always-on. On-demand scaling (spin up when transcription queue > 5, shut down after 30 min idle) could cut this by 50-70%. CPU worker manages GPU lifecycle programmatically. | High | Railway API integration for service replica management. Model cache on persistent volume eliminates re-download. The tricky part is cold-start time (~2-5 min for model load into VRAM) and handling crashes mid-transcription. |
| D6 | **3-layer content deduplication** | URL normalization + content fingerprinting + trigram similarity for candidate names. Most systems stop at URL dedup. The fingerprint layer catches cross-platform duplicates (same episode on Apple vs Spotify). Trigram similarity catches "Dr. John Smith" vs "John Smith Ph.D." | Medium | Each layer is simple individually. The value is in the layered defense: layer 1 is fast and catches 90%, layer 2 catches cross-platform reposts, layer 3 prevents candidate/thinker name collisions. |
| D7 | **Content attribution with confidence scoring** | `content_thinkers` junction with role (host/guest/panelist/mentioned) and confidence (1-10). Enables queries like "all episodes where Andrej Karpathy appeared as a guest with confidence >= 7." Most podcast tools only track the show owner. | Medium | Title matching (confidence 9), description matching (confidence 6), partial name matching (confidence 4). V1 is string matching -- LLM-assisted NER deferred to Phase 2. Low-confidence attributions retained but excluded from default downstream analysis. |
| D8 | **Backpressure mechanism** | When transcription queue exceeds threshold, discovery job priority is automatically demoted. Prevents unbounded queue growth without halting discovery entirely. Sophisticated systems slow down rather than stop. | Low | Simple queue depth check before each discovery job. Priority demotion by +3 when queue > `max_pending_transcriptions`. Resumes when queue drops to 80% of threshold. |
| D9 | **Admin dashboard with LLM decision panel** | Human oversight layer above the LLM Supervisor. Shows pending approvals, recent decisions, override capability with audit trail, daily digests, queue depth charts, rate limit gauges, GPU status, cost tracking. Most automated systems lack a meaningful human override path. | High | HTMX + FastAPI. Real-time queue visualization (10-second refresh). LLM decision review with approve/reject/override. Error log, source health, candidate queue. This is the operator's window into the autonomous system. |
| D10 | **API cost tracking and budget awareness** | Hourly rollup of external API usage with estimated USD costs. Rate limit gauges showing current usage vs configured limits. GPU cost estimation. This is first-class, not an afterthought. Most ingestion systems discover their costs after the bill arrives. | Medium | `api_usage` table with hourly aggregation from `rate_limit_usage`. Per-API unit cost tracking (YouTube quota units, Anthropic tokens). Dashboard visualization. Daily digest includes cost summary. |
| D11 | **LLM fallback and timeout escalation** | When the Anthropic API is down, the system degrades gracefully: existing pipeline continues on approved content, pending approvals escalate to human review after `llm_timeout_hours`, admin dashboard shows outage banner. Most LLM-dependent systems simply break when the API is unavailable. | Medium | Timeout check every 15 minutes. Auto-escalation after 2 hours. Persistent warning after 3 consecutive scheduled check failures. Recovery processes accumulated queue in FIFO order. |
| D12 | **Full LLM audit trail with human override** | Every LLM decision logged with context snapshot, prompt used, raw response, parsed decision, reasoning. Any decision overridable by admin with logged reasoning. Override history fed back to LLM in next health check. This creates a learning feedback loop. | Medium | `llm_reviews` table is the complete decision history. Override fields on each review row. Context snapshot preserves what the LLM saw when it decided. Enables post-hoc analysis of LLM decision quality. |
| D13 | **Podcast guest discovery via Listen Notes + Podcast Index** | Finds thinker appearances across the entire podcast ecosystem, not just their own feeds. Uses two complementary APIs: Listen Notes for guest search, Podcast Index for feed lookup. This is how the corpus captures the most valuable content -- unscripted guest appearances where thinkers are most revealing. | Medium | Fuzzy name matching against API results. Rate-limited API calls. Guest feeds fetched as RSS and filtered. This is the primary mechanism for "total capture" of a thinker's public audio presence. |
| D14 | **Global kill switch and operational controls** | `workers_active = false` immediately halts all workers. Ability to pause specific job types (e.g., GPU processing for cost control). LLM Supervisor can trigger kill switch if it detects systemic issues. Essential for an autonomous system that runs unsupervised. | Low | Single `system_config` entry. Workers check on each job claim. Admin dashboard toggle. Simple but critical for operational safety. |

---

## Anti-Features

Features to explicitly NOT build in V1. Including these would add complexity without proportional value, distract from the core mission, or belong in a later phase.

| # | Anti-Feature | Why Avoid | What to Do Instead |
|---|--------------|-----------|-------------------|
| AF1 | **Query/retrieval interface** | ThinkTank V1 is ingestion-only. Building search, filtering, and retrieval UX before the corpus is populated and validated puts the cart before the horse. | Store full transcripts in Postgres `body_text`. API endpoints expose raw content for downstream consumers. Retrieval layer is a separate future milestone. |
| AF2 | **Claim/opinion extraction** | Downstream analysis (extracting structured claims, opinions, research citations from transcripts) depends on having a high-quality, well-attributed corpus first. Attempting extraction on a half-built corpus wastes LLM tokens on bad input. | Focus V1 entirely on ingestion quality. Extraction is a distinct milestone built on top of the completed corpus. |
| AF3 | **Speaker diarization in transcripts** | Diarization ("who said what") is valuable but adds significant pipeline complexity (VAD + segmentation + speaker embeddings + clustering). Parakeet TDT 1.1B does not include diarization. Adding it requires NeMo MSDD or a separate model, doubling GPU pipeline complexity. | Defer to Month 2 per spec. V1 transcripts are undiarized full text. Attribution happens at the episode level via `content_thinkers`, not at the utterance level. |
| AF4 | **pgvector embeddings and semantic search** | Embeddings are useful for retrieval and similarity search, but V1 has no retrieval interface. Computing embeddings for every transcript consumes significant GPU/API resources with no consumer yet. | Defer to the retrieval layer milestone. When needed, embed transcripts in batch after corpus is stable. |
| AF5 | **Real-time streaming ingestion** | Podcasts are published on schedules (daily/weekly). Batch polling at tier-based intervals (6h/24h/168h) is more than sufficient. Real-time adds WebSocket complexity, higher API costs, and infrastructure burden for zero practical benefit. | Tier-based refresh intervals with `refresh_due_sources` hourly check. Tier 1 thinkers refresh every 6 hours -- fast enough for any podcast publishing cadence. |
| AF6 | **Multi-tenant access control** | ThinkTank is a single-owner system. Building RBAC, tenant isolation, and per-user permissions adds architectural complexity that serves no current user. | Single admin user with full access. Admin dashboard behind private networking. Authentication can be basic auth or Railway's built-in private networking. |
| AF7 | **Non-text content (images, video frames, PDFs)** | Text-first strategy. Podcast audio is converted to text. Video is only used for its audio track. Processing thumbnails, slides, or video frames is a different pipeline with different infrastructure needs. | Store `thumbnail_url` as metadata only. Extract audio from video via `yt-dlp`. All downstream analysis operates on text in `body_text`. |
| AF8 | **Mobile app or consumer-facing UI** | ThinkTank is infrastructure, not a consumer product. The admin dashboard is for operators, not end users. Building consumer UX before the pipeline is proven is premature optimization of the wrong layer. | HTMX admin dashboard for operational oversight. API for programmatic access. Any consumer interface is a separate product built on ThinkTank's API. |
| AF9 | **Notification system (email, Slack, webhooks)** | Admin checks the dashboard. LLM handles routine oversight. Building notification infrastructure (email templates, Slack integration, webhook delivery) is significant work that serves a single admin user. | Dashboard banners for urgent issues (LLM offline, GPU failures). LLM daily digest is the primary "notification." Push notifications deferred. |
| AF10 | **Advanced blog and academic paper scraping** | HTML extraction from arbitrary websites and arXiv paper parsing are each significant engineering challenges with different edge cases from podcast RSS. Mixing them into V1 dilutes focus. | Support Substack (RSS-based, straightforward) and blog RSS if available. Full HTML extraction and arXiv parsing are explicitly Phase 2. |
| AF11 | **Custom transcription model fine-tuning** | Parakeet TDT 1.1B works well out of the box (~6% WER). Fine-tuning on domain-specific vocabulary (names, technical terms) could improve accuracy but requires training infrastructure, labeled data, and model management. | Use Parakeet as-is. If specific terms are consistently misrecognized, address via post-processing text correction rather than model fine-tuning. |
| AF12 | **Third-party YouTube transcript services** | YouTube captions are used as a first-pass transcription source for own channels only. Integrating third-party transcript APIs adds cost, API dependencies, and legal considerations for minimal V1 value. | Use `yt-dlp --write-auto-sub` for own channels. If podcast coverage is insufficient (evaluated Month 2), then investigate third-party services. |

---

## Feature Dependencies

```
T13 (Alembic migrations) --> T9 (Schema/data model) --> T5 (Job queue)
                                                     --> T14 (Source approval)
                                                     --> T3 (URL dedup) --> T4 (Fingerprint dedup)

T5 (Job queue) --> T6 (Stale reclamation)
               --> T7 (Rate limiting)
               --> T8 (Content filtering)
               --> T1 (RSS polling)
               --> T2 (Transcription pipeline)

T1 (RSS polling) --> T2 (Transcription pipeline) --> D5 (GPU scaling)
                 --> D7 (Content attribution)

T14 (Source approval) --> D1 (LLM Supervisor)
                      --> D2 (Cascade discovery)
                      --> D13 (Guest discovery)

D1 (LLM Supervisor) --> D11 (LLM fallback)
                     --> D12 (LLM audit trail)
                     --> D2 (Cascade discovery)

D9 (Admin dashboard) --> D10 (Cost tracking)
                     --> D12 (LLM audit trail)
                     --> D14 (Kill switch)

T10 (Health endpoints) --> T11 (Structured logging)
```

### Critical Path

The critical path to "first transcript in the database" is:

```
T13 --> T9 --> T5 --> T1 --> T2
```

Schema, data model, job queue, RSS polling, transcription. Everything else builds on top of this spine.

### LLM Supervisor Dependency

The LLM Supervisor (D1) is a dependency for thinker approval, source approval, and candidate promotion. However, the spec specifies that the initial LLM review happens during bootstrap (Step 5), which means D1 must be functional before workers activate. This makes D1 part of the critical path to "system operational," even though the basic pipeline can function with manual approval as a fallback.

---

## MVP Recommendation

### Phase 1: Foundation (Must Ship First)

Prioritize the critical path -- get content flowing through the pipeline:

1. **T13** - Alembic migrations and full schema
2. **T9** - Complete data model (thinkers, sources, content, categories)
3. **T5** - Job queue with priority, retry, and `SELECT FOR UPDATE SKIP LOCKED`
4. **T6** - Stale job reclamation
5. **T12** - Configuration system (env vars + `system_config` table)
6. **T10/T11** - Health endpoints and structured logging
7. **T1** - RSS feed polling and episode extraction
8. **T3/T4** - URL normalization and content fingerprinting (dedup layers 1-2)
9. **T8** - Content filtering (duration + title patterns)

### Phase 2: Transcription + LLM Core

The two most complex subsystems, but both are essential for autonomous operation:

1. **T2** - Multi-pass transcription pipeline (YouTube captions, existing transcripts, Parakeet)
2. **D5** - GPU on-demand scaling via Railway API
3. **T14** - Source approval workflow
4. **D1** - LLM Supervisor (approval track: thinker, source, candidate)
5. **D11** - LLM fallback and timeout escalation
6. **D12** - LLM audit trail

### Phase 3: Autonomous Growth

Features that make the system self-expanding:

1. **D2** - Cascade discovery of candidate thinkers
2. **D13** - Podcast guest discovery (Listen Notes + Podcast Index)
3. **D7** - Content attribution with confidence scoring
4. **D6** - 3-layer dedup (add trigram similarity for candidates)
5. **T7** - Rate limiting coordination between workers
6. **D8** - Backpressure mechanism
7. **D3** - Tiered refresh scheduling

### Phase 4: Operational Excellence

The operator experience that makes the system manageable:

1. **D9** - Admin dashboard (queue depth, error log, source health, GPU status)
2. **D10** - API cost tracking and budget visualization
3. **D14** - Global kill switch and operational controls
4. **D4** - Category taxonomy visualization and management
5. **T15** - Full API for programmatic access
6. **D1** (extended) - Scheduled health checks, daily digests, weekly audits

### Defer

- **AF3 (Speaker diarization)**: Month 2. Depends on proven transcription pipeline.
- **AF4 (pgvector embeddings)**: Future retrieval milestone.
- **AF2 (Claim extraction)**: Future analysis milestone. The entire point of ThinkTank V1 is to build the corpus this will operate on.
- **AF10 (Blog/paper scraping)**: Phase 2 per spec. Different edge cases from podcast RSS.

---

## Sources

- [Northflank: Best Open Source STT Models 2026](https://northflank.com/blog/best-open-source-speech-to-text-stt-model-in-2026-benchmarks) - Parakeet TDT benchmarks
- [NVIDIA Parakeet V2 vs Whisper](https://medium.com/data-science-in-your-pocket/nvidia-parakeet-v2-vs-openai-whisper-which-is-the-best-asr-ai-model-5912cb778dcf) - WER comparison
- [PostgreSQL SKIP LOCKED Job Queue](https://www.dbpro.app/blog/postgresql-skip-locked) - DB-backed queue patterns
- [Postgres as Queue up to 50k jobs/sec](https://medium.com/@harsh.vaghela.work/postgres-is-the-only-queue-you-need-until-50k-jobs-sec-5931611b551c) - Performance ceiling
- [Potent Pages: Deduplication and Canonicalization](https://potentpages.com/web-crawler-development/web-crawlers-and-hedge-funds/deduplication-canonicalization-preventing-double-counts-and-phantom-signals) - Multi-layer dedup best practices
- [AssemblyAI Speaker Diarization Update](https://www.assemblyai.com/blog/speaker-diarization-update) - Diarization state-of-art (validates deferral decision)
- [LLManager by LangChain](https://github.com/langchain-ai/llmanager) - LLM-as-approval-manager pattern
- [Listen Notes API](https://www.listennotes.com/api/) - Podcast discovery API capabilities
- [Podcast Index vs Taddy](https://taddy.org/blog/podcastindex-vs-taddy-podcast-api) - Podcast API comparison
- [Current Challenges in Podcast Information Access](https://arxiv.org/pdf/2106.09227) - Academic survey of podcast IR challenges
- [Mapping the Podcast Ecosystem](https://arxiv.org/html/2411.07892v1) - Structured podcast research corpus
