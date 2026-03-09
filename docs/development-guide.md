# ThinkTank Development Guide

This guide covers how to extend the ThinkTank system: adding new job types, API endpoints, thinker categories, and working with the codebase conventions.

## 1. Project Structure

```
src/thinktank/
├── api/                    # REST API (FastAPI, port 8000)
│   ├── main.py             # FastAPI app with lifespan, middleware, router registration
│   ├── health.py           # Health check endpoint
│   ├── middleware.py        # CorrelationID middleware
│   ├── schemas.py          # Pydantic v2 request/response models
│   └── routers/            # Route handlers by domain
│       ├── thinkers.py     # /api/thinkers CRUD
│       ├── sources.py      # /api/sources list
│       ├── content.py      # /api/content list
│       ├── jobs.py         # /api/jobs/status
│       └── config.py       # /api/config CRUD
├── admin/                  # Admin dashboard (FastAPI + HTMX, port 8001)
│   ├── main.py             # Separate FastAPI app for admin
│   ├── dependencies.py     # Admin-specific DI (session, templates)
│   ├── routers/
│   │   ├── dashboard.py    # Dashboard page + 6 HTMX partials
│   │   ├── llm_panel.py    # LLM review panel + override
│   │   └── categories.py   # Category taxonomy CRUD
│   └── templates/          # Jinja2 HTML templates
│       ├── base.html       # Layout with HTMX CDN
│       ├── dashboard.html  # 6-widget dashboard grid
│       ├── llm_panel.html  # LLM approval interface
│       ├── categories.html # Category tree + create form
│       └── partials/       # HTMX fragment templates (10 files)
├── models/                 # SQLAlchemy 2.0 ORM models
│   ├── base.py             # Base, TimestampMixin, uuid_pk type alias
│   ├── thinker.py          # Thinker, ThinkerProfile, ThinkerMetrics
│   ├── source.py           # Source (RSS feeds)
│   ├── content.py          # Content, ContentThinker (junction)
│   ├── category.py         # Category, ThinkerCategory (junction)
│   ├── job.py              # Job (queue entries)
│   ├── review.py           # LLMReview (audit trail)
│   ├── config_table.py     # SystemConfig (key-value)
│   ├── candidate.py        # CandidateThinker
│   ├── rate_limit.py       # RateLimitUsage
│   └── api_usage.py        # ApiUsage (cost rollups)
├── handlers/               # Job handler implementations
│   ├── base.py             # JobHandler Protocol definition
│   ├── registry.py         # Handler registry (job_type -> handler mapping)
│   ├── fetch_podcast_feed.py
│   ├── refresh_due_sources.py
│   ├── tag_content_thinkers.py
│   ├── process_content.py
│   ├── llm_approval_check.py
│   ├── scan_for_candidates.py
│   ├── discover_guests_listennotes.py
│   ├── discover_guests_podcastindex.py
│   └── rollup_api_usage.py
├── queue/                  # Job queue engine
│   ├── claim.py            # claim_job, complete_job, fail_job
│   ├── errors.py           # ErrorCategory enum + categorize_error()
│   ├── rate_limiter.py     # Sliding-window rate limiter
│   ├── backpressure.py     # Priority demotion under load
│   ├── reclaim.py          # Stale job reclamation
│   └── kill_switch.py      # Global worker halt via system_config
├── worker/                 # Worker loop
│   └── loop.py             # Poll/claim/dispatch cycle
├── ingestion/              # Content ingestion pure logic
│   ├── url_normalizer.py   # URL canonicalization
│   ├── fingerprint.py      # Content fingerprinting
│   ├── feed_parser.py      # RSS/Atom feed parsing
│   ├── content_filter.py   # Duration/title filtering
│   └── name_matcher.py     # Thinker name matching
├── transcription/          # Transcription pipeline
│   ├── captions.py         # YouTube caption extraction
│   ├── existing.py         # Existing transcript detection
│   ├── audio.py            # Audio download + conversion
│   └── gpu_client.py       # GPU service client
├── llm/                    # LLM governance
│   ├── client.py           # Anthropic client wrapper
│   ├── prompts.py          # Prompt templates
│   ├── snapshots.py        # Bounded context snapshots
│   ├── decisions.py        # Decision application logic
│   └── time_utils.py       # Time helpers for scheduling
├── discovery/              # Discovery modules
│   ├── name_extractor.py   # Regex name extraction
│   ├── listennotes.py      # Listen Notes API client
│   ├── podcastindex.py     # Podcast Index API client
│   └── quota.py            # Daily quota tracker
├── scaling/                # GPU scaling
│   └── railway.py          # Railway API client
├── gpu_worker/             # GPU worker service
├── config.py               # Settings (pydantic-settings)
├── database.py             # Engine + session factory
└── logging.py              # Structured logging setup
```

