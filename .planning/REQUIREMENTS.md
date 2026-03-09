# Requirements: ThinkTank

**Defined:** 2026-03-08
**Core Value:** Total capture of expert knowledge from every source where they've published, starting with long-form audio where thinkers are least polished and most revealing.

## v1 Requirements

Requirements for initial release. Each maps to roadmap phases.

### Foundation

- [ ] **FNDTN-01**: PostgreSQL schema with all 14 tables (categories, thinkers, sources, content, jobs, llm_reviews, system_config, rate_limit_usage, api_usage, content_thinkers, candidate_thinkers, thinker_profiles, thinker_metrics, thinker_categories) deployed via Alembic migration
- [ ] **FNDTN-02**: SQLAlchemy 2.0 async models for all tables with relationship mappings
- [ ] **FNDTN-03**: Alembic migration system with advisory lock to prevent concurrent migration corruption
- [ ] **FNDTN-04**: Environment-based configuration with DB override precedence (env vars > system_config table > code defaults)
- [ ] **FNDTN-05**: Structured JSON logging with correlation IDs, service name, job ID on every log entry
- [ ] **FNDTN-06**: Health endpoint per service returning 200 when DB connected and worker loop running
- [ ] **FNDTN-07**: FastAPI application scaffold with async lifespan, connection pool configuration, and CORS
- [ ] **FNDTN-08**: Project toolchain setup (uv, ruff, mypy, pytest, pre-commit) with CI enforcement
- [ ] **FNDTN-09**: Docker configuration for all 4 Railway services (API, CPU Worker, GPU Worker, Admin)

### Job Queue

- [ ] **QUEUE-01**: DB-backed job queue using `SELECT FOR UPDATE SKIP LOCKED` with priority ordering
- [ ] **QUEUE-02**: Async worker base loop that claims and dispatches jobs by type with configurable concurrency
- [ ] **QUEUE-03**: Job retry with exponential backoff and per-type max attempt limits
- [ ] **QUEUE-04**: Stale job reclamation running every 5 minutes, returning stuck `running` jobs to `queued`
- [ ] **QUEUE-05**: Rate limit coordination via `rate_limit_usage` table with sliding-window queries across concurrent workers
- [ ] **QUEUE-06**: Backpressure mechanism demoting discovery priority when transcription queue exceeds threshold
- [ ] **QUEUE-07**: Global kill switch (`workers_active = false` in system_config) halting all job claiming
- [ ] **QUEUE-08**: Error categorization with closed set of error categories on failed jobs

### Content Ingestion

- [ ] **INGEST-01**: RSS feed polling via httpx (with 60s timeout) + feedparser for XML parsing, extracting episodes as content rows
- [ ] **INGEST-02**: URL normalization (strip UTMs, force HTTPS, canonicalize YouTube IDs) with `canonical_url` unique constraint
- [ ] **INGEST-03**: Content fingerprinting via `sha256(title + date + duration)` catching cross-platform duplicates
- [ ] **INGEST-04**: Content filtering by minimum duration (default 600s) and skip title patterns, with per-source overrides
- [ ] **INGEST-05**: Source approval workflow — workers only process sources with `approval_status = 'approved'`
- [ ] **INGEST-06**: Tier-based refresh scheduling (Tier 1: 6h, Tier 2: 24h, Tier 3: 168h) via `refresh_due_sources` check
- [ ] **INGEST-07**: Discovery orchestration job that coordinates feed checks across all approved sources

### Transcription

- [ ] **TRANS-01**: Three-pass transcription pipeline: YouTube captions first, existing transcripts second, Parakeet GPU inference last
- [ ] **TRANS-02**: GPU Worker service running Parakeet TDT 1.1B on Railway L4, model persisted in VRAM across jobs
- [ ] **TRANS-03**: Audio download via yt-dlp (pinned to 2025.12.08) with ffmpeg conversion to 16kHz WAV
- [ ] **TRANS-04**: On-demand GPU scaling via Railway API — spin up when queue > threshold, shut down after idle timeout
- [ ] **TRANS-05**: Audio temp file cleanup after transcription (audio never persisted to storage)
- [ ] **TRANS-06**: Transcription output stored in `content.body_text` with metadata (word count, duration, source pass used)

### LLM Governance

