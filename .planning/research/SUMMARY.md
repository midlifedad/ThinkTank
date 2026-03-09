# Project Research Summary

**Project:** ThinkTank - Global Intelligence Ingestion Platform
**Domain:** Continuous content ingestion, transcription, and knowledge capture infrastructure
**Researched:** 2026-03-08
**Confidence:** HIGH

## Executive Summary

ThinkTank is a pipeline-oriented, job-driven ingestion system that continuously discovers, fetches, and transcribes expert audio content (primarily podcasts) into a structured PostgreSQL corpus. Experts build systems like this as multi-stage pipelines where each unit of work is a database-backed job, services communicate exclusively through shared database state (no message brokers), and an LLM governance layer prevents corpus pollution. The stack is Python 3.12 / FastAPI / SQLAlchemy 2.0 / PostgreSQL 16, deployed as four Railway services (API, CPU Worker, GPU Worker, Admin Dashboard) sharing a single database. The recommended transcription engine is NVIDIA Parakeet TDT 1.1B running on an on-demand Railway L4 GPU, with an LLM Supervisor (Claude) governing all corpus expansion decisions.

The recommended approach follows a strict dependency chain: database schema and job queue infrastructure first, then RSS/feed ingestion and deduplication, then the GPU transcription pipeline, then LLM governance, and finally the admin dashboard and autonomous discovery features. This order is dictated by clear architectural dependencies -- nothing works without the schema, nothing processes without the job queue, nothing transcribes without content, and nothing grows autonomously without governance. The "everything is a job" pattern using PostgreSQL `SELECT FOR UPDATE SKIP LOCKED` is well-proven and eliminates the need for Redis or external message brokers.

The key risks are: (1) feedparser hangs that silently consume worker slots -- mitigated by fetching RSS via httpx with explicit timeouts before passing to feedparser, (2) PostgreSQL connection pool exhaustion under concurrent workers -- mitigated by separating claim and execution pools and committing immediately after job claiming, (3) LLM Supervisor cost spiral from unbounded context snapshots -- mitigated by capping context sizes and implementing prompt budgeting from day one, (4) GPU cold start costs exceeding estimates due to the heavyweight NeMo container -- mitigated by raising queue thresholds and extending idle timeouts, and (5) concurrent Alembic migrations corrupting schema state -- mitigated by wrapping migrations in a PostgreSQL advisory lock.

## Key Findings

### Recommended Stack

The stack is entirely Python-based with high confidence across all choices. FastAPI provides the async-native API framework with Pydantic validation. SQLAlchemy 2.0 with asyncpg is the database layer, chosen for its async support and 3-4x lower latency compared to psycopg in async workloads. The job queue is custom-built on PostgreSQL's `SELECT FOR UPDATE SKIP LOCKED` (no Celery, no Redis -- explicitly prohibited). HTMX + Jinja2 handles the admin dashboard, eliminating the entire frontend build pipeline.

**Core technologies:**
- **Python 3.12 + FastAPI + Pydantic 2**: Async API framework with built-in validation. Avoid 3.13+ until NeMo ecosystem catches up.
- **SQLAlchemy 2.0 + asyncpg + Alembic**: Async ORM with the fastest PostgreSQL driver and standard migration tooling.
- **Parakeet TDT 1.1B (via NeMo)**: RTFx >2,000, ~4GB VRAM on L4. 5x faster than Canary Qwen 2.5B for batch transcription. Consider Canary for accuracy-critical thinkers post-backfill.
- **httpx**: Async HTTP client for all external API calls (RSS, Listen Notes, Podcast Index, YouTube).
- **HTMX + Jinja2 + TailwindCSS (CDN)**: Server-rendered admin dashboard. No build step, no JS framework.
- **structlog**: Structured JSON logging with context binding for correlation IDs.
- **ruff + mypy + uv**: Modern toolchain -- ruff replaces Black/Flake8/isort, uv replaces pip/poetry.
- **Anthropic SDK**: Claude for LLM Supervisor decisions.