## 2. Adding a New Job Type

Job types are the primary extension point. Each job type has a handler that processes jobs of that type.

### Step 1: Create the Handler

Create a new file in `src/thinktank/handlers/`:

```python
# src/thinktank/handlers/cleanup_old_content.py
"""Handler: cleanup_old_content -- Remove content older than retention period."""

from __future__ import annotations

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.thinktank.models.job import Job

logger = structlog.get_logger(__name__)


async def handle_cleanup_old_content(session: AsyncSession, job: Job) -> None:
    """Delete content rows older than the configured retention period.

    Payload:
        retention_days (int): Number of days to keep content. Default 365.

    Side effects:
        - Deletes content rows with created_at older than retention_days
        - Logs count of deleted rows
    """
    retention_days = job.payload.get("retention_days", 365)

    result = await session.execute(
        text("""
            DELETE FROM content
            WHERE created_at < LOCALTIMESTAMP - MAKE_INTERVAL(days => :days)
            AND status = 'processed'
        """),
        {"days": retention_days},
    )

    await logger.ainfo(
        "cleanup_old_content.done",
        deleted=result.rowcount,
        retention_days=retention_days,
    )
```

**Key conventions:**
- Import from `src.thinktank.*` (not `thinktank.*`) to match project convention
- Handler signature must match `JobHandler` protocol: `async def(session: AsyncSession, job: Job) -> None`
- Raise exceptions on failure (the worker loop catches, categorizes, and calls `fail_job`)
- Use `structlog` for structured logging
- Access job parameters via `job.payload` (JSONB dict)
- Use `LOCALTIMESTAMP` and `MAKE_INTERVAL` for timezone-naive datetime comparisons

### Step 2: Register in the Handler Registry

Add the handler to `src/thinktank/handlers/registry.py`:

```python
# At the top, add the import
from src.thinktank.handlers.cleanup_old_content import handle_cleanup_old_content

# At the bottom, register it
register_handler("cleanup_old_content", handle_cleanup_old_content)
```

The registry maps `job_type` strings to handler callables. The worker loop uses `get_handler(job_type)` to dispatch.

### Step 3: Create a Contract Test

Create `tests/contract/test_cleanup_handler.py`:

```python
"""Contract tests for cleanup_old_content handler."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tests.factories import create_content, create_job

pytestmark = pytest.mark.anyio


class TestCleanupOldContent:
    """cleanup_old_content handler removes old processed content."""

    async def test_deletes_old_processed_content(self, session: AsyncSession):
        from src.thinktank.handlers.cleanup_old_content import handle_cleanup_old_content

        # Create old content (use factory with date override)
        # ...create test data...

        job = await create_job(
            session,
            job_type="cleanup_old_content",
            payload={"retention_days": 30},
        )
        await session.commit()

        await handle_cleanup_old_content(session, job)
        await session.commit()

        # Assert old content was deleted
        # Assert recent content was preserved
```

**Contract test conventions:**
- File lives in `tests/contract/`
- Uses `pytestmark = pytest.mark.anyio`
- Uses factory functions from `tests/factories.py`
- Tests the handler's expected side effects given a known input payload

### Step 4: Add Error Categories (If Needed)

If your handler can produce new error types, extend `src/thinktank/queue/errors.py`:

```python
class ErrorCategory(StrEnum):
    # ... existing categories ...

    # Your new category
    CLEANUP_FAILED = "cleanup_failed"
```

And update `categorize_error()` if you have specific exception types to map.

### Step 5: Enqueue Jobs

To trigger your handler, create job rows:

