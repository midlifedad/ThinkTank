# Roadmap: ThinkTank

## Milestones

- **v1.0 Ingestion Engine** - Phases 1-7 (shipped 2026-03-09)
- **v1.1 Admin Control Panel** - Phases 8-12 (in progress)

## Overview

ThinkTank is a continuous ingestion engine that discovers, fetches, and transcribes expert audio content into a structured PostgreSQL corpus. The build follows a strict dependency chain: database schema and project scaffolding first, then the job queue that drives all work, then content ingestion (the first real jobs), then GPU transcription, then LLM governance over corpus expansion, then autonomous discovery features, and finally the admin dashboard, REST API, and operational tooling. Each phase delivers a verifiable capability that the next phase depends on.

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

<details>
<summary>v1.0 Ingestion Engine (Phases 1-7) -- SHIPPED 2026-03-09</summary>

- [x] **Phase 1: Foundation Layer** - Database schema, models, migrations, configuration, logging, health endpoints, project scaffolding, and test infrastructure
- [x] **Phase 2: Job Queue Engine** - DB-backed job queue with priority, retry, stale reclamation, rate limiting, backpressure, and kill switch
- [x] **Phase 3: Content Ingestion Pipeline** - RSS feed polling, 3-layer deduplication, content filtering, source approval, content attribution, and discovery orchestration
- [x] **Phase 4: Transcription Pipeline** - Three-pass transcription (captions, existing transcripts, Parakeet GPU), GPU worker service, on-demand scaling, audio processing
- [x] **Phase 5: LLM Governance** - Claude Supervisor for thinker/source/candidate approval, audit trail, fallback escalation, scheduled health checks and digests
- [x] **Phase 6: Discovery and Autonomous Growth** - Cascade discovery, guest discovery via Listen Notes and Podcast Index, candidate promotion, daily quotas
- [x] **Phase 7: Operations, API, and Polish** - Admin dashboard, REST API, cost tracking, bootstrap sequence, operations runbook, development guide

</details>

### v1.1 Admin Control Panel (Phases 8-12)

- [x] **Phase 8: Dashboard and System Configuration** - Morning briefing dashboard with health/activity/approvals, kill switch control, auto-refresh, and system config management (API keys, rate limits, worker settings, categories) (completed 2026-03-10)
- [x] **Phase 9: Thinker Management** - Searchable thinker list, add/edit/deactivate thinkers with LLM approval, thinker detail pages, candidate queue with promote/reject, triggered discovery
- [x] **Phase 10: Source Management** - Filterable source list, manual source addition, approve/reject sources, force-refresh feeds, source detail pages with health and error history
- [ ] **Phase 11: Pipeline Control** - Job queue browser with status/type/date filters, manual job triggers, recurring task scheduler with frequency/toggle, job retry/cancel, job detail view
- [ ] **Phase 12: Agent Chat** - Persistent chat drawer on all admin pages, LLM agent with database query capability, propose-then-execute mutations, SSE streaming responses, session chat history

## Phase Details

<details>
<summary>v1.0 Ingestion Engine (Phases 1-7) -- SHIPPED 2026-03-09</summary>

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
**Plans:** 3 plans

Plans:
- [x] 02-01-PLAN.md -- Queue core: atomic job claiming (SKIP LOCKED), completion, failure with retry/backoff, error categorization enum
- [x] 02-02-PLAN.md -- Queue coordination: sliding-window rate limiter, backpressure priority demotion, kill switch, stale job reclamation
- [x] 02-03-PLAN.md -- Worker loop with poll/claim/dispatch cycle, handler registry with Protocol interface, contract tests

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
**Plans:** 4 plans

Plans:
- [x] 03-01-PLAN.md -- Pure logic modules (URL normalizer, fingerprint, duration parser, content filter, name matcher/normalizer, feed parser), feedparser dependency, unit tests
- [x] 03-02-PLAN.md -- RSS fixture files, pg_trgm Alembic migration, test conftest update for pg_trgm
- [x] 03-03-PLAN.md -- fetch_podcast_feed and refresh_due_sources handlers, config reader, handler registration, integration tests for feed polling, dedup, scheduling
- [x] 03-04-PLAN.md -- tag_content_thinkers handler, trigram similarity module, content attribution, candidate dedup, contract tests for all Phase 3 handlers