**Critical version pins:**
- yt-dlp: Pin to 2025.12.08 (2026.03.03 has DASH audio regression)
- NeMo: Pin exact version in GPU container (ML frameworks break between minor versions)

### Expected Features

**Must have (table stakes):**
- T1: RSS feed polling and episode extraction (core ingestion path)
- T2: Multi-pass transcription pipeline (YouTube captions -> existing transcripts -> Parakeet GPU)
- T3/T4: URL normalization + content fingerprinting (3-layer dedup, layers 1-2)
- T5: DB-backed job queue with priority, retry, `SELECT FOR UPDATE SKIP LOCKED`
- T6: Stale job reclamation (reclaim stuck `running` jobs)
- T7: Rate limiting for external APIs (sliding-window counter in Postgres)
- T8: Content filtering (duration + title patterns to exclude trailers/promos)
- T9: Thinker-source-content data model hierarchy
- T10/T11: Health endpoints and structured logging
- T12/T13: Environment-based configuration and Alembic migrations

**Should have (differentiators):**
- D1: LLM Supervisor governing all corpus expansion (the single most differentiating feature)
- D2: Cascade discovery of candidate thinkers (system grows itself)
- D5: On-demand GPU scaling via Railway API (50-70% GPU cost reduction)
- D9: Admin dashboard with LLM decision panel (human oversight layer)
- D13: Podcast guest discovery via Listen Notes + Podcast Index (total capture)

**Defer (v2+):**
- Speaker diarization (AF3) -- adds significant pipeline complexity, Parakeet does not include it
- pgvector embeddings and semantic search (AF4) -- no retrieval interface in v1
- Claim/opinion extraction (AF2) -- the entire point of v1 is to build the corpus this operates on
- Advanced blog/paper scraping (AF10) -- different edge cases from podcast RSS
- Custom transcription model fine-tuning (AF11) -- Parakeet works well out of the box

### Architecture Approach

Four Railway services sharing a single PostgreSQL database, following the "everything is a job" principle. Services communicate exclusively through database state -- no direct inter-service RPC, no message brokers. The CPU Worker is the system's heartbeat (always-on, 4-6 concurrent async tasks handling discovery, fetching, LLM calls, and GPU orchestration). The GPU Worker is single-purpose (Parakeet transcription only, on-demand scaling). The LLM Supervisor is a logical component co-located on the CPU Worker, not a separate service.

**Major components:**
1. **API Service** -- HTTP interface, Alembic migration runner (schema owner, deploy first)
2. **CPU Worker** -- All non-GPU job processing: discovery, fetching, LLM calls, attribution, GPU orchestration (always-on)
3. **GPU Worker** -- Audio transcription via Parakeet TDT 1.1B only (on-demand L4, model stays in VRAM)
4. **Admin Dashboard** -- HTMX human oversight interface, private networking only
5. **PostgreSQL** -- Single source of truth AND coordination layer (job queue, rate limiting, config, audit trail)
6. **LLM Supervisor (logical)** -- Governance over corpus expansion, physically runs on CPU Worker

**Key architectural boundaries:**
- Workers never call the API service
- API service never processes jobs
- GPU worker only handles `process_content`
- External API calls are always rate-limited
- All LLM decisions are logged
- Audio is never persisted

### Critical Pitfalls

1. **Feedparser hangs indefinitely on malformed feeds** -- Fetch RSS via httpx with explicit 60s timeout, then pass raw bytes to feedparser for parsing. Set `socket.setdefaulttimeout(30)` as a global safety net. This is day-one infrastructure.

2. **PostgreSQL connection pool exhaustion** -- Separate pools for job claiming (short transactions) vs. execution (long). Set `pool_size=10, max_overflow=5, pool_pre_ping=True`. Commit immediately after `SELECT FOR UPDATE SKIP LOCKED` -- never hold the lock while processing.

3. **LLM Supervisor cost spiral** -- Cap context snapshots (max 50 thinkers, 100 errors, 20 candidates per review). Use aggregated summaries, not full row dumps. Track `tokens_used` per review type, alert at 2x baseline. Implement prompt budgeting before sending.