- [ ] **GOV-01**: LLM Supervisor using Claude claude-sonnet-4-20250514 with structured JSON prompts for all corpus expansion decisions
- [ ] **GOV-02**: Thinker approval flow — new thinkers reviewed by LLM with context snapshot before activation
- [ ] **GOV-03**: Source approval flow — new sources reviewed by LLM before RSS polling begins
- [ ] **GOV-04**: Candidate thinker review — batch review of candidates exceeding appearance threshold
- [ ] **GOV-05**: Full audit trail in `llm_reviews` table with context snapshot, prompt, raw response, parsed decision, reasoning
- [ ] **GOV-06**: Fallback and timeout escalation — pending approvals escalate to human review after `llm_timeout_hours` (default 2h)
- [ ] **GOV-07**: Graceful degradation when Anthropic API unavailable — existing pipeline continues, new approvals queue for human
- [ ] **GOV-08**: Scheduled health checks, daily digests, and weekly corpus audits via LLM
- [ ] **GOV-09**: Context budgeting — bounded context snapshots (max 50 thinkers, 100 errors, 20 candidates per review)

### Discovery

- [ ] **DISC-01**: Cascade discovery — scan episode titles/descriptions for names not in thinkers table, surface as candidates after 3+ appearances
- [ ] **DISC-02**: Guest discovery via Listen Notes and Podcast Index APIs with rate-limited queries
- [ ] **DISC-03**: Content attribution via `content_thinkers` junction with role (host/guest/panelist/mentioned) and confidence scoring (1-10)
- [ ] **DISC-04**: Trigram similarity dedup (`pg_trgm`) for candidate thinker names at 0.7 threshold
- [ ] **DISC-05**: Daily quota limits on candidate discovery to prevent unbounded growth
- [ ] **DISC-06**: Candidate-to-thinker promotion flow triggered by LLM batch review approval

### Operations

- [ ] **OPS-01**: Admin dashboard (HTMX + FastAPI) showing queue depth, error log, source health, GPU status
- [ ] **OPS-02**: LLM decision panel — view pending approvals, recent decisions, override with audit trail
- [ ] **OPS-03**: API cost tracking via `api_usage` table with hourly rollups and estimated USD costs
- [ ] **OPS-04**: Rate limit gauges showing current usage vs configured limits per external API
- [ ] **OPS-05**: Category taxonomy management in admin dashboard
- [ ] **OPS-06**: Bootstrap sequence — seed categories, initial thinkers, trigger first LLM review, activate workers

### API

- [ ] **API-01**: RESTful endpoints for thinkers (CRUD, list with filtering by category/tier/status)
- [ ] **API-02**: RESTful endpoints for sources (list by thinker, approval status filtering)
- [ ] **API-03**: RESTful endpoints for content (list by source/thinker, pagination, status filtering)
- [ ] **API-04**: Job queue status endpoint (counts by type/status, recent errors)
- [ ] **API-05**: System config read/write endpoints for operational parameters
- [ ] **API-06**: OpenAPI auto-generated documentation

### Quality Standards

- [ ] **QUAL-01**: Test suite following STANDARDS.md pyramid — unit tests (pure logic), integration tests (real Postgres), E2E tests (full system flow)
- [ ] **QUAL-02**: Factory functions for all domain objects with sensible defaults and overridable fields
- [ ] **QUAL-03**: Contract tests for every API endpoint (request/response shape, status codes, error formats)
- [ ] **QUAL-04**: Contract tests for every job handler (given input payload, expected side effects)
- [ ] **QUAL-05**: Operations runbook covering bootstrap, post-deploy verification, rollback, and common problem resolution
- [ ] **QUAL-06**: Architecture documentation with data flow diagrams and service boundaries
- [ ] **QUAL-07**: Development guide covering how to add new job types, new API endpoints, and new thinker categories

## v2 Requirements

Deferred to future release. Tracked but not in current roadmap.

### Knowledge Extraction

- **KNOW-01**: Claim and opinion extraction from transcripts via LLM
- **KNOW-02**: Research source citation identification and linking
- **KNOW-03**: Cross-thinker claim comparison and contradiction detection

### Content Expansion

- **EXPN-01**: Speaker diarization in transcripts (NeMo MSDD)
- **EXPN-02**: Blog and academic paper scraping (HTML extraction, arXiv parsing)
- **EXPN-03**: pgvector embeddings for semantic search across transcripts

### Retrieval

- **RETR-01**: Query/retrieval interface for searching ingested knowledge
- **RETR-02**: Semantic search across transcripts via embeddings
- **RETR-03**: Thinker expertise profiling and cross-referencing

## Out of Scope

Explicitly excluded. Documented to prevent scope creep.

