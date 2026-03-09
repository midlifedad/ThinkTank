# ThinkTank Architecture

## System Overview

ThinkTank is a global intelligence ingestion platform that monitors 500+
thinkers (analysts, researchers, commentators) across multiple content sources,
processes their output through NLP and LLM pipelines, and serves curated
intelligence via a REST API with an admin dashboard.

The system runs on Railway as four independent services sharing a single
PostgreSQL database. Each service scales independently and communicates
through the database (no inter-service RPC).

```
                                    ThinkTank Architecture

    ┌─────────────────────────────────────────────────────────────────────────┐
    │                          Railway Platform                              │
    │                                                                       │
    │   ┌─────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────┐  │
    │   │   API        │  │  Worker CPU  │  │  Worker GPU  │  │  Admin   │  │
    │   │   :8000      │  │  (polling)   │  │  (polling)   │  │  :8001   │  │
    │   │             │  │             │  │             │  │          │  │
    │   │  FastAPI     │  │  Scraping    │  │  Parakeet    │  │ FastAPI  │  │
    │   │  REST API    │  │  LLM Review  │  │  ASR/NLP     │  │ Admin UI │  │
    │   │  Health      │  │  Categorize  │  │  Whisper     │  │ CRUD     │  │
    │   └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  └────┬─────┘  │
    │          │                │                │              │         │
    │          └────────────────┴────────────────┴──────────────┘         │
    │                                  │                                  │
    │                          ┌───────┴───────┐                          │
    │                          │  PostgreSQL   │                          │
    │                          │  (Railway)    │                          │
    │                          │  14 tables    │                          │
    │                          └───────────────┘                          │
    └─────────────────────────────────────────────────────────────────────────┘
```

## Service Architecture

### API Service (`Dockerfile.api`, port 8000)

The public-facing REST API. Handles authentication, rate limiting, and serves
curated intelligence data to consumers.

**Responsibilities:**
- REST endpoints for thinkers, content, categories, and search
- JWT authentication and API key validation
- Rate limiting (per-key and global)
- CORS handling with configurable origins
- Health checks with database connectivity verification

**Startup sequence:**
1. Run Alembic migrations (`alembic upgrade head`) with advisory lock
2. Start Uvicorn ASGI server on port 8000

**Key modules:**
- `src/thinktank/api/main.py` - FastAPI app with lifespan, middleware stack
- `src/thinktank/api/health.py` - Health check endpoint (`/health`)
- `src/thinktank/api/middleware.py` - Correlation ID middleware
- `src/thinktank/api/dependencies.py` - Dependency injection (DB session)

### Worker CPU Service (`Dockerfile.worker-cpu`)

Background job processor for CPU-bound tasks. Polls the `jobs` table for
pending work and processes items sequentially or in parallel.

**Responsibilities:**
- Web scraping (RSS, YouTube, podcast feeds)
- LLM-based content review and scoring
- Thinker categorization and metadata extraction
- Content deduplication

**Runtime:** Long-running Python process, no HTTP server.

### Worker GPU Service (`Dockerfile.worker-gpu`)

Specialized worker for GPU-accelerated NLP tasks. Built on NVIDIA NeMo
container for access to Parakeet ASR and other GPU models.

**Responsibilities:**
- Speech-to-text transcription (Parakeet ASR)
- Audio/video content processing
- GPU-accelerated NLP pipelines

**Runtime:** Long-running Python process with NVIDIA GPU access.
Model cache persists via `NEMO_CACHE_DIR` on Railway persistent volume.

### Admin Service (`Dockerfile.admin`, port 8001)

Internal dashboard for managing thinkers, reviewing content, and monitoring
system health. Not exposed to external consumers.

**Responsibilities:**
- Thinker CRUD management (add, edit, approve candidates)
- Content review and moderation
- System configuration management
- Job queue monitoring and manual triggers


## Data Flow