4. **Concurrent Alembic migrations** -- Wrap in `pg_advisory_lock(1)`. Only run from API service. Workers check migration currency on startup and fail-fast if behind.

5. **GPU cold start exceeds 2-5 min estimate** -- Realistic total is 3-6 minutes (provisioning + container + NeMo imports + model load + CUDA warmup). Raise `gpu_queue_threshold` to 15-20, extend idle timeout to 60 minutes.

## Implications for Roadmap

Based on combined research, the architecture has clear dependency chains that dictate a 6-phase build order.

### Phase 1: Foundation Layer
**Rationale:** Every other component reads/writes the database. Nothing can be tested without models, migrations, and configuration. The schema IS the architecture.
**Delivers:** Database schema, SQLAlchemy models, Alembic migrations, configuration system, structured logging, health endpoints, project scaffolding (FastAPI app, Docker setup, CI).
**Addresses:** T9, T10, T11, T12, T13
**Avoids:** Pitfall #6 (concurrent Alembic migrations -- advisory lock from day one), Pitfall #2 (connection pool design -- get pool configuration right here)

### Phase 2: Job Queue Infrastructure
**Rationale:** Every job handler depends on the queue infrastructure. Building this before any specific handler ensures a solid foundation. The `SELECT FOR UPDATE SKIP LOCKED` pattern and worker architecture must be correct before any jobs run.
**Delivers:** Job claim/complete/fail primitives, async worker base loop, stale job reclamation, rate limit coordination, backpressure mechanism.
**Addresses:** T5, T6, T7, T8, D8, D14 (kill switch)
**Avoids:** Pitfall #8 (jobs table bloat -- partial index from day one), Pitfall #14 (priority starvation -- floor priorities per job type), Pitfall #2 (connection pool -- separate claim vs execution pools)

### Phase 3: Content Ingestion Pipeline
**Rationale:** Feed fetching is the primary content source and the first real test of the job queue. Dedup and filtering must work before content flows to transcription. This is the critical path to "first content in the database."
**Delivers:** RSS feed polling, content dedup (URL normalization + fingerprinting), content filtering (duration + title patterns), source approval workflow, discovery orchestration.
**Addresses:** T1, T3, T4, T8, T14, T15 (basic API endpoints)
**Avoids:** Pitfall #1 (feedparser hangs -- httpx fetch with timeout, then feedparser parse), Pitfall #7 (RSS date parsing -- normalization with fallbacks), Pitfall #4 (fingerprint collisions -- source-aware fingerprinting + audit logging), Pitfall #15 (RSS pagination -- detection and logging)

### Phase 4: Transcription Pipeline
**Rationale:** Transcription depends on content rows existing (Phase 3). The GPU worker is a separate deployment target with its own Docker image (nvcr.io/nvidia/nemo:24.05) and unique infrastructure concerns (VRAM, model caching, audio temp files).
**Delivers:** Three-pass transcription (YouTube captions, existing transcripts, Parakeet), GPU worker service, GPU on-demand scaling via Railway API, audio download + ffmpeg conversion + cleanup.
**Addresses:** T2, D5
**Avoids:** Pitfall #5 (GPU cold start costs -- higher queue threshold, longer idle timeout, minimal Docker image), Pitfall #9 (CDN rate limiting -- per-domain download limits, respectful User-Agent), Pitfall #16 (ffmpeg edge cases -- output verification), Pitfall #13 (yt-dlp breakage -- version pinning)

### Phase 5: LLM Governance
**Rationale:** The LLM Supervisor adds governance to an already-working pipeline. Building the pipeline first (Phases 2-4) allows testing without LLM dependency, then adding the governance layer. However, D1 is part of the critical path to "system operational" because initial thinker/source approval requires it.
**Delivers:** LLM Supervisor with structured JSON prompts, approval flow (thinkers, sources, candidates), audit trail (llm_reviews), fallback/escalation on Anthropic API outage, scheduled checks (health, daily digest, weekly audit).
**Addresses:** D1, D11, D12
**Avoids:** Pitfall #3 (LLM cost spiral -- bounded context snapshots, prompt budgeting, monthly cost cap, token tracking per review type)