### Phase 4: Transcription Pipeline
**Goal**: Content discovered in Phase 3 is transcribed through a three-pass pipeline (YouTube captions first, existing transcripts second, Parakeet GPU inference last) with on-demand GPU scaling and automatic audio cleanup
**Depends on**: Phase 3
**Requirements**: TRANS-01, TRANS-02, TRANS-03, TRANS-04, TRANS-05, TRANS-06
**Success Criteria** (what must be TRUE):
  1. A `process_content` job first attempts YouTube captions, then checks for existing transcripts, and only falls back to Parakeet GPU inference when no text source is found -- with `transcription_method` recording which pass succeeded
  2. The GPU worker service loads Parakeet TDT 1.1B into VRAM once and holds it across jobs, processing audio at near real-time speed on an L4 GPU
  3. Audio is downloaded via yt-dlp, converted to 16kHz mono WAV via ffmpeg, and deleted immediately after transcription -- audio is never persisted to storage
  4. The CPU worker scales the GPU service up via Railway API when `process_content` queue exceeds threshold, and scales it down after the configured idle timeout with no pending transcription jobs
**Plans**: 2 plans

Plans:
- [x] 04-01-PLAN.md -- Transcription building blocks (captions, existing transcripts, audio download/conversion, GPU client, Railway scaling client), yt-dlp/webvtt-py dependencies, unit tests
- [x] 04-02-PLAN.md -- process_content handler (three-pass orchestrator), GPU worker FastAPI service, GPU scaling scheduler in worker loop, handler registration, integration + contract tests

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
**Plans**: 3 plans

Plans:
- [x] 05-01-PLAN.md -- LLM core module: Anthropic client wrapper, Pydantic response schemas, prompt templates, bounded context snapshots, decision application logic with candidate promotion
- [x] 05-02-PLAN.md -- llm_approval_check handler, error categorization extension for Anthropic exceptions, handler registration, integration and contract tests
- [x] 05-03-PLAN.md -- Timeout escalation, scheduled health checks/digests/audits, time utilities, worker loop integration with 4 new LLM schedulers

### Phase 6: Discovery and Autonomous Growth
**Goal**: The system autonomously grows its corpus by scanning episode metadata for new thinker candidates, discovering guest appearances via Listen Notes and Podcast Index APIs, and promoting candidates through LLM-gated review
**Depends on**: Phase 5
**Requirements**: DISC-01, DISC-02, DISC-05
**Success Criteria** (what must be TRUE):
  1. Episode titles and descriptions are scanned for names not in the thinkers table, and names appearing in 3+ episodes are surfaced as candidate thinkers with `status = 'pending_llm'`
  2. Guest appearances are discovered via Listen Notes and Podcast Index APIs within configured rate limits, and discovered feeds are registered as sources pending LLM approval
  3. Daily quota limits on candidate discovery (`max_candidates_per_day`) prevent unbounded growth, and when the quota is approached, cascade discovery pauses until the LLM reviews the existing queue
**Plans:** 2 plans

Plans:
- [x] 06-01-PLAN.md -- Discovery building blocks: regex name extractor, Listen Notes client, Podcast Index client, daily quota tracker, unit tests with API fixtures
- [x] 06-02-PLAN.md -- scan_for_candidates, discover_guests_listennotes, discover_guests_podcastindex handlers, error category extensions, handler registration, integration and contract tests

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
**Plans:** 3 plans

Plans:
- [x] 07-01-PLAN.md -- REST API endpoints (thinkers, sources, content, jobs, config CRUD), Pydantic schemas, rollup_api_usage cost tracking handler, OpenAPI docs, contract tests
- [x] 07-02-PLAN.md -- Admin dashboard (HTMX + Jinja2), LLM decision panel with human override, rate limit gauges, category taxonomy management, integration tests
- [x] 07-03-PLAN.md -- Bootstrap seed scripts (categories, config, thinkers), bootstrap orchestrator, operations runbook, development guide, integration tests

</details>

