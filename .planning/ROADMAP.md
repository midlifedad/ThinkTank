# Roadmap: ThinkTank

## Overview

ThinkTank is a continuous ingestion engine that discovers, fetches, and transcribes expert audio content into a structured PostgreSQL corpus. The build follows a strict dependency chain: database schema and project scaffolding first, then the job queue that drives all work, then content ingestion (the first real jobs), then GPU transcription, then LLM governance over corpus expansion, then autonomous discovery features, and finally the admin dashboard, REST API, and operational tooling. Each phase delivers a verifiable capability that the next phase depends on.

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

- [x] **Phase 1: Foundation Layer** - Database schema, models, migrations, configuration, logging, health endpoints, project scaffolding, and test infrastructure
- [ ] **Phase 2: Job Queue Engine** - DB-backed job queue with priority, retry, stale reclamation, rate limiting, backpressure, and kill switch
- [ ] **Phase 3: Content Ingestion Pipeline** - RSS feed polling, 3-layer deduplication, content filtering, source approval, content attribution, and discovery orchestration
- [ ] **Phase 4: Transcription Pipeline** - Three-pass transcription (captions, existing transcripts, Parakeet GPU), GPU worker service, on-demand scaling, audio processing
- [ ] **Phase 5: LLM Governance** - Claude Supervisor for thinker/source/candidate approval, audit trail, fallback escalation, scheduled health checks and digests
- [ ] **Phase 6: Discovery and Autonomous Growth** - Cascade discovery, guest discovery via Listen Notes and Podcast Index, candidate promotion, daily quotas
- [ ] **Phase 7: Operations, API, and Polish** - Admin dashboard, REST API, cost tracking, bootstrap sequence, operations runbook, development guide

## Phase Details

### Phase 1: Foundation Layer
**Goal**: A deployable FastAPI application with the complete database schema, async models, migrations, configuration system, structured logging, and test infrastructure -- everything needed for other phases to build on top of
**Depends on**: Nothing (first phase)
**Requirements**: FNDTN-01, FNDTN-02, FNDTN-03, FNDTN-04, FNDTN-05, FNDTN-06, FNDTN-07, FNDTN-08, FNDTN-09, QUAL-01, QUAL-02, QUAL-06
**Success Criteria** (what must be TRUE):
  1. Running `alembic upgrade head` against a fresh PostgreSQL instance creates all 14 tables with correct relationships, constraints, and indexes
  2. Every SQLAlchemy model can be instantiated via a factory function with sensible defaults, and persisted to the database in an integration test
  3. The FastAPI application starts, connects to PostgreSQL, and returns 200 from its health endpoint
  4. Every log entry is structured JSON containing timestamp, service name, correlation ID, and severity level
  5. `pytest` runs the full unit and integration test suite in under 60 seconds against a real PostgreSQL instance (Docker Compose), with architecture documentation generated alongside the schema
**Plans:** 3 plans

Plans:
- [x] 01-01-PLAN.md -- Project scaffold, FastAPI app, health endpoint, Docker Compose, toolchain
- [x] 01-02-PLAN.md -- SQLAlchemy 2.0 models for all 14 tables, factory functions
- [x] 01-03-PLAN.md -- Configuration, logging, Alembic migrations, Docker images, integration tests, architecture docs

### Phase 2: Job Queue Engine
**Goal**: A fully operational job queue where workers can claim jobs by priority, retry with backoff, reclaim stale jobs, respect external API rate limits, apply backpressure, and be halted via a global kill switch
**Depends on**: Phase 1
**Requirements**: QUEUE-01, QUEUE-02, QUEUE-03, QUEUE-04, QUEUE-05, QUEUE-06, QUEUE-07, QUEUE-08, QUAL-04
**Success Criteria** (what must be TRUE):
  1. A worker loop claims the highest-priority pending job using `SELECT FOR UPDATE SKIP LOCKED`, processes it, and marks it done -- with no two workers ever claiming the same job
  2. A failed job is retried with exponential backoff up to its max attempts, and a stale `running` job is automatically reclaimed and returned to the queue within 5 minutes
  3. External API calls are rate-limited via sliding-window counts in `rate_limit_usage`, and a worker that hits the limit backs off without blocking other workers
  4. When `process_content` queue depth exceeds the configured threshold, discovery job priority is automatically demoted; when `workers_active` is set to false, no worker claims any new job
  5. Every job handler has a contract test verifying its expected side effects given a known input payload