### Phase 6: Autonomous Growth and Operational Excellence
**Rationale:** These features compound value but are not required for the core ingestion loop. The system is useful with just approved thinkers, feed fetching, and transcription. Autonomous discovery and the admin dashboard make it self-expanding and manageable.
**Delivers:** Cascade discovery, guest discovery (Listen Notes + Podcast Index), content attribution, trigram dedup for candidates, tiered refresh scheduling, admin dashboard (HTMX), API cost tracking, category taxonomy management.
**Addresses:** D2, D3, D4, D6, D7, D9, D10, D13
**Avoids:** Pitfall #11 (candidate name dedup -- raised trigram threshold for short names, LLM review before auto-merge), Pitfall #12 (Listen Notes quota -- monthly quota tracking, Podcast Index as parallel path, per-tier prioritization)

### Phase Ordering Rationale

- **Dependency chain is strict:** Schema -> Job Queue -> Content Ingestion -> Transcription -> Governance -> Autonomous Features. Each layer depends on the one before it.
- **Critical path to "first transcript":** T13 -> T9 -> T5 -> T1 -> T2 (Phases 1-4). This must be buildable and testable without the LLM or admin dashboard.
- **LLM Supervisor is deliberately late (Phase 5):** This allows the entire pipeline to be tested with manual approvals before introducing LLM dependency. The governance layer wraps a working pipeline, it does not replace core functionality.
- **Admin dashboard is last (Phase 6):** It is a read-heavy view over existing data. It does not block pipeline development. Building it last means it can display real data from the working pipeline.
- **Autonomous discovery is last (Phase 6):** The system must work for manually-curated thinkers before it learns to grow itself. Cascade discovery and guest search are high-value but not foundational.

### Research Flags

Phases likely needing deeper research during planning:
- **Phase 4 (Transcription):** Railway GPU-specific scaling API needs exploration -- the GraphQL mutation names for replica scaling are not documented publicly and must be discovered via Railway's GraphiQL playground. NeMo container optimization (building a minimal inference image vs. using the full training image) needs prototyping.
- **Phase 5 (LLM Governance):** LLM-as-governance-gate is an emerging pattern without established best practices. Prompt engineering for structured approval decisions, context budgeting strategies, and the exact fallback behavior when Anthropic API is down need design work during planning.
- **Phase 6 (Autonomous Discovery):** Listen Notes and Podcast Index API integration details, rate limit behavior under load, and the cascade discovery algorithm (name extraction, dedup, promotion threshold) need API-level research.

Phases with standard patterns (skip research-phase):
- **Phase 1 (Foundation):** FastAPI + SQLAlchemy + Alembic + structlog is the most well-documented Python web stack. Established patterns everywhere.
- **Phase 2 (Job Queue):** PostgreSQL `SELECT FOR UPDATE SKIP LOCKED` for job queues is proven at scale by Solid Queue, PGQueuer, and Procrastinate. Multiple production reference implementations exist.
- **Phase 3 (Content Ingestion):** RSS parsing with feedparser, URL normalization, and content fingerprinting are standard web crawling patterns with extensive documentation.

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | HIGH | All technologies verified against official docs and PyPI. Versions confirmed current. No speculative choices -- every tool has a clear rationale and production track record. |
| Features | HIGH | Feature landscape grounded in spec analysis and podcast ingestion domain research. Critical path clearly identified. Anti-features well-justified. |
| Architecture | HIGH | Pipeline-of-autonomous-stages pattern is well-proven (Solid Queue, PGQueuer, Procrastinate). Component boundaries are clean. Data flow diagrams are concrete. One MEDIUM area: Railway GPU scaling API specifics need exploration. |
| Pitfalls | HIGH | 16 pitfalls identified with specific prevention and detection strategies. Critical pitfalls backed by documented issues (feedparser GitHub issues, SQLAlchemy async pool issues, Alembic concurrent migration failures). Two MEDIUM-confidence items: Railway GPU cold start timing and content fingerprint collision rates. |

