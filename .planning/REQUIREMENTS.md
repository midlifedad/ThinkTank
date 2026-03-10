# Requirements: ThinkTank

**Defined:** 2026-03-08
**Core Value:** Total capture of expert knowledge from every source where they've published, starting with long-form audio where thinkers are least polished and most revealing.

## v1.1 Requirements

Requirements for Admin Control Panel milestone. Phases 8-12.

### Dashboard

- [x] **DASH-01**: Operator can view a morning briefing page showing system health (worker status, DB connection, error rates), queue depth by job type, and pending approval counts
- [x] **DASH-02**: Operator can toggle the global kill switch on/off from a prominent dashboard control
- [x] **DASH-03**: Operator can view a recent activity feed showing the last 50 system actions (jobs completed, approvals made, errors, thinkers added)
- [x] **DASH-04**: Dashboard auto-refreshes every 10 seconds via HTMX without full page reload

### Thinker Management

- [x] **THNK-01**: Operator can view a searchable, filterable list of all thinkers with name, tier, category, active status, and source count
- [x] **THNK-02**: Operator can add a new thinker via a form (name, tier, categories) which creates the thinker and triggers LLM approval
- [x] **THNK-03**: Operator can edit an existing thinker's name, tier, categories, and active status
- [x] **THNK-04**: Operator can view a thinker detail page showing their sources, recent content, discovery status, and LLM review history
- [x] **THNK-05**: Operator can view the candidate queue and promote or reject candidates with a reason
- [x] **THNK-06**: Operator can trigger podcast discovery (PodcastIndex) for a specific thinker from the thinker detail page
- [x] **THNK-07**: Operator can deactivate/reactivate a thinker without deleting their data

### Source Management

- [ ] **SRC-01**: Operator can view all sources filterable by thinker, approval status, and source type
- [ ] **SRC-02**: Operator can approve or reject a pending source with a reason, bypassing LLM review
- [ ] **SRC-03**: Operator can add a source manually (RSS URL, name, thinker) which registers it as pending approval
- [ ] **SRC-04**: Operator can force-refresh a specific source immediately (creates a fetch_podcast_feed job)
- [ ] **SRC-05**: Operator can view source detail page showing feed health, last fetched time, episode count, and error history

### Pipeline Control

- [x] **PIPE-01**: Operator can view the job queue with filters by status (pending, running, failed, complete), job type, and date range
- [x] **PIPE-02**: Operator can manually trigger pipeline jobs: refresh_due_sources, scan_for_candidates, discover_guests for a thinker
- [ ] **PIPE-03**: Operator can configure recurring task schedules with frequency (hours), enable/disable toggle, and a Run Now button
- [x] **PIPE-04**: Operator can retry a failed job or cancel a pending job from the queue view
- [x] **PIPE-05**: Operator can view job detail showing payload, attempts, error messages, and timing

### System Configuration

- [x] **CONF-01**: Operator can manage API keys (add, update, remove) for external services (Anthropic, PodcastIndex, YouTube)
- [x] **CONF-02**: Operator can view and edit rate limit settings per external API
- [x] **CONF-03**: Operator can view and edit system config values (worker settings, thresholds, timeouts)
- [x] **CONF-04**: Operator can manage the category taxonomy (add, edit, reorder categories and subcategories)

### Agent Chat