```python
from src.thinktank.models.job import Job

job = Job(
    job_type="cleanup_old_content",
    payload={"retention_days": 90},
    priority=8,  # Low priority (1=highest, 10=lowest)
)
session.add(job)
```

Or schedule recurring jobs via the worker loop's scheduler pattern (see `src/thinktank/worker/loop.py`).

## 3. Adding a New API Endpoint

### Step 1: Add Pydantic Schemas

Add request/response models to `src/thinktank/api/schemas.py`:

```python
# ---------- YourEntity schemas ----------

class YourEntityResponse(BaseModel):
    """Response model for your entity."""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    created_at: datetime


class YourEntityCreate(BaseModel):
    """Request model for creating your entity."""
    name: str
```

**Schema conventions:**
- All response models use `model_config = ConfigDict(from_attributes=True)` for ORM compatibility
- Use `PaginatedResponse[YourEntityResponse]` for list endpoints
- Request models (Create/Update) do not need `from_attributes`

### Step 2: Create a Router

Create `src/thinktank/api/routers/your_entity.py`:

```python
"""YourEntity API endpoints."""

from fastapi import APIRouter, Depends
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from src.thinktank.api.schemas import (
    PaginatedResponse,
    YourEntityCreate,
    YourEntityResponse,
)
from thinktank.api.dependencies import get_session

router = APIRouter(prefix="/api/your-entities", tags=["your-entities"])


@router.get("/", response_model=PaginatedResponse[YourEntityResponse])
async def list_entities(
    page: int = 1,
    size: int = 20,
    session: AsyncSession = Depends(get_session),
):
    """List entities with pagination."""
    offset = (page - 1) * size

    # Count total
    count_q = select(func.count()).select_from(YourEntity)
    total = (await session.execute(count_q)).scalar()

    # Fetch page
    q = select(YourEntity).offset(offset).limit(size)
    result = await session.execute(q)
    items = result.scalars().all()

    return PaginatedResponse(
        items=items,
        total=total,
        page=page,
        size=size,
        pages=(total + size - 1) // size if total else 0,
    )
```

**Router conventions:**
- Use `APIRouter(prefix="/api/...", tags=["..."])` for grouping
- Use `Depends(get_session)` for database access
- SQLAlchemy 2.0 style: `select()` + `session.execute()`
- Return Pydantic models, not raw dicts

### Step 3: Include Router in the App

Add to `src/thinktank/api/main.py`:

```python
from thinktank.api.routers.your_entity import router as your_entity_router

# In the router includes section:
app.include_router(your_entity_router)
```

### Step 4: Write Contract Tests

Create `tests/contract/test_api_your_entity.py`:

```python
"""Contract tests for /api/your-entities endpoints."""

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.anyio


class TestListEntities:
    async def test_returns_paginated_response(self, client: AsyncClient):
        resp = await client.get("/api/your-entities/")
        assert resp.status_code == 200
        body = resp.json()
        assert "items" in body
        assert "total" in body
        assert "page" in body
        assert "size" in body
        assert "pages" in body

    async def test_empty_list(self, client: AsyncClient):
        resp = await client.get("/api/your-entities/")
        assert resp.json()["total"] == 0
        assert resp.json()["items"] == []
```

**Test conventions:**
- Use the `client` fixture from `tests/conftest.py` (AsyncClient with ASGITransport)
- Test response status codes and body shapes
- Test empty states, pagination boundaries, and error responses

## 4. Adding a New Thinker Category

### Option A: Via Admin Dashboard

1. Open `http://localhost:8001/admin/categories/`
2. Fill in the "Add Category" form:
   - **Slug**: lowercase, hyphenated (e.g., `machine-learning`)
   - **Name**: display name (e.g., "Machine Learning")
   - **Description**: brief description
   - **Parent**: select parent category for subcategories, or leave empty for top-level
3. Click "Create"

### Option B: Via Seed Script

Add to the `TAXONOMY` dict in `scripts/seed_categories.py`:

```python
TAXONOMY = {
    # ... existing categories ...
    "your-new-category": (
        "Your New Category",
        "Description of the category",
        {
            "subcategory-slug": ("Subcategory Name", "Subcategory description"),
        },
    ),
}
```