```
  External Sources                Processing Pipeline              Consumers
  ────────────────               ────────────────────             ──────────

  RSS Feeds ─────┐
                 │
  YouTube ───────┤   ┌────────────────────────────────┐
                 ├──>│ Worker CPU                     │
  Podcasts ──────┤   │  1. Fetch content from source  │
                 │   │  2. Parse and normalize        │      ┌──────────┐
  Newsletters ───┘   │  3. Deduplicate                │      │ API      │
                     │  4. Store in content table     │      │ :8000    │
                     │  5. Queue for LLM review       │─────>│          │──> API
                     └─────────┬──────────────────────┘      │ REST     │   Consumers
                               │                             │ Endpoints│
                     ┌─────────┴──────────────────────┐      └──────────┘
                     │ Worker GPU                     │
                     │  1. Transcribe audio/video     │      ┌──────────┐
                     │  2. NLP entity extraction      │      │ Admin    │
                     │  3. Store results              │─────>│ :8001    │──> Internal
                     └────────────────────────────────┘      │ Dashboard│   Staff
                                                             └──────────┘
```

### Job Processing Flow

```
  ┌──────────┐     ┌──────────┐     ┌──────────┐     ┌──────────┐
  │ PENDING  │────>│ RUNNING  │────>│COMPLETED │     │  FAILED  │
  │          │     │          │──┐  │          │     │          │
  └──────────┘     └──────────┘  │  └──────────┘     └──────────┘
                                 │                        ^
                                 └────────────────────────┘
                                   (on error, retry logic)
```

Jobs are created with `status='pending'` and a `job_type` that determines
which worker processes them. Workers poll for jobs matching their capabilities.


## Database Schema

14 tables organized by domain concern:

```
  ┌─────────────────────────────────────────────────────────────────┐
  │                    Core Domain Models                          │
  │                                                                │
  │  ┌──────────────┐    ┌───────────────┐    ┌────────────────┐  │
  │  │   thinkers    │───>│thinker_profiles│   │candidate_      │  │
  │  │              │    │               │    │thinkers        │  │
  │  │ name, slug,  │    │ bio, photo,   │    │               │  │
  │  │ status,      │    │ credentials   │    │ name, source,  │  │
  │  │ source_type  │    └───────────────┘    │ review_status  │  │
  │  └──────┬───────┘                         └────────────────┘  │
  │         │                                                      │
  │         │ 1:N                                                  │
  │         v                                                      │
  │  ┌──────────────┐    ┌───────────────┐                        │
  │  │   content     │───>│content_thinkers│  (M:N join table)    │
  │  │              │    └───────────────┘                        │
  │  │ title, body, │                                             │
  │  │ url, pubdate,│    ┌───────────────┐                        │
  │  │ content_type │───>│  llm_reviews   │                       │
  │  └──────────────┘    │               │                        │
  │                      │ score, summary,│                        │
  │                      │ model, prompt  │                        │
  │                      └───────────────┘                        │
  └─────────────────────────────────────────────────────────────────┘

  ┌─────────────────────────────────────────────────────────────────┐
  │                    Classification                              │
  │                                                                │
  │  ┌──────────────┐    ┌───────────────────┐                    │
  │  │  categories   │───>│thinker_categories │  (M:N join table) │
  │  │              │    └───────────────────┘                    │
  │  │ name, slug,  │                                             │
  │  │ parent_id    │    (self-referencing for hierarchy)         │
  │  └──────────────┘                                             │
  └─────────────────────────────────────────────────────────────────┘

  ┌─────────────────────────────────────────────────────────────────┐
  │                 Operations & Monitoring                        │
  │                                                                │
  │  ┌──────────────┐  ┌────────────────┐  ┌──────────────────┐   │
  │  │    jobs       │  │  api_usage     │  │ rate_limit_usage │   │
  │  │             │  │               │  │                 │   │
  │  │ job_type,   │  │ endpoint,     │  │ api_key,       │   │
  │  │ status,     │  │ method,       │  │ window_start,  │   │
  │  │ payload,    │  │ status_code,  │  │ request_count  │   │
  │  │ result,     │  │ response_ms   │  └──────────────────┘   │
  │  │ error_msg   │  └────────────────┘                         │
  │  └──────────────┘                                             │
  │                                                                │
  │  ┌──────────────┐  ┌────────────────┐                         │
  │  │   sources     │  │ system_config  │                         │
  │  │             │  │               │                         │
  │  │ url, type,  │  │ key, value,   │                         │
  │  │ check_freq  │  │ description   │                         │
  │  └──────────────┘  └────────────────┘                         │
  │                                                                │
  │  ┌──────────────────┐                                          │
  │  │ thinker_metrics  │                                          │
  │  │                │                                          │
  │  │ content_count, │                                          │
  │  │ avg_score,     │                                          │
  │  │ last_activity  │                                          │
  │  └──────────────────┘                                          │
  └─────────────────────────────────────────────────────────────────┘
```