**Plans**: TBD

Plans:
- [ ] 02-01: TBD
- [ ] 02-02: TBD

### Phase 3: Content Ingestion Pipeline
**Goal**: The system can poll approved RSS feeds, extract episodes, deduplicate content across three layers (URL normalization, content fingerprint, trigram similarity), filter by duration and title patterns, and attribute content to thinkers
**Depends on**: Phase 2
**Requirements**: INGEST-01, INGEST-02, INGEST-03, INGEST-04, INGEST-05, INGEST-06, INGEST-07, DISC-03, DISC-04
**Success Criteria** (what must be TRUE):
  1. Polling an approved RSS feed extracts episodes as content rows with correct metadata, and the same feed polled twice produces no duplicate content (URL normalization catches identical URLs, fingerprinting catches cross-platform duplicates)
  2. Episodes shorter than the configured minimum duration or matching skip title patterns are inserted with `status = 'skipped'` and never enter the transcription queue
  3. Sources with `approval_status != 'approved'` are never polled, and tier-based refresh scheduling (6h/24h/168h) correctly staggers feed checks
  4. Content attribution tags the source owner as `role = 'primary'` with `confidence = 10`, and matches thinker names found in episode titles/descriptions as guests with appropriate confidence scores
  5. Candidate thinker names are deduplicated using `pg_trgm` trigram similarity at 0.7 threshold, preventing near-duplicate candidates from accumulating
**Plans**: TBD

Plans:
- [ ] 03-01: TBD
- [ ] 03-02: TBD
- [ ] 03-03: TBD

### Phase 4: Transcription Pipeline
**Goal**: Content discovered in Phase 3 is transcribed through a three-pass pipeline (YouTube captions first, existing transcripts second, Parakeet GPU inference last) with on-demand GPU scaling and automatic audio cleanup
**Depends on**: Phase 3
**Requirements**: TRANS-01, TRANS-02, TRANS-03, TRANS-04, TRANS-05, TRANS-06
**Success Criteria** (what must be TRUE):
  1. A `process_content` job first attempts YouTube captions, then checks for existing transcripts, and only falls back to Parakeet GPU inference when no text source is found -- with `transcription_method` recording which pass succeeded
  2. The GPU worker service loads Parakeet TDT 1.1B into VRAM once and holds it across jobs, processing audio at near real-time speed on an L4 GPU
  3. Audio is downloaded via yt-dlp, converted to 16kHz mono WAV via ffmpeg, and deleted immediately after transcription -- audio is never persisted to storage
  4. The CPU worker scales the GPU service up via Railway API when `process_content` queue exceeds threshold, and scales it down after the configured idle timeout with no pending transcription jobs
**Plans**: TBD

Plans:
- [ ] 04-01: TBD
- [ ] 04-02: TBD

### Phase 5: LLM Governance
**Goal**: An LLM Supervisor (Claude) governs all corpus expansion decisions -- approving/rejecting thinkers, sources, and candidates with a full audit trail, graceful degradation when the Anthropic API is unavailable, and scheduled health checks and digests
**Depends on**: Phase 4
**Requirements**: GOV-01, GOV-02, GOV-03, GOV-04, GOV-05, GOV-06, GOV-07, GOV-08, GOV-09, DISC-06
**Success Criteria** (what must be TRUE):
  1. A new thinker submitted for approval enters `awaiting_llm` status, the LLM Supervisor reviews it with a bounded context snapshot, and the decision (approve/reject/modify/escalate) is logged in `llm_reviews` with full prompt, response, and reasoning
  2. Source approval and candidate promotion follow the same gated flow -- workers never process unapproved sources or promote unapproved candidates
  3. When the Anthropic API is unavailable, jobs awaiting LLM review are automatically escalated to human review after `llm_timeout_hours`, and the existing pipeline continues operating on already-approved thinkers and sources
  4. Scheduled health checks run every 6 hours, daily digests run at 07:00 UTC, and weekly audits run on Mondays -- all producing structured summaries logged to `llm_reviews`
  5. Context snapshots are bounded (max 50 thinkers, 100 errors, 20 candidates per review) and `tokens_used` is tracked per review to prevent cost spirals