Then re-run the seeder (idempotent):

```bash
uv run python -m scripts.seed_categories
```

### Category Naming Conventions

- **Slug**: lowercase, hyphenated, no spaces (e.g., `artificial-intelligence`)
- **Name**: title case, human-readable (e.g., "Artificial Intelligence")
- **Description**: one sentence explaining the category scope
- **Hierarchy**: maximum 2 levels deep (parent -> child). Do not create grandchild categories.

### Option C: Via API

```bash
# Categories are managed through the admin dashboard, not the REST API.
# Use the admin dashboard at http://localhost:8001/admin/categories/
```

## 5. Testing Conventions

### Test Pyramid

```
tests/
├── unit/           # Pure logic tests (no DB, no I/O)
├── integration/    # Real PostgreSQL tests
├── contract/       # Endpoint shape + handler side-effect tests
├── fixtures/       # RSS feed XML fixtures, etc.
├── factories.py    # Factory functions for all 14 models
└── conftest.py     # Shared fixtures (engine, session, client, cleanup)
```

| Level | What It Tests | Database | Speed |
|-------|--------------|----------|-------|
| Unit | Pure functions, parsing, normalization | No | Fast |
| Integration | DB queries, model relationships, transactions | Yes (PostgreSQL) | Medium |
| Contract | API endpoint shapes, handler side effects | Yes (PostgreSQL) | Medium |

### Factory Functions

All test data creation uses factory functions from `tests/factories.py`:

```python
from tests.factories import create_thinker, create_job, create_category

# In-memory (no DB):
thinker = make_thinker(name="Test", slug="test")

# Persisted to DB (requires session fixture):
thinker = await create_thinker(session, name="Test", slug="test")
```

Every field is overridable. UUIDs and slugs are auto-generated to be unique.

### Key Fixtures

| Fixture | Scope | Source | Purpose |
|---------|-------|--------|---------|
| `engine` | session | `tests/conftest.py` | Async SQLAlchemy engine for test DB |
| `session_factory` | session | `tests/conftest.py` | Session factory bound to test engine |
| `session` | function | `tests/conftest.py` | Per-test async session |
| `_cleanup_tables` | function | `tests/conftest.py` | TRUNCATE CASCADE after each test |
| `_auto_cleanup` | function (autouse) | `tests/integration/conftest.py` | Auto-applies cleanup for integration tests |
| `client` | function | `tests/conftest.py` | AsyncClient for API contract tests |

### Running Tests

```bash
# Quick unit tests only (no DB needed)
uv run pytest tests/unit -x -q

# Integration tests (requires PostgreSQL)
uv run pytest tests/integration -x -v

# Contract tests (requires PostgreSQL)
uv run pytest tests/contract -x -v

# Full test suite
uv run pytest tests/ -x --timeout=60

# Single test file
uv run pytest tests/integration/test_bootstrap.py -x -v

# Single test class or method
uv run pytest tests/integration/test_bootstrap.py::TestSeedCategories -x -v
```

### Async Test Pattern

All async test files must include:

```python
import pytest

pytestmark = pytest.mark.anyio
```

This marks all tests in the file as async-compatible. The project uses `asyncio_mode = "auto"` in `pyproject.toml`.

## 6. Database Conventions

### SQLAlchemy 2.0 Style

Always use the 2.0 query API:

```python
# Correct (2.0 style)
result = await session.execute(select(Thinker).where(Thinker.slug == "test"))
thinker = result.scalar_one_or_none()

# Incorrect (1.x style -- do NOT use)
thinker = await session.query(Thinker).filter_by(slug="test").first()
```

### Timezone-Naive Datetimes

The project uses `TIMESTAMP WITHOUT TIME ZONE` throughout. All Python datetimes must be timezone-naive:

```python
from datetime import UTC, datetime

# Correct
now = datetime.now(UTC).replace(tzinfo=None)

# Incorrect -- will cause asyncpg errors
now = datetime.now(UTC)  # Has tzinfo
now = datetime.utcnow()  # Deprecated
```

For raw SQL datetime comparisons, use `LOCALTIMESTAMP` (not `NOW()` which returns timezone-aware):