### Key Relationships

| Relationship                  | Type | Description                       |
|-------------------------------|------|-----------------------------------|
| thinkers -> thinker_profiles  | 1:1  | Extended profile data             |
| thinkers -> thinker_metrics   | 1:1  | Aggregated performance metrics    |
| thinkers <-> content          | M:N  | Via content_thinkers join table   |
| thinkers <-> categories       | M:N  | Via thinker_categories join table |
| content -> llm_reviews        | 1:N  | Multiple LLM reviews per content |
| content -> sources            | N:1  | Content originates from a source  |
| categories -> categories      | Self | Hierarchical category tree        |
| thinkers -> candidate_thinkers| -    | Separate table for pending review |


## Configuration Architecture

Configuration uses pydantic-settings with three-tier precedence:

```
  Priority (highest to lowest):
  ┌─────────────────────────┐
  │ Environment Variables   │  <-- Railway sets these in production
  ├─────────────────────────┤
  │ .env File               │  <-- Local development overrides
  ├─────────────────────────┤
  │ Code Defaults           │  <-- Sensible development defaults
  └─────────────────────────┘
```

**Settings singleton:** `get_settings()` uses `@lru_cache` to load once and
reuse. Call `get_settings.cache_clear()` in tests to reset between test cases.

**Key settings:**

| Setting          | Default                                      | Description            |
|------------------|----------------------------------------------|------------------------|
| `database_url`   | `postgresql+asyncpg://...localhost:5432/...`  | Async database URL     |
| `db_pool_size`   | 10                                           | Connection pool size   |
| `db_max_overflow` | 5                                           | Max overflow conns     |
| `debug`          | `false`                                      | Debug mode flag        |
| `service_name`   | `thinktank-api`                              | Service identifier     |
| `log_level`      | `INFO`                                       | Logging level          |
| `cors_origins`   | `["*"]`                                      | Allowed CORS origins   |


## Logging Architecture

Structured JSON logging via structlog with correlation ID propagation:

```
  Request arrives
       │
       v
  ┌──────────────────────────────┐
  │  CorrelationIDMiddleware     │
  │                              │
  │  1. Clear contextvars        │
  │  2. Generate UUID            │
  │  3. Bind to structlog ctx:   │
  │     - correlation_id         │
  │     - service name           │
  │  4. Call next middleware      │
  │  5. Add X-Correlation-ID     │
  │     response header          │
  └──────────────────────────────┘
       │
       v
  ┌──────────────────────────────┐
  │  Application Code            │
  │                              │
  │  logger = get_logger("mod")  │
  │  logger.info("message",      │
  │      key="value")            │
  │                              │
  │  Automatically includes:     │
  │  - correlation_id from ctx   │
  │  - service from ctx          │
  │  - timestamp (ISO 8601)      │
  │  - log_level                 │
  └──────────────────────────────┘
       │
       v
  JSON output (stdout):
  {
    "timestamp": "2026-01-15T10:30:00Z",
    "log_level": "info",
    "event": "message",
    "service": "thinktank-api",
    "correlation_id": "a1b2c3d4-...",
    "key": "value",
    "logger": "mod"
  }
```

**Log key naming:** The spec requires `log_level` (not `level`). A custom
structlog processor `_rename_level_to_log_level` handles this transformation.


## Migration Architecture

Alembic manages database schema migrations with async PostgreSQL support:

```
  Service Startup (API container)
       │
       v
  ┌──────────────────────────────┐
  │  alembic upgrade head        │
  │                              │
  │  1. Connect to PostgreSQL    │
  │  2. Acquire pg_advisory_lock │
  │     (lock ID: 1)             │
  │  3. Run pending migrations   │
  │  4. Release advisory lock    │
  │  5. Start application        │
  └──────────────────────────────┘
```

**Advisory lock** prevents corruption when multiple API containers start
simultaneously during a Railway deployment. Only one container runs migrations;
others wait for the lock then see no pending migrations.

