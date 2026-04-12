# ThinkTank

## What This Is

A knowledge infrastructure engine that continuously discovers, fetches, and transcribes content from leading thinkers across a wide range of categories. Primarily ingests long-form spoken content (podcasts, interviews) into a searchable PostgreSQL store — capturing the unpolished, in-depth expertise that reveals where a thinker's understanding currently stands. This forms the text knowledge foundation for later extraction of claims, opinions, research sources, and deeper analysis.

## Core Value

Total capture of expert knowledge from every source where they've published, starting with long-form audio where thinkers are least polished and most revealing.

## Current Milestone: v1.1 Admin Control Panel

**Goal:** Transform the read-only admin dashboard into a full operational control panel with thinker management, pipeline controls, scheduling, and an LLM-powered chat agent for natural language system interaction.

**Target features:**
- Full thinker lifecycle management (add, edit, approve, deactivate) with candidate promotion
- Source management with approval workflows and manual source addition
- Pipeline control: job queue visibility, kill switch, manual job triggers, scheduler configuration
- System configuration: rate limits, API keys, worker settings, category taxonomy
- LLM chat agent as persistent bottom drawer for natural language commands ("add Nassim Taleb and find his podcast appearances")
- Morning briefing dashboard: health status, recent activity, pending approvals, queue depth

## Requirements

### Validated (v1.0)

- [x] Continuous podcast discovery and RSS ingestion for tracked thinkers
- [x] Multi-source transcription pipeline (YouTube captions, existing transcripts, Parakeet GPU inference)
- [x] LLM Supervisor governing all corpus expansion decisions (new thinkers, sources, candidates)
- [x] DB-backed job queue with priority, rate limiting, and stale job reclamation
- [x] 3-layer content deduplication (URL normalization, content fingerprint, trigram similarity)
- [x] Admin dashboard for human oversight and system monitoring (read-only)
- [x] Category-organized thinker hierarchy with cascade discovery
- [x] GPU worker on-demand scaling via Railway API
- [x] Content attribution linking content to thinkers with confidence scoring
- [x] API for programmatic access to ingested content and system state

### Active (v1.1)

(See REQUIREMENTS.md for detailed REQ-IDs)

### Out of Scope

- Query/retrieval interface — knowledge access layer comes in a future milestone
- Claim/opinion extraction — downstream analysis built on top of ingested content later
- Non-text content (images, files, video) — text-first, other modalities later
- Real-time streaming ingestion — batch/poll-based is sufficient for v1
- Multi-tenant access control — single-owner system for now

## Context

ThinkTank is the foundational layer for a larger knowledge system. The broader vision is to ingest all global expertise across categories, then extract structured knowledge (claims, opinions, research citations) from that corpus. V1 focuses purely on the ingestion and storage pipeline — getting content in, transcribed, and organized. The retrieval and analysis layers will be built as separate milestones on top of this foundation.

Long-form audio (podcasts, interviews) was chosen as the starting content type because speakers are less guarded and more revealing of their current thinking compared to polished written content. This yields richer raw material for later knowledge extraction.

The system specification is fully defined in `ThinkTank_Specification.md`. Engineering standards for testing, observability, documentation, and deployment are codified in `STANDARDS.md`.

## Constraints

- **Deployment**: Railway (4 services: API, Worker-CPU, Worker-GPU, Admin)
- **Database**: PostgreSQL (Railway-managed), no Redis — DB-backed job queue only
- **GPU**: Railway L4 GPU for Parakeet TDT 1.1B transcription, scaled on-demand
- **LLM**: Claude claude-sonnet-4-20250514 via Anthropic API for Supervisor decisions
- **Stack**: Python 3.12, FastAPI, SQLAlchemy 2.0, Alembic, HTMX (admin)
- **Cost**: Rate limiting and API usage tracking are first-class — no runaway spend
- **Standards**: All work must follow `STANDARDS.md` (testing, observability, docs, deployment conventions)

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| DB-backed job queue (no Redis) | Simplifies infrastructure, Postgres `SELECT FOR UPDATE SKIP LOCKED` sufficient for throughput | — Pending |
| Parakeet TDT 1.1B for transcription | Runs on Railway L4 GPU, good accuracy, cost-effective vs API transcription | — Pending |
| LLM Supervisor for corpus expansion | Prevents garbage-in by gating all new thinkers/sources through Claude review | — Pending |
| Railway for deployment | Managed infrastructure, GPU support, auto-deploy from git | — Pending |
| Long-form audio first | Less polished content reveals more authentic expertise than written sources | — Pending |
| 3-layer dedup | URL normalization + fingerprint + trigram covers all duplication vectors | — Pending |
| Persistent chat drawer (not separate page) | Agent always accessible, co-located with data being discussed | v1.1 |
| Propose-then-execute for mutations | Chat agent proposes changes, user confirms before execution — safe by default | v1.1 |
| Simple scheduler (frequency + toggle, not cron) | Cron is overkill for single-owner; frequency in hours is simpler and sufficient | v1.1 |
| Controls co-located with data | Add thinker lives on Thinkers page, not a separate admin section — intuitive | v1.1 |
| SSE for chat streaming | sse-starlette for real-time agent responses, reuses existing LLMClient | v1.1 |

---
*Last updated: 2026-04-12 after Phase 13 complete — catalog-then-promote pipeline, YouTube channel support*