```python
from sqlalchemy import text

# Correct
await session.execute(text("SELECT * FROM jobs WHERE created_at < LOCALTIMESTAMP - INTERVAL '1 hour'"))

# For parameterized intervals
await session.execute(
    text("SELECT * FROM jobs WHERE created_at < LOCALTIMESTAMP - MAKE_INTERVAL(mins => :mins)"),
    {"mins": 30},
)
```

### JSONB for Flexible Data

System config values, job payloads, and profile data use PostgreSQL JSONB:

```python
from sqlalchemy.dialects.postgresql import JSONB

# SystemConfig stores raw primitives (not nested objects)
# Good: value = True, value = 20, value = "active"
# Bad:  value = {"enabled": True}

# Job payload is a free-form dict
job = Job(
    job_type="my_handler",
    payload={"key1": "value1", "count": 42},
)
```

### Upserts with ON CONFLICT

For idempotent operations, use PostgreSQL's `INSERT ... ON CONFLICT DO UPDATE`:

```python
from sqlalchemy.dialects.postgresql import insert

stmt = insert(SystemConfig).values(
    key="my_key",
    value=42,
    set_by="seed",
).on_conflict_do_update(
    index_elements=["key"],  # Column(s) with unique constraint
    set_={"value": 42, "set_by": "seed"},
)
await session.execute(stmt)
```

### Advisory Locks

For operations that must not run concurrently (e.g., migrations):

```python
from sqlalchemy import text

# Acquire lock (blocks until available)
await conn.execute(text("SELECT pg_advisory_lock(1)"))
# ... do work ...
await conn.execute(text("SELECT pg_advisory_unlock(1)"))
```

## 7. Deployment

### Railway Services

ThinkTank deploys as 4 Railway services:

| Service | Dockerfile | Port | Entry Point |
|---------|-----------|------|-------------|
| API | `Dockerfile.api` | 8000 | `uvicorn src.thinktank.api.main:app` |
| Admin | `Dockerfile.admin` | 8001 | `uvicorn src.thinktank.admin.main:app` |
| CPU Worker | `Dockerfile.worker-cpu` | -- | `python -m src.thinktank.worker.loop` |
| GPU Worker | `Dockerfile.worker-gpu` | -- | GPU transcription service |

### Required Environment Variables

| Variable | Service | Purpose |
|----------|---------|---------|
| `DATABASE_URL` | All | PostgreSQL connection (asyncpg format) |
| `ANTHROPIC_API_KEY` | CPU Worker | Claude API for LLM governance |
| `RAILWAY_API_KEY` | CPU Worker | Railway API for GPU scaling |
| `LOG_LEVEL` | All | Logging level (default: INFO) |
| `SERVICE_NAME` | All | Service identifier for structured logs |
| `DEBUG` | All | Debug mode (default: false) |

### Local Development Setup

```bash
# 1. Clone the repository
git clone <repo-url>
cd ThinkTank

# 2. Install dependencies
uv sync --all-groups

# 3. Start PostgreSQL (Docker)
docker compose up -d db

# 4. Set environment
export DATABASE_URL="postgresql+asyncpg://thinktank:thinktank@localhost:5432/thinktank"

# 5. Run migrations
uv run alembic upgrade head

# 6. Bootstrap (seed data)
uv run python -m scripts.bootstrap

# 7. Start API server
uv run uvicorn src.thinktank.api.main:app --reload --port 8000

# 8. Start Admin dashboard (separate terminal)
uv run uvicorn src.thinktank.admin.main:app --reload --port 8001

# 9. Run tests
export TEST_DATABASE_URL="postgresql+asyncpg://thinktank_test:thinktank_test@localhost:5433/thinktank_test"
uv run pytest tests/ -x --timeout=60
```

### Database Migrations

```bash
# Create a new migration
uv run alembic revision --autogenerate -m "description of change"

# Apply all pending migrations
uv run alembic upgrade head

# Rollback one migration
uv run alembic downgrade -1

# Show current migration status
uv run alembic current
```

**Note:** Migrations use advisory locks (`pg_advisory_lock(1)`) to prevent concurrent migration corruption. The lock is acquired in `env.py` before running migrations.