**Overall confidence:** HIGH

### Gaps to Address

- **Railway GPU scaling API:** The exact GraphQL mutations for scaling GPU service replicas need to be discovered via Railway's GraphiQL playground. The pattern is sound but the implementation details are unverified. Address during Phase 4 planning by prototyping the API calls.
- **NeMo container cold start time:** The 3-6 minute estimate is based on comparable setups, not Railway-specific measurements. Run a cold start test on Railway L4 early in Phase 4 to calibrate `gpu_queue_threshold` and `gpu_idle_minutes_before_shutdown`.
- **LLM prompt design for governance decisions:** The structured JSON prompt format for thinker/source/candidate approval needs design and testing with Claude. Prompt budgeting thresholds (max tokens per review type) need empirical calibration. Address during Phase 5 planning.
- **Listen Notes free tier adequacy:** Whether 10K requests/month is sufficient depends on backfill depth and thinker count. If the initial thinker list exceeds ~30, plan for the paid tier ($50/mo) from the start. Validate during Phase 6 planning.
- **Content fingerprint false positive rate:** The collision vectors identified (same guest on multiple shows, multi-part episodes) are inferred, not measured. Build fingerprint collision logging from day one (Phase 3) and audit false positive rates before relying on fingerprints for silent dedup.

## Sources

### Primary (HIGH confidence)
- [PostgreSQL SKIP LOCKED patterns](https://www.inferable.ai/blog/posts/postgres-skip-locked) -- job queue architecture
- [FastAPI PyPI](https://pypi.org/project/fastapi/), [SQLAlchemy 2.0 docs](https://docs.sqlalchemy.org/en/20/) -- stack versions
- [NVIDIA Parakeet TDT 1.1B](https://huggingface.co/nvidia/parakeet-tdt-1.1b) -- transcription model specs
- [Northflank ASR Benchmarks 2026](https://northflank.com/blog/best-open-source-speech-to-text-stt-model-in-2026-benchmarks) -- Parakeet vs Canary comparison
- [feedparser GitHub issues #76, #263, #245](https://github.com/kurtmckee/feedparser/issues/76) -- feedparser hang documentation
- [SQLAlchemy asyncpg connection leak #6652](https://github.com/sqlalchemy/sqlalchemy/issues/6652) -- pool exhaustion documentation
- [Vlad Mihalcea: Database job queue](https://vladmihalcea.com/database-job-queue-skip-locked/) -- SKIP LOCKED patterns
- [PGQueuer](https://github.com/janbjorge/pgqueuer), [Procrastinate](https://github.com/procrastinate-org/procrastinate) -- reference implementations

### Secondary (MEDIUM confidence)
- [Railway Scaling docs](https://docs.railway.com/reference/scaling), [Railway Public API](https://docs.railway.com/integrations/api) -- GPU scaling (mutation names need exploration)
- [Anthropic API pricing analysis](https://www.finout.io/blog/anthropic-api-pricing) -- LLM cost estimation
- [MDPI: Airflow-orchestrated podcast transcription](https://www.mdpi.com/3042-6308/2/1/1) -- pipeline architecture reference
- [ZenML: LLM governance patterns](https://www.zenml.io/blog/what-1200-production-deployments-reveal-about-llmops-in-2025) -- LLM-in-the-loop patterns
- [Listen Notes API](https://www.listennotes.com/api/) -- podcast discovery capabilities
- [yt-dlp DASH regression #16128](https://github.com/yt-dlp/yt-dlp/issues/16128) -- version pinning rationale

### Tertiary (LOW confidence)
- Railway GPU cold start timing -- inferred from comparable platforms, not measured on Railway L4
- Content fingerprint collision rates -- inferred from podcast metadata patterns, not measured on real data

---
*Research completed: 2026-03-08*
*Ready for roadmap: yes*