**Plans**: TBD

Plans:
- [ ] 05-01: TBD
- [ ] 05-02: TBD

### Phase 6: Discovery and Autonomous Growth
**Goal**: The system autonomously grows its corpus by scanning episode metadata for new thinker candidates, discovering guest appearances via Listen Notes and Podcast Index APIs, and promoting candidates through LLM-gated review
**Depends on**: Phase 5
**Requirements**: DISC-01, DISC-02, DISC-05
**Success Criteria** (what must be TRUE):
  1. Episode titles and descriptions are scanned for names not in the thinkers table, and names appearing in 3+ episodes are surfaced as candidate thinkers with `status = 'pending_llm'`
  2. Guest appearances are discovered via Listen Notes and Podcast Index APIs within configured rate limits, and discovered feeds are registered as sources pending LLM approval
  3. Daily quota limits on candidate discovery (`max_candidates_per_day`) prevent unbounded growth, and when the quota is approached, cascade discovery pauses until the LLM reviews the existing queue
**Plans**: TBD

Plans:
- [ ] 06-01: TBD
- [ ] 06-02: TBD

### Phase 7: Operations, API, and Polish
**Goal**: A complete operational layer -- admin dashboard for human oversight, REST API for programmatic access, cost tracking, bootstrap sequence, operations runbook, and development guide -- making the system production-ready and maintainable
**Depends on**: Phase 6
**Requirements**: OPS-01, OPS-02, OPS-03, OPS-04, OPS-05, OPS-06, API-01, API-02, API-03, API-04, API-05, API-06, QUAL-03, QUAL-05, QUAL-07
**Success Criteria** (what must be TRUE):
  1. The admin dashboard displays live queue depth, error logs, source health, GPU status, rate limit gauges, and API cost tracking -- with HTMX providing 10-second auto-refresh without a JavaScript framework
  2. The LLM decision panel shows pending approvals, recent decisions, and allows human override with logged reasoning -- and jobs awaiting LLM review longer than `llm_timeout_hours` are highlighted for human action
  3. REST API endpoints support CRUD operations on thinkers, sources, and content with filtering, pagination, and auto-generated OpenAPI documentation, with contract tests covering every endpoint's request/response shape and error format
  4. Running the bootstrap sequence (seed categories, seed config, seed thinkers, first LLM review, activate workers) on a fresh deployment produces a fully operational system with approved thinkers and jobs flowing
  5. The operations runbook covers bootstrap, post-deploy verification, rollback, and common problem resolution; the development guide covers how to add new job types, API endpoints, and thinker categories
**Plans**: TBD

Plans:
- [ ] 07-01: TBD
- [ ] 07-02: TBD
- [ ] 07-03: TBD

## Progress

**Execution Order:**
Phases execute in numeric order: 1 -> 2 -> 3 -> 4 -> 5 -> 6 -> 7

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Foundation Layer | 3/3 | Complete | 2026-03-09 |
| 2. Job Queue Engine | 0/2 | Not started | - |
| 3. Content Ingestion Pipeline | 0/3 | Not started | - |
| 4. Transcription Pipeline | 0/2 | Not started | - |
| 5. LLM Governance | 0/2 | Not started | - |
| 6. Discovery and Autonomous Growth | 0/2 | Not started | - |
| 7. Operations, API, and Polish | 0/3 | Not started | - |