| Feature | Reason |
|---------|--------|
| Query/retrieval interface | Knowledge access layer is a separate future milestone |
| Claim/opinion extraction | Downstream analysis built on top of completed corpus |
| Speaker diarization | Adds significant pipeline complexity; Parakeet TDT 1.1B doesn't include it |
| pgvector embeddings | No retrieval interface in v1 to consume them |
| Real-time streaming ingestion | Batch/poll-based sufficient for podcast publishing cadences |
| Multi-tenant access control | Single-owner system for now |
| Mobile app or consumer UI | ThinkTank is infrastructure, not a consumer product |
| Non-text content (images, video frames, PDFs) | Text-first strategy; other modalities later |
| Email/Slack/webhook notifications | Dashboard banners + LLM digests sufficient for single admin |
| Custom transcription model fine-tuning | Parakeet works well out of the box (~6% WER) |
| Third-party YouTube transcript services | yt-dlp auto-subs sufficient for v1 |

## Traceability

Which phases cover which requirements. Updated during roadmap creation.

| Requirement | Phase | Status |
|-------------|-------|--------|
| FNDTN-01 | Phase 1 | Pending |
| FNDTN-02 | Phase 1 | Pending |
| FNDTN-03 | Phase 1 | Pending |
| FNDTN-04 | Phase 1 | Pending |
| FNDTN-05 | Phase 1 | Pending |
| FNDTN-06 | Phase 1 | Pending |
| FNDTN-07 | Phase 1 | Pending |
| FNDTN-08 | Phase 1 | Pending |
| FNDTN-09 | Phase 1 | Pending |
| QUEUE-01 | Phase 2 | Pending |
| QUEUE-02 | Phase 2 | Pending |
| QUEUE-03 | Phase 2 | Pending |
| QUEUE-04 | Phase 2 | Pending |
| QUEUE-05 | Phase 2 | Pending |
| QUEUE-06 | Phase 2 | Pending |
| QUEUE-07 | Phase 2 | Pending |
| QUEUE-08 | Phase 2 | Pending |
| INGEST-01 | Phase 3 | Pending |
| INGEST-02 | Phase 3 | Pending |
| INGEST-03 | Phase 3 | Pending |
| INGEST-04 | Phase 3 | Pending |
| INGEST-05 | Phase 3 | Pending |
| INGEST-06 | Phase 3 | Pending |
| INGEST-07 | Phase 3 | Pending |
| TRANS-01 | Phase 4 | Pending |
| TRANS-02 | Phase 4 | Pending |
| TRANS-03 | Phase 4 | Pending |
| TRANS-04 | Phase 4 | Pending |
| TRANS-05 | Phase 4 | Pending |
| TRANS-06 | Phase 4 | Pending |
| GOV-01 | Phase 5 | Pending |
| GOV-02 | Phase 5 | Pending |
| GOV-03 | Phase 5 | Pending |
| GOV-04 | Phase 5 | Pending |
| GOV-05 | Phase 5 | Pending |
| GOV-06 | Phase 5 | Pending |
| GOV-07 | Phase 5 | Pending |
| GOV-08 | Phase 5 | Pending |
| GOV-09 | Phase 5 | Pending |
| DISC-01 | Phase 6 | Pending |
| DISC-02 | Phase 6 | Pending |
| DISC-03 | Phase 3 | Pending |
| DISC-04 | Phase 3 | Pending |
| DISC-05 | Phase 6 | Pending |
| DISC-06 | Phase 5 | Pending |
| OPS-01 | Phase 7 | Pending |
| OPS-02 | Phase 7 | Pending |
| OPS-03 | Phase 7 | Pending |
| OPS-04 | Phase 7 | Pending |
| OPS-05 | Phase 7 | Pending |
| OPS-06 | Phase 7 | Pending |
| API-01 | Phase 7 | Pending |
| API-02 | Phase 7 | Pending |
| API-03 | Phase 7 | Pending |
| API-04 | Phase 7 | Pending |
| API-05 | Phase 7 | Pending |
| API-06 | Phase 7 | Pending |
| QUAL-01 | Phase 1 | Pending |
| QUAL-02 | Phase 1 | Pending |
| QUAL-03 | Phase 7 | Pending |
| QUAL-04 | Phase 2 | Pending |
| QUAL-05 | Phase 7 | Pending |
| QUAL-06 | Phase 1 | Pending |
| QUAL-07 | Phase 7 | Pending |

**Coverage:**
- v1 requirements: 64 total
- Mapped to phases: 64
- Unmapped: 0

---
*Requirements defined: 2026-03-08*
*Last updated: 2026-03-08 after roadmap creation*