- [ ] **CHAT-01**: Operator can open a persistent chat drawer (bottom of page) on any admin page to interact with an LLM agent
- [ ] **CHAT-02**: Agent can answer questions about system state (how many thinkers, what's in the queue, recent errors) by querying the database
- [ ] **CHAT-03**: Agent proposes state-changing actions (add thinker, trigger discovery, approve source) and waits for operator confirmation before executing
- [ ] **CHAT-04**: Agent responses stream in real-time via SSE so the operator sees partial output as it generates
- [ ] **CHAT-05**: Operator can see a history of recent chat interactions within the current session

## v1.0 Requirements (Complete)

v1.0 ingestion engine requirements. All mapped to Phases 1-7.

### Foundation

- [x] **FNDTN-01**: PostgreSQL schema with all 14 tables (categories, thinkers, sources, content, jobs, llm_reviews, system_config, rate_limit_usage, api_usage, content_thinkers, candidate_thinkers, thinker_profiles, thinker_metrics, thinker_categories) deployed via Alembic migration
- [x] **FNDTN-02**: SQLAlchemy 2.0 async models for all tables with relationship mappings
- [x] **FNDTN-03**: Alembic migration system with advisory lock to prevent concurrent migration corruption
- [x] **FNDTN-04**: Environment-based configuration with DB override precedence (env vars > system_config table > code defaults)
- [x] **FNDTN-05**: Structured JSON logging with correlation IDs, service name, job ID on every log entry
- [x] **FNDTN-06**: Health endpoint per service returning 200 when DB connected and worker loop running
- [x] **FNDTN-07**: FastAPI application scaffold with async lifespan, connection pool configuration, and CORS
- [x] **FNDTN-08**: Project toolchain setup (uv, ruff, mypy, pytest, pre-commit) with CI enforcement
- [x] **FNDTN-09**: Docker configuration for all 4 Railway services (API, CPU Worker, GPU Worker, Admin)

### Job Queue

- [x] **QUEUE-01**: DB-backed job queue using `SELECT FOR UPDATE SKIP LOCKED` with priority ordering
- [x] **QUEUE-02**: Async worker base loop that claims and dispatches jobs by type with configurable concurrency
- [x] **QUEUE-03**: Job retry with exponential backoff and per-type max attempt limits
- [x] **QUEUE-04**: Stale job reclamation running every 5 minutes, returning stuck `running` jobs to `queued`
- [x] **QUEUE-05**: Rate limit coordination via `rate_limit_usage` table with sliding-window queries across concurrent workers
- [x] **QUEUE-06**: Backpressure mechanism demoting discovery priority when transcription queue exceeds threshold
- [x] **QUEUE-07**: Global kill switch (`workers_active = false` in system_config) halting all job claiming
- [x] **QUEUE-08**: Error categorization with closed set of error categories on failed jobs

### Content Ingestion

- [ ] **INGEST-01**: RSS feed polling via httpx (with 60s timeout) + feedparser for XML parsing, extracting episodes as content rows
- [ ] **INGEST-02**: URL normalization (strip UTMs, force HTTPS, canonicalize YouTube IDs) with `canonical_url` unique constraint
- [ ] **INGEST-03**: Content fingerprinting via `sha256(title + date + duration)` catching cross-platform duplicates
- [ ] **INGEST-04**: Content filtering by minimum duration (default 600s) and skip title patterns, with per-source overrides
- [ ] **INGEST-05**: Source approval workflow — workers only process sources with `approval_status = 'approved'`
- [ ] **INGEST-06**: Tier-based refresh scheduling (Tier 1: 6h, Tier 2: 24h, Tier 3: 168h) via `refresh_due_sources` check
- [ ] **INGEST-07**: Discovery orchestration job that coordinates feed checks across all approved sources

### Transcription

- [x] **TRANS-01**: Three-pass transcription pipeline: YouTube captions first, existing transcripts second, Parakeet GPU inference last
- [x] **TRANS-02**: GPU Worker service running Parakeet TDT 1.1B on Railway L4, model persisted in VRAM across jobs
- [x] **TRANS-03**: Audio download via yt-dlp (pinned to 2025.12.08) with ffmpeg conversion to 16kHz WAV
- [x] **TRANS-04**: On-demand GPU scaling via Railway API — spin up when queue > threshold, shut down after idle timeout
- [x] **TRANS-05**: Audio temp file cleanup after transcription (audio never persisted to storage)
- [x] **TRANS-06**: Transcription output stored in `content.body_text` with metadata (word count, duration, source pass used)

### LLM Governance

- [x] **GOV-01**: LLM Supervisor using Claude claude-sonnet-4-20250514 with structured JSON prompts for all corpus expansion decisions
- [x] **GOV-02**: Thinker approval flow — new thinkers reviewed by LLM with context snapshot before activation
- [x] **GOV-03**: Source approval flow — new sources reviewed by LLM before RSS polling begins
- [x] **GOV-04**: Candidate thinker review — batch review of candidates exceeding appearance threshold
- [x] **GOV-05**: Full audit trail in `llm_reviews` table with context snapshot, prompt, raw response, parsed decision, reasoning
- [x] **GOV-06**: Fallback and timeout escalation — pending approvals escalate to human review after `llm_timeout_hours` (default 2h)
- [x] **GOV-07**: Graceful degradation when Anthropic API unavailable — existing pipeline continues, new approvals queue for human
- [x] **GOV-08**: Scheduled health checks, daily digests, and weekly corpus audits via LLM
- [x] **GOV-09**: Context budgeting — bounded context snapshots (max 50 thinkers, 100 errors, 20 candidates per review)

### Discovery

- [x] **DISC-01**: Cascade discovery — scan episode titles/descriptions for names not in thinkers table, surface as candidates after 3+ appearances
- [x] **DISC-02**: Guest discovery via Listen Notes and Podcast Index APIs with rate-limited queries
- [x] **DISC-03**: Content attribution via `content_thinkers` junction with role (host/guest/panelist/mentioned) and confidence scoring (1-10)
- [x] **DISC-04**: Trigram similarity dedup (`pg_trgm`) for candidate thinker names at 0.7 threshold
- [x] **DISC-05**: Daily quota limits on candidate discovery to prevent unbounded growth
- [x] **DISC-06**: Candidate-to-thinker promotion flow triggered by LLM batch review approval

### Operations

- [x] **OPS-01**: Admin dashboard (HTMX + FastAPI) showing queue depth, error log, source health, GPU status
- [x] **OPS-02**: LLM decision panel — view pending approvals, recent decisions, override with audit trail
- [ ] **OPS-03**: API cost tracking via `api_usage` table with hourly rollups and estimated USD costs
- [x] **OPS-04**: Rate limit gauges showing current usage vs configured limits per external API
- [x] **OPS-05**: Category taxonomy management in admin dashboard
- [x] **OPS-06**: Bootstrap sequence — seed categories, initial thinkers, trigger first LLM review, activate workers

### API

- [ ] **API-01**: RESTful endpoints for thinkers (CRUD, list with filtering by category/tier/status)
- [ ] **API-02**: RESTful endpoints for sources (list by thinker, approval status filtering)
- [ ] **API-03**: RESTful endpoints for content (list by source/thinker, pagination, status filtering)
- [ ] **API-04**: Job queue status endpoint (counts by type/status, recent errors)
- [ ] **API-05**: System config read/write endpoints for operational parameters
- [ ] **API-06**: OpenAPI auto-generated documentation

### Quality Standards

- [x] **QUAL-01**: Test suite following STANDARDS.md pyramid — unit tests (pure logic), integration tests (real Postgres), E2E tests (full system flow)
- [x] **QUAL-02**: Factory functions for all domain objects with sensible defaults and overridable fields
- [ ] **QUAL-03**: Contract tests for every API endpoint (request/response shape, status codes, error formats)
- [x] **QUAL-04**: Contract tests for every job handler (given input payload, expected side effects)
- [x] **QUAL-05**: Operations runbook covering bootstrap, post-deploy verification, rollback, and common problem resolution
- [x] **QUAL-06**: Architecture documentation with data flow diagrams and service boundaries
- [x] **QUAL-07**: Development guide covering how to add new job types, new API endpoints, and new thinker categories

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
| Agent chat: delete operations | Too dangerous for automated execution; manual controls only |
| Agent chat: API key management | Security-sensitive, always manual via dedicated UI |
| Agent chat: auto-healing/self-repair | Fail-safe principle; agent proposes, human executes |
| Cron-style scheduling | Over-engineered for single-owner; frequency + toggle is sufficient |
| Separate content management page | Too rare an action; content accessed via source/thinker detail pages |

## Traceability

Which phases cover which requirements. Updated during roadmap creation.

| Requirement | Phase | Status |
|-------------|-------|--------|
| FNDTN-01 | Phase 1 | Complete |
| FNDTN-02 | Phase 1 | Complete |
| FNDTN-03 | Phase 1 | Complete |
| FNDTN-04 | Phase 1 | Complete |
| FNDTN-05 | Phase 1 | Complete |
| FNDTN-06 | Phase 1 | Complete |
| FNDTN-07 | Phase 1 | Complete |
| FNDTN-08 | Phase 1 | Complete |
| FNDTN-09 | Phase 1 | Complete |
| QUEUE-01 | Phase 2 | Complete |
| QUEUE-02 | Phase 2 | Complete |
| QUEUE-03 | Phase 2 | Complete |
| QUEUE-04 | Phase 2 | Complete |
| QUEUE-05 | Phase 2 | Complete |
| QUEUE-06 | Phase 2 | Complete |
| QUEUE-07 | Phase 2 | Complete |
| QUEUE-08 | Phase 2 | Complete |
| INGEST-01 | Phase 3 | Pending |
| INGEST-02 | Phase 3 | Pending |
| INGEST-03 | Phase 3 | Pending |
| INGEST-04 | Phase 3 | Pending |
| INGEST-05 | Phase 3 | Pending |
| INGEST-06 | Phase 3 | Pending |
| INGEST-07 | Phase 3 | Pending |
| TRANS-01 | Phase 4 | Complete (04-01: captions, existing, audio modules) |
| TRANS-02 | Phase 4 | Complete |
| TRANS-03 | Phase 4 | Complete (04-01: yt-dlp + ffmpeg conversion) |
| TRANS-04 | Phase 4 | Complete |
| TRANS-05 | Phase 4 | Complete (04-01: guaranteed cleanup in finally blocks) |
| TRANS-06 | Phase 4 | Complete |
| GOV-01 | Phase 5 | Complete |
| GOV-02 | Phase 5 | Complete |
| GOV-03 | Phase 5 | Complete |
| GOV-04 | Phase 5 | Complete |
| GOV-05 | Phase 5 | Complete |
| GOV-06 | Phase 5 | Complete |
| GOV-07 | Phase 5 | Complete |
| GOV-08 | Phase 5 | Complete |
| GOV-09 | Phase 5 | Complete |
| DISC-01 | Phase 6 | Complete (06-01: name extractor, 06-02: scan_for_candidates handler) |
| DISC-02 | Phase 6 | Complete (06-01: API clients, 06-02: discover_guests handlers) |
| DISC-03 | Phase 3 | Complete |
| DISC-04 | Phase 3 | In Progress (pg_trgm extension + GiST index created in 03-02) |
| DISC-05 | Phase 6 | Complete (06-01: quota tracker, 06-02: quota enforcement in scan_for_candidates) |
| DISC-06 | Phase 5 | Complete |
| OPS-01 | Phase 7 | Complete |
| OPS-02 | Phase 7 | Complete |
| OPS-03 | Phase 7 | Pending |
| OPS-04 | Phase 7 | Complete |
| OPS-05 | Phase 7 | Complete |
| OPS-06 | Phase 7 | Complete |
| API-01 | Phase 7 | Pending |
| API-02 | Phase 7 | Pending |
| API-03 | Phase 7 | Pending |
| API-04 | Phase 7 | Pending |
| API-05 | Phase 7 | Pending |
| API-06 | Phase 7 | Pending |
| QUAL-01 | Phase 1 | Complete |
| QUAL-02 | Phase 1 | Complete |
| QUAL-03 | Phase 7 | Pending |
| QUAL-04 | Phase 2 | Complete |
| QUAL-05 | Phase 7 | Complete |
| QUAL-06 | Phase 1 | Complete |
| QUAL-07 | Phase 7 | Complete |
| DASH-01 | Phase 8 | Complete |
| DASH-02 | Phase 8 | Complete |
| DASH-03 | Phase 8 | Complete |
| DASH-04 | Phase 8 | Complete |
| CONF-01 | Phase 8 | Complete |
| CONF-02 | Phase 8 | Complete |
| CONF-03 | Phase 8 | Complete |
| CONF-04 | Phase 8 | Complete |
| THNK-01 | Phase 9 | Complete |
| THNK-02 | Phase 9 | Complete |
| THNK-03 | Phase 9 | Complete |
| THNK-04 | Phase 9 | Complete |
| THNK-05 | Phase 9 | Complete |
| THNK-06 | Phase 9 | Complete |
| THNK-07 | Phase 9 | Complete |
| SRC-01 | Phase 10 | Pending |
| SRC-02 | Phase 10 | Pending |
| SRC-03 | Phase 10 | Pending |
| SRC-04 | Phase 10 | Pending |
| SRC-05 | Phase 10 | Pending |
| PIPE-01 | Phase 11 | Complete |
| PIPE-02 | Phase 11 | Complete |
| PIPE-03 | Phase 11 | Pending |
| PIPE-04 | Phase 11 | Complete |
| PIPE-05 | Phase 11 | Complete |
| CHAT-01 | Phase 12 | Pending |
| CHAT-02 | Phase 12 | Pending |
| CHAT-03 | Phase 12 | Pending |
| CHAT-04 | Phase 12 | Pending |
| CHAT-05 | Phase 12 | Pending |

**v1.0 Coverage:**
- v1.0 requirements: 64 total
- Mapped to phases: 64
- Unmapped: 0

**v1.1 Coverage:**
- v1.1 requirements: 30 total
- Mapped to phases: 30
- Unmapped: 0

---
*Requirements defined: 2026-03-08*
*Last updated: 2026-03-09 after v1.1 roadmap creation (Phases 8-12)*