### Phase 8: Dashboard and System Configuration
**Goal**: The existing admin dashboard is transformed into an operational morning briefing with system health, activity feeds, queue status, and a global kill switch control -- plus full management of API keys, rate limits, worker settings, and category taxonomy
**Depends on**: Phase 7
**Requirements**: DASH-01, DASH-02, DASH-03, DASH-04, CONF-01, CONF-02, CONF-03, CONF-04
**Success Criteria** (what must be TRUE):
  1. Operator opens the dashboard and sees a morning briefing page with system health indicators (worker status, DB connection, error rates), queue depth broken down by job type, and a count of pending approvals -- all loading within 2 seconds
  2. Operator can toggle the global kill switch on/off from a prominent dashboard control, and the system immediately stops/starts claiming new jobs in response
  3. Operator can view a recent activity feed showing the last 50 system actions (jobs completed, approvals made, errors, thinkers added), and the entire dashboard auto-refreshes every 10 seconds via HTMX without a full page reload
  4. Operator can manage API keys (add, update, remove) for external services, view and edit rate limit settings per API, and view and edit system config values (worker settings, thresholds, timeouts) from dedicated configuration pages
  5. Operator can manage the category taxonomy (add, edit, reorder categories and subcategories) from the configuration section, with changes immediately reflected in thinker forms
**Plans:** 2/2 plans complete

Plans:
- [x] 08-01-PLAN.md -- Morning briefing dashboard: health summary, kill switch toggle, activity feed, pending approvals, reorganized layout, integration tests
- [x] 08-02-PLAN.md -- System configuration page: rate limits editor, system config editor, config landing page with links to API keys and categories, integration tests

### Phase 9: Thinker Management
**Goal**: Operators can manage the full thinker lifecycle from the admin panel -- browsing, searching, adding new thinkers (with LLM approval), editing existing thinkers, viewing detailed thinker profiles, managing the candidate queue, and triggering discovery
**Depends on**: Phase 8
**Requirements**: THNK-01, THNK-02, THNK-03, THNK-04, THNK-05, THNK-06, THNK-07
**Success Criteria** (what must be TRUE):
  1. Operator can view a searchable, filterable list of all thinkers showing name, tier, category, active status, and source count -- with text search returning results as they type via HTMX
  2. Operator can add a new thinker via an inline form (name, tier, categories), which creates the thinker record and triggers LLM approval -- and the new thinker appears in the list with "awaiting_llm" status
  3. Operator can edit an existing thinker's name, tier, categories, and active status via an inline edit form, and can deactivate/reactivate a thinker without deleting their data
  4. Operator can view a thinker detail page showing their sources, recent content, discovery status, and LLM review history -- with links to drill into individual sources and content
  5. Operator can view the candidate queue, promote or reject candidates with a reason, and trigger podcast discovery (PodcastIndex) for a specific thinker from the thinker detail page
**Plans:** 2 plans

Plans:
- [x] 09-01-PLAN.md -- Thinker list page: searchable/filterable list, add form with LLM approval trigger, edit form, active toggle, integration tests
- [x] 09-02-PLAN.md -- Thinker detail page: sources/content/reviews tabs, candidate queue with promote/reject, PodcastIndex discovery trigger, integration tests

### Phase 10: Source Management
**Goal**: Operators can view, add, approve, reject, and inspect sources for any thinker -- with manual source addition, force-refresh capability, and detailed source health monitoring
**Depends on**: Phase 9
**Requirements**: SRC-01, SRC-02, SRC-03, SRC-04, SRC-05
**Success Criteria** (what must be TRUE):
  1. Operator can view all sources with filters for thinker, approval status, and source type -- and the list displays feed health indicators (last fetched, error count) at a glance
  2. Operator can approve or reject a pending source with a reason, bypassing LLM review -- and the decision is logged in the audit trail alongside LLM decisions
  3. Operator can add a source manually (RSS URL, name, thinker) which registers it as pending approval, and can force-refresh a specific approved source immediately (creating a fetch_podcast_feed job)
  4. Operator can view a source detail page showing feed health, last fetched time, episode count, and error history -- providing enough context to diagnose feed problems without checking logs
**Plans:** 2 plans

Plans:
- [x] 10-01-PLAN.md -- Source list page: filterable list, add form, approve/reject with audit trail, force-refresh, integration tests
- [x] 10-02-PLAN.md -- Source detail page: health summary, episodes list, error history, integration tests