**Migration commands:**
```bash
alembic revision --autogenerate -m "description"  # Generate migration
alembic upgrade head                               # Apply all migrations
alembic downgrade -1                               # Rollback one step
alembic downgrade base                             # Rollback everything
```


## Testing Architecture

Three-tier test strategy with real PostgreSQL (no mocks for data layer):

```
  ┌────────────────────────────────────────────────────────┐
  │                    Test Pyramid                        │
  │                                                        │
  │                    ┌────────┐                          │
  │                    │  E2E   │   (future: Playwright)   │
  │                   ┌┴────────┴┐                         │
  │                   │Integration│  Real PostgreSQL       │
  │                  ┌┴──────────┴┐ Models, migrations,   │
  │                  │   Unit      │ API endpoints          │
  │                  │             │                        │
  │                  │ Config,     │ No DB required         │
  │                  │ logging,    │                        │
  │                  │ pure logic  │                        │
  │                  └─────────────┘                        │
  └────────────────────────────────────────────────────────┘
```

**Test infrastructure:**
- `pytest` + `pytest-asyncio` with session-scoped event loop
- `httpx.AsyncClient` for API endpoint testing
- Real PostgreSQL via `docker-compose.test.yml` (port 5433)
- Factory functions for all 14 model types (`tests/factories.py`)
- TRUNCATE CASCADE cleanup between tests (fast, no schema recreation)

**Test database isolation:**
- Session-scoped engine creates tables once via `Base.metadata.create_all`
- Each test gets a fresh `AsyncSession`
- Autouse `_cleanup_tables` fixture truncates all tables after each test
- Migration tests use subprocess to avoid asyncio event loop conflicts

**Running tests:**
```bash
# Start test database
docker compose -f docker-compose.test.yml up -d

# Run all tests
uv run pytest

# Run by category
uv run pytest tests/unit/          # Unit tests (no DB needed)
uv run pytest tests/integration/   # Integration tests (needs DB)
```


## Directory Structure

```
ThinkTank/
├── alembic/
│   ├── env.py                    # Async migration runner with advisory lock
│   ├── script.py.mako            # Migration template
│   └── versions/
│       └── 92ce_initial.py       # Initial schema (14 tables)
├── alembic.ini                   # Alembic configuration
├── docker/
│   ├── Dockerfile.api            # API service (runs migrations + uvicorn)
│   ├── Dockerfile.admin          # Admin dashboard (uvicorn on 8001)
│   ├── Dockerfile.worker-cpu     # CPU worker (polling process)
│   └── Dockerfile.worker-gpu     # GPU worker (NeMo base image)
├── docker-compose.yml            # Local development stack
├── docker-compose.test.yml       # Test database only
├── docs/
│   └── architecture.md           # This file
├── pyproject.toml                # Dependencies and tool config
├── src/
│   └── thinktank/
│       ├── api/
│       │   ├── main.py           # FastAPI app, lifespan, middleware
│       │   ├── health.py         # Health check endpoint
│       │   ├── middleware.py     # Correlation ID middleware
│       │   └── dependencies.py   # DI: get_session()
│       ├── config.py             # Pydantic-settings configuration
│       ├── database.py           # SQLAlchemy async engine + session
│       ├── logging.py            # Structured JSON logging setup
│       └── models/
│           ├── __init__.py       # Re-exports all 14 models + Base
│           ├── base.py           # Declarative base with UUID PK mixin
│           ├── thinker.py        # Thinker, ThinkerProfile, ThinkerMetrics
│           ├── content.py        # Content, ContentThinker
│           ├── category.py       # Category, ThinkerCategory
│           ├── job.py            # Job (processing queue)
│           ├── review.py         # LLMReview
│           ├── source.py         # Source
│           ├── api_usage.py      # ApiUsage
│           ├── rate_limit.py     # RateLimitUsage
│           ├── candidate.py      # CandidateThinker
│           └── config_table.py   # SystemConfig
└── tests/
    ├── conftest.py               # Shared fixtures (engine, session, client)
    ├── factories.py              # Factory functions for all 14 models
    ├── unit/
    │   ├── test_config.py        # Configuration tests
    │   └── test_logging.py       # Logging output tests
    └── integration/
        ├── test_health.py        # Health endpoint + correlation ID tests
        ├── test_models.py        # Model CRUD + constraint tests
        └── test_migrations.py    # Alembic upgrade/downgrade tests
```