### Phase 11: Pipeline Control
**Goal**: Operators have full visibility and control over the job pipeline -- browsing the queue, triggering jobs manually, configuring recurring task schedules, and managing individual job lifecycle (retry, cancel, inspect)
**Depends on**: Phase 8
**Requirements**: PIPE-01, PIPE-02, PIPE-03, PIPE-04, PIPE-05
**Success Criteria** (what must be TRUE):
  1. Operator can view the job queue with filters by status (pending, running, failed, complete), job type, and date range -- with pagination handling queues of thousands of jobs
  2. Operator can manually trigger pipeline jobs (refresh_due_sources, scan_for_candidates, discover_guests for a thinker) from the pipeline page, and the created job appears in the queue immediately
  3. Operator can configure recurring task schedules with frequency (in hours), an enable/disable toggle, and a Run Now button -- without cron syntax, just simple frequency controls
  4. Operator can retry a failed job or cancel a pending job from the queue view, and can view job detail showing payload, attempts, error messages, and timing for any job
**Plans:** 2 plans

Plans:
- [x] 11-01-PLAN.md -- Pipeline page with job queue browser (filters, pagination), manual triggers, retry/cancel, job detail view, integration tests
- [ ] 11-02-PLAN.md -- Recurring task scheduler editor with frequency/toggle/Run Now, system_config persistence, integration tests

### Phase 12: Agent Chat
**Goal**: A persistent LLM-powered chat drawer is available on every admin page, enabling operators to ask questions about system state and propose mutations through natural language -- with streaming responses and a propose-then-execute safety model
**Depends on**: Phases 8, 9, 10, 11
**Requirements**: CHAT-01, CHAT-02, CHAT-03, CHAT-04, CHAT-05
**Success Criteria** (what must be TRUE):
  1. Operator can open a persistent chat drawer (bottom of any admin page) and it remains open across page navigations within the admin panel
  2. Operator can ask the agent questions about system state ("how many thinkers are active?", "what failed in the last hour?", "what's in the queue?") and receive accurate answers drawn from live database queries
  3. When the operator requests a state-changing action ("add Nassim Taleb", "approve that source", "trigger discovery for Sam Harris"), the agent proposes the action with details and waits for explicit confirmation before executing
  4. Agent responses stream in real-time via SSE so the operator sees partial output as it generates, with no perceptible delay before the first token appears
  5. Operator can scroll through a history of recent chat interactions within the current session, providing context for follow-up questions
**Plans**: TBD

## Progress

**Execution Order:**
Phases execute in numeric order: 1 -> 2 -> 3 -> 4 -> 5 -> 6 -> 7 -> 8 -> 9 -> 10 -> 11 -> 12

Note: Phase 11 depends on Phase 8 (not Phase 10), so Phases 9-10 and Phase 11 could theoretically run in parallel after Phase 8.

| Phase | Milestone | Plans Complete | Status | Completed |
|-------|-----------|----------------|--------|-----------|
| 1. Foundation Layer | v1.0 | 3/3 | Complete | 2026-03-09 |
| 2. Job Queue Engine | v1.0 | 3/3 | Complete | 2026-03-09 |
| 3. Content Ingestion Pipeline | v1.0 | 4/4 | Complete | 2026-03-09 |
| 4. Transcription Pipeline | v1.0 | 2/2 | Complete | 2026-03-09 |
| 5. LLM Governance | v1.0 | 3/3 | Complete | 2026-03-09 |
| 6. Discovery and Autonomous Growth | v1.0 | 2/2 | Complete | 2026-03-09 |
| 7. Operations, API, and Polish | v1.0 | 3/3 | Complete | 2026-03-09 |
| 8. Dashboard and System Configuration | 2/2 | Complete   | 2026-03-10 | 2026-03-10 |
| 9. Thinker Management | v1.1 | 2/2 | Complete | 2026-03-10 |
| 10. Source Management | v1.1 | 2/2 | Complete | 2026-03-10 |
| 11. Pipeline Control | v1.1 | 1/2 | In Progress | - |
| 12. Agent Chat | v1.1 | 0/? | Not started | - |
