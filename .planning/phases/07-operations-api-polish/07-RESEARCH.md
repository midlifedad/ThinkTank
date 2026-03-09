# Phase 7: Operations, API, and Polish - Research

**Researched:** 2026-03-08
**Domain:** Admin dashboard (HTMX + FastAPI), REST API, cost tracking, bootstrap scripts, operational documentation
**Confidence:** HIGH

## Summary

Phase 7 is the final milestone phase, building the operational and programmatic access layers on top of the fully functional ingestion pipeline (Phases 1-6 complete, 582 tests passing). The work spans five distinct domains: (1) an HTMX-powered admin dashboard for human oversight of the LLM Supervisor and pipeline health, (2) RESTful CRUD endpoints for thinkers/sources/content with OpenAPI docs, (3) API cost tracking via a `rollup_api_usage` handler, (4) bootstrap seed scripts for fresh deployments, and (5) operations/development documentation.

The existing codebase provides strong foundations: all 14 SQLAlchemy models exist, the FastAPI app scaffold has health endpoints with correlation ID middleware, the `api_usage` and `rate_limit_usage` models are ready, the LLM `escalation.py` module already flags timed-out reviews for human action, and a `Dockerfile.admin` is pre-configured pointing at `src.thinktank.admin.main:app`. The admin dashboard is a separate FastAPI service (port 8001) from the API service, as specified in Section 10.1 of the spec.

**Primary recommendation:** Build in three plans: (1) REST API endpoints + contract tests + OpenAPI + rollup handler, (2) Admin dashboard with HTMX auto-refresh + LLM decision panel + override functionality, (3) Bootstrap seed scripts + operations runbook + development guide.

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| OPS-01 | Admin dashboard (HTMX + FastAPI) showing queue depth, error log, source health, GPU status | Jinja2Templates + HTMX `hx-trigger="every 10s"` pattern; separate FastAPI app at `src.thinktank.admin.main` |
| OPS-02 | LLM decision panel -- view pending approvals, recent decisions, override with audit trail | LLMReview model has `overridden_by`, `overridden_at`, `override_reasoning` fields; escalation.py flags timed-out reviews |
| OPS-03 | API cost tracking via `api_usage` table with hourly rollups and estimated USD costs | `rollup_api_usage` handler needed; aggregate from `rate_limit_usage` into `api_usage`; cost map per API |
| OPS-04 | Rate limit gauges showing current usage vs configured limits per external API | Query `rate_limit_usage` with sliding window COUNT vs `system_config` limits |
| OPS-05 | Category taxonomy management in admin dashboard | Category model with self-referential parent/children relationships already exists |
| OPS-06 | Bootstrap sequence -- seed categories, config, thinkers, first LLM review, activate workers | Scripts at `scripts/seed_categories.py`, `scripts/seed_config.py`, `scripts/seed_thinkers.py`, `scripts/run_initial_llm_review.py` per spec Section 10.3 |
| API-01 | RESTful endpoints for thinkers (CRUD, list with filtering by category/tier/status) | FastAPI router with Pydantic response models, SQLAlchemy async queries with selectin joins |
| API-02 | RESTful endpoints for sources (list by thinker, approval status filtering) | Same pattern; Source model with thinker FK |
| API-03 | RESTful endpoints for content (list by source/thinker, pagination, status filtering) | Offset/limit pagination with total count; Content model with source/thinker FKs |
| API-04 | Job queue status endpoint (counts by type/status, recent errors) | Aggregate query on jobs table with GROUP BY |
| API-05 | System config read/write endpoints for operational parameters | SystemConfig model with TEXT PK; upsert semantics |
| API-06 | OpenAPI auto-generated documentation | FastAPI built-in at `/docs` (Swagger) and `/redoc`; already configured on the app |
| QUAL-03 | Contract tests for every API endpoint (request/response shape, status codes, error formats) | httpx AsyncClient with ASGI transport (existing conftest.py pattern) |
| QUAL-05 | Operations runbook covering bootstrap, post-deploy verification, rollback, common problems | Markdown document in `docs/operations-runbook.md` |
| QUAL-07 | Development guide covering how to add new job types, API endpoints, thinker categories | Markdown document in `docs/development-guide.md` |
</phase_requirements>

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| FastAPI | >=0.135.1 | REST API + admin dashboard (two separate apps) | Already in use; built-in OpenAPI, Pydantic validation |
| Jinja2 | >=3.1 | Server-side HTML templates for admin dashboard | FastAPI native support via `Jinja2Templates`; required for HTMX pattern |
| HTMX | 2.0 (CDN) | 10-second auto-refresh, form submissions, partial swaps | Spec mandates "without a JavaScript framework"; HTMX is the standard |
| SQLAlchemy | >=2.0.46 | Async ORM queries for all endpoints | Already in use throughout |
| Pydantic | >=2.12.5 | Request/response schema validation | Already in use; powers OpenAPI docs |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| python-multipart | >=0.0.9 | Form data parsing for admin dashboard POST submissions | Required by FastAPI for form handling in override panel |
| jinja2 | >=3.1 | Template engine (FastAPI dependency, may need explicit install) | Admin dashboard HTML rendering |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| HTMX polling | Server-Sent Events (SSE) | SSE is more efficient but adds complexity; polling at 10s is spec-mandated and simpler |
| Custom pagination | fastapi-pagination library | Adds dependency for simple offset/limit; hand-rolling is 15 lines and matches project convention |
| Separate admin framework | FastAPI-Admin or Starlette-Admin | Over-engineered for this use case; spec is clear about HTMX + custom templates |

**Installation:**
```bash
uv add jinja2 python-multipart
```

Note: HTMX is loaded via CDN (`<script src="https://unpkg.com/htmx.org@2.0.4"></script>`) -- no Python package needed.

## Architecture Patterns

### Recommended Project Structure
```
src/thinktank/
├── api/
│   ├── main.py              # Existing API app (enhanced)
│   ├── dependencies.py      # Existing session dependency
│   ├── health.py            # Existing health endpoint
│   ├── middleware.py         # Existing correlation ID
│   ├── schemas.py            # NEW: Pydantic response/request models
│   ├── routers/              # NEW: API endpoint routers
│   │   ├── __init__.py
│   │   ├── thinkers.py       # CRUD + filtering for thinkers
│   │   ├── sources.py        # CRUD + filtering for sources
│   │   ├── content.py        # List + filtering for content
│   │   ├── jobs.py           # Queue status endpoint
│   │   └── config.py         # System config read/write
├── admin/
│   ├── __init__.py           # NEW
│   ├── main.py               # NEW: Separate FastAPI app for admin
│   ├── dependencies.py       # NEW: Admin session + config helpers
│   ├── routers/              # NEW: Admin route handlers
│   │   ├── __init__.py
│   │   ├── dashboard.py      # Queue depth, errors, source health, GPU
│   │   ├── llm_panel.py      # Pending approvals, decisions, overrides
│   │   ├── categories.py     # Category taxonomy CRUD
│   │   └── costs.py          # API cost tracking views
│   └── templates/            # NEW: Jinja2 HTML templates
│       ├── base.html         # Layout with HTMX CDN, nav
│       ├── dashboard.html    # Main dashboard page
│       ├── partials/         # HTMX-swappable fragments
│       │   ├── queue_depth.html
│       │   ├── error_log.html
│       │   ├── source_health.html
│       │   ├── gpu_status.html
│       │   ├── rate_limits.html
│       │   ├── cost_tracker.html
│       │   └── llm_status.html
│       ├── llm_panel.html    # LLM decisions page
│       ├── categories.html   # Category management
│       └── overrides.html    # Override form
├── handlers/
│   ├── rollup_api_usage.py   # NEW: Hourly cost rollup handler
│   └── registry.py           # Updated with rollup handler
scripts/                       # NEW directory
├── seed_categories.py         # Idempotent category taxonomy seeder
├── seed_config.py             # Idempotent system_config defaults
├── seed_thinkers.py           # Initial thinker list seeder
└── run_initial_llm_review.py  # First LLM batch review + worker activation
docs/
├── architecture.md            # Existing
├── operations-runbook.md      # NEW
└── development-guide.md       # NEW
```

### Pattern 1: HTMX Auto-Refresh with Partial Swaps
**What:** Dashboard sections auto-refresh every 10 seconds by fetching HTML fragments from dedicated endpoints.
**When to use:** Every dashboard widget that displays live data.
**Example:**
```html
<!-- In dashboard.html -->
<div id="queue-depth"
     hx-get="/admin/partials/queue-depth"
     hx-trigger="load, every 10s"
     hx-swap="innerHTML">
    Loading...
</div>
```
```python
# In admin/routers/dashboard.py
@router.get("/partials/queue-depth")
async def queue_depth_partial(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Return HTML fragment with current queue depth by job type."""
    result = await session.execute(
        text("SELECT job_type, status, COUNT(*) FROM jobs GROUP BY job_type, status")
    )
    rows = result.fetchall()
    return templates.TemplateResponse(
        "partials/queue_depth.html",
        {"request": request, "queue_data": rows},
    )
```

### Pattern 2: Pydantic Response Schemas for REST API
**What:** Separate input/output Pydantic models for each resource, with a generic paginated response wrapper.
**When to use:** All REST API endpoints.
**Example:**
```python
# In api/schemas.py
from pydantic import BaseModel
from datetime import datetime
from uuid import UUID

class ThinkerResponse(BaseModel):
    id: UUID
    name: str
    slug: str
    tier: int
    bio: str
    approval_status: str
    active: bool
    added_at: datetime

    model_config = {"from_attributes": True}

class PaginatedResponse[T](BaseModel):
    items: list[T]
    total: int
    page: int
    size: int
    pages: int

class ThinkerCreate(BaseModel):
    name: str
    slug: str
    tier: int
    bio: str
    # submitted to LLM approval queue
```

### Pattern 3: Human Override with Audit Trail
**What:** Admin overrides an LLM decision via POST form, logging username + reasoning to `llm_reviews`.
**When to use:** LLM decision panel override functionality.
**Example:**
```python
# In admin/routers/llm_panel.py
@router.post("/override/{review_id}")
async def override_decision(
    review_id: UUID,
    request: Request,
    session: AsyncSession = Depends(get_session),
    override_decision: str = Form(...),
    override_reasoning: str = Form(...),
    admin_username: str = Form("admin"),
):
    review = await session.get(LLMReview, review_id)
    review.overridden_by = admin_username
    review.overridden_at = _now()
    review.override_reasoning = override_reasoning
    # Apply the override decision to the target entity
    await _apply_override(session, review, override_decision)
    await session.commit()
    # Return updated partial for HTMX swap
    return templates.TemplateResponse(...)
```

### Pattern 4: Idempotent Seed Scripts with Upsert
**What:** Seed scripts use `ON CONFLICT DO UPDATE` to be safe to run multiple times.
**When to use:** All bootstrap seed scripts (categories, config, thinkers).
**Example:**
```python
# In scripts/seed_config.py
from sqlalchemy.dialects.postgresql import insert

async def seed_config(session: AsyncSession):
    defaults = [
        {"key": "workers_active", "value": False, "set_by": "seed"},
        {"key": "max_candidates_per_day", "value": 20, "set_by": "seed"},
        # ... all defaults from spec Section 3.12
    ]
    for entry in defaults:
        stmt = insert(SystemConfig).values(entry).on_conflict_do_update(
            index_elements=["key"],
            set_={"value": entry["value"], "set_by": "seed"},
        )
        await session.execute(stmt)
    await session.commit()
```

### Anti-Patterns to Avoid
- **Mixing API and Admin apps:** The admin and API are separate FastAPI applications with separate Dockerfiles and ports. Do not combine them into one app.
- **Client-side state management:** HTMX eliminates the need for JavaScript state. Do not add React/Vue/Alpine for the dashboard. Server renders everything.
- **Fat endpoints:** Keep route handlers thin -- query logic goes in dedicated query functions, not inline in route decorators.
- **Missing `from_attributes = True`:** Pydantic v2 requires this config to convert SQLAlchemy models to response schemas. Without it, `ThinkerResponse.model_validate(orm_obj)` fails.
- **Blocking sync queries:** All database queries MUST use async SQLAlchemy. Never use synchronous `session.query()`.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| OpenAPI docs | Custom API documentation | FastAPI built-in `/docs` and `/redoc` | Automatic from Pydantic schemas and route decorators |
| Form parsing | Manual request body parsing | `python-multipart` + FastAPI `Form(...)` | Handles multipart correctly, integrates with validation |
| HTML templates | String concatenation or f-strings | `Jinja2Templates` | Escaping, inheritance, partials, FastAPI integration |
| CORS | Custom headers | `CORSMiddleware` (already configured) | Handles preflight, credentials, origins |
| UUID serialization | Custom JSON encoders | Pydantic v2 native UUID support | Pydantic serializes/deserializes UUIDs automatically |
| Pagination math | Manual page calculation | Simple `total // size + (1 if total % size else 0)` in response schema | 3 lines, no library needed |

**Key insight:** FastAPI already provides most of the infrastructure needed for both the REST API (OpenAPI, validation, dependency injection) and the admin dashboard (Jinja2Templates, form handling). The work is primarily writing Pydantic schemas, query functions, and HTML templates.

## Common Pitfalls

### Pitfall 1: Admin App Database Session Sharing
**What goes wrong:** The admin app needs its own database session setup since it's a separate FastAPI application from the API service.
**Why it happens:** The existing `database.py` creates engine/session from `Settings()` which defaults `service_name` to "thinktank-api". The admin app needs the same DB connection but different service name for logging.
**How to avoid:** The admin app's `main.py` should import the same `engine` and `async_session_factory` from `thinktank.database` -- the database connection is shared, only the FastAPI app instance is separate. Add a `get_session` dependency in `admin/dependencies.py` that mirrors `api/dependencies.py`.
**Warning signs:** Import errors, "no such table" errors, connection pool exhaustion.

### Pitfall 2: HTMX Partial vs Full Page Responses
**What goes wrong:** An HTMX partial endpoint returns a full HTML page instead of a fragment, causing nested `<html>` tags.
**Why it happens:** Using the wrong template (full page vs partial) or forgetting to distinguish between HTMX requests and direct browser requests.
**How to avoid:** Full page endpoints render `dashboard.html` (which `{% extends "base.html" %}`). Partial endpoints render `partials/queue_depth.html` (standalone HTML fragment, no base extension). Check `request.headers.get("HX-Request")` if an endpoint needs to serve both.
**Warning signs:** Doubled headers/navbars, broken layouts after HTMX swap.

### Pitfall 3: SystemConfig Value JSONB Type
**What goes wrong:** `system_config.value` is JSONB, so `workers_active = true` is stored as JSON `true`, not Python `True` or string `"true"`. Reading it returns the raw JSON value.
**Why it happens:** The existing `get_config_value()` returns the raw JSONB value. For simple values like booleans and integers, the JSONB wrapping means `value` might be `True` (Python bool from JSON) or `{"enabled": True}` (nested object).
**How to avoid:** Look at existing usage in `kill_switch.py` and `config_reader.py`. The pattern is: store simple values directly (not wrapped in objects) and `get_config_value` returns them as Python primitives. Seed scripts should store `True`/`False`/`20` directly, not `{"value": 20}`.
**Warning signs:** Config reads returning unexpected types, boolean comparisons failing.

### Pitfall 4: Contract Test Import Paths
**What goes wrong:** Tests fail with `ModuleNotFoundError` or SQLAlchemy dual-import-path errors.
**Why it happens:** The project uses `src.thinktank.*` import paths in source modules but `thinktank.*` for some API imports. Models use `src.thinktank.models.base` consistently.
**How to avoid:** Follow the existing pattern exactly. Source code uses `src.thinktank.*`. Tests import from `src.thinktank.*` for modules and `thinktank.*` for the FastAPI app (via `thinktank.api.main`). Check the existing `conftest.py` `client` fixture for the pattern.
**Warning signs:** `Can't determine which implementation of 'Base' to use`.

### Pitfall 5: Timezone-Naive Datetimes
**What goes wrong:** `asyncpg` raises `can't subtract offset-naive and offset-aware datetimes` or similar errors.
**Why it happens:** The project convention (documented in STATE.md decisions) uses `TIMESTAMP WITHOUT TIME ZONE` columns and timezone-naive Python datetimes.
**How to avoid:** Use `_now()` pattern: `datetime.now(UTC).replace(tzinfo=None)`. Never use `datetime.utcnow()` (deprecated) or `datetime.now(UTC)` (timezone-aware). All factories follow this pattern.
**Warning signs:** asyncpg type mismatch errors on datetime columns.

### Pitfall 6: Bootstrap Script Ordering
**What goes wrong:** Seed thinkers script fails because categories don't exist yet, or LLM review fails because config isn't seeded.
**Why it happens:** The bootstrap sequence has strict ordering dependencies (spec Section 4.1).
**How to avoid:** Scripts must be run in order: migrations -> categories -> config -> thinkers -> LLM review -> activate workers. Each script should validate its prerequisites before running.
**Warning signs:** Foreign key constraint violations, missing config values.

## Code Examples

Verified patterns from the existing codebase and official FastAPI documentation:

### REST API Router with Pagination
```python
# In api/routers/thinkers.py
from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from thinktank.api.dependencies import get_session
from thinktank.api.schemas import PaginatedResponse, ThinkerResponse
from thinktank.models.thinker import Thinker

router = APIRouter(prefix="/api/thinkers", tags=["thinkers"])

@router.get("", response_model=PaginatedResponse[ThinkerResponse])
async def list_thinkers(
    session: AsyncSession = Depends(get_session),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    tier: int | None = Query(None, ge=1, le=3),
    status: str | None = Query(None),
):
    query = select(Thinker)
    count_query = select(func.count()).select_from(Thinker)

    if tier is not None:
        query = query.where(Thinker.tier == tier)
        count_query = count_query.where(Thinker.tier == tier)
    if status is not None:
        query = query.where(Thinker.approval_status == status)
        count_query = count_query.where(Thinker.approval_status == status)

    total = (await session.execute(count_query)).scalar_one()
    offset = (page - 1) * size
    result = await session.execute(query.offset(offset).limit(size))
    items = result.scalars().all()

    return PaginatedResponse(
        items=[ThinkerResponse.model_validate(t) for t in items],
        total=total,
        page=page,
        size=size,
        pages=(total + size - 1) // size,
    )
```

### Admin Dashboard HTMX Template
```html
<!-- templates/base.html -->
<!DOCTYPE html>
<html>
<head>
    <title>ThinkTank Admin</title>
    <script src="https://unpkg.com/htmx.org@2.0.4"></script>
    <style>
        /* Minimal CSS for dashboard layout - no framework */
        .dashboard-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; }
        .card { border: 1px solid #ddd; padding: 1rem; border-radius: 4px; }
        .error { color: #c00; }
        .warning { color: #c90; }
        .healthy { color: #0a0; }
        .gauge { height: 20px; background: #eee; border-radius: 10px; overflow: hidden; }
        .gauge-fill { height: 100%; transition: width 0.3s; }
        .gauge-green { background: #0a0; }
        .gauge-yellow { background: #cc0; }
        .gauge-red { background: #c00; }
        .timeout-highlight { background: #fff3cd; border-left: 4px solid #ffc107; }
    </style>
</head>
<body>
    <nav>
        <a href="/admin/">Dashboard</a>
        <a href="/admin/llm">LLM Panel</a>
        <a href="/admin/categories">Categories</a>
    </nav>
    {% block content %}{% endblock %}
</body>
</html>
```

### API Cost Rollup Handler
```python
# In handlers/rollup_api_usage.py
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from src.thinktank.models.job import Job

# Per-API cost estimates (USD per call)
API_COST_MAP = {
    "listennotes": {"search": 0.005, "default": 0.001},
    "youtube": {"search": 0.01, "default": 0.001},
    "podcastindex": {"search": 0.0, "default": 0.0},  # Free API
    "anthropic": {"messages.create": 0.015, "default": 0.015},
    "twitter": {"default": 0.001},
}

async def handle_rollup_api_usage(session: AsyncSession, job: Job) -> None:
    """Aggregate rate_limit_usage into api_usage hourly rollups.

    Groups calls by api_name and truncated hour, inserts into api_usage
    with estimated costs, then purges raw rows older than 2 hours.
    """
    # Rollup: aggregate by api_name + hour
    rollup_stmt = text("""
        INSERT INTO api_usage (id, api_name, endpoint, period_start, call_count, estimated_cost_usd)
        SELECT gen_random_uuid(), api_name, 'aggregate', date_trunc('hour', called_at),
               COUNT(*), NULL
        FROM rate_limit_usage
        WHERE called_at < date_trunc('hour', LOCALTIMESTAMP)
        GROUP BY api_name, date_trunc('hour', called_at)
        ON CONFLICT DO NOTHING
    """)
    await session.execute(rollup_stmt)

    # Purge old raw rows (older than 2 hours)
    purge_stmt = text("""
        DELETE FROM rate_limit_usage
        WHERE called_at < LOCALTIMESTAMP - INTERVAL '2 hours'
    """)
    await session.execute(purge_stmt)
    await session.commit()
```

### Contract Test for API Endpoint
```python
# In tests/contract/test_api_endpoints.py
import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.anyio


class TestThinkerEndpointContract:
    """Verify thinker endpoints return correct shapes."""

    async def test_list_thinkers_returns_paginated_response(self, client: AsyncClient):
        response = await client.get("/api/thinkers")
        assert response.status_code == 200
        body = response.json()
        assert "items" in body
        assert "total" in body
        assert "page" in body
        assert "size" in body
        assert "pages" in body
        assert isinstance(body["items"], list)

    async def test_list_thinkers_filter_by_tier(self, client: AsyncClient):
        response = await client.get("/api/thinkers?tier=1")
        assert response.status_code == 200
        for item in response.json()["items"]:
            assert item["tier"] == 1

    async def test_get_thinker_not_found(self, client: AsyncClient):
        response = await client.get("/api/thinkers/00000000-0000-0000-0000-000000000000")
        assert response.status_code == 404
        body = response.json()
        assert "detail" in body

    async def test_create_thinker_validation_error(self, client: AsyncClient):
        response = await client.post("/api/thinkers", json={})
        assert response.status_code == 422
        body = response.json()
        assert "detail" in body
```

### Idempotent Category Seed Script
```python
# In scripts/seed_categories.py
"""Seed the category taxonomy from spec Section 4.2.

Idempotent: uses ON CONFLICT DO UPDATE so running twice is safe.
"""
import asyncio
import uuid

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.thinktank.database import async_session_factory
from src.thinktank.models.category import Category


TAXONOMY = {
    "knowledge": {
        "name": "Knowledge",
        "children": {
            "artificial_intelligence": {
                "name": "Artificial Intelligence",
                "children": {
                    "ai_models": {"name": "AI Models"},
                    "ai_safety": {"name": "AI Safety"},
                    "ai_infrastructure": {"name": "AI Infrastructure"},
                    "ai_applications": {"name": "AI Applications"},
                },
            },
            # ... full taxonomy from spec
        },
    },
}


async def seed_categories(session: AsyncSession) -> int:
    """Insert category taxonomy. Returns count of categories seeded."""
    count = 0

    async def _seed(slug: str, data: dict, parent_id: uuid.UUID | None = None):
        nonlocal count
        cat_id = uuid.uuid5(uuid.NAMESPACE_DNS, f"thinktank.category.{slug}")
        stmt = insert(Category).values(
            id=cat_id,
            slug=slug,
            name=data["name"],
            description=data.get("description", f"Category: {data['name']}"),
            parent_id=parent_id,
        ).on_conflict_do_update(
            index_elements=["slug"],
            set_={"name": data["name"], "parent_id": parent_id},
        )
        await session.execute(stmt)
        count += 1
        for child_slug, child_data in data.get("children", {}).items():
            await _seed(child_slug, child_data, parent_id=cat_id)

    for slug, data in TAXONOMY.items():
        await _seed(slug, data)

    await session.commit()
    return count


if __name__ == "__main__":
    async def main():
        async with async_session_factory() as session:
            count = await seed_categories(session)
            print(f"Seeded {count} categories")

    asyncio.run(main())
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Jinja2 `TemplateLookup` | `Jinja2Templates(directory=...)` | FastAPI 0.100+ | Use FastAPI's built-in template support |
| `response_model=List[...]` | `response_model=list[...]` | Python 3.12 | Use lowercase generics throughout |
| `@validator` | `@field_validator` | Pydantic v2 | All validators must use v2 syntax |
| `orm_mode = True` | `from_attributes = True` | Pydantic v2 | Required for SQLAlchemy model conversion |
| `Session.query()` | `select()` + `session.execute()` | SQLAlchemy 2.0 | Project already uses 2.0-style exclusively |

**Deprecated/outdated:**
- Pydantic v1 `Config` class: Use `model_config` dict instead
- `datetime.utcnow()`: Deprecated in Python 3.12; use `datetime.now(UTC).replace(tzinfo=None)` per project convention

## Open Questions

1. **Admin authentication**
   - What we know: The spec says the admin dashboard runs on "private networking only" (Section 10.1). No authentication mechanism is specified.
   - What's unclear: Whether any basic auth or API key is needed for the admin endpoints.
   - Recommendation: Skip authentication for v1. The admin runs on Railway private networking, not exposed to the public internet. Add a comment/TODO noting this is secured by network isolation.

2. **API cost estimates accuracy**
   - What we know: The spec mentions "estimated USD costs" in `api_usage`. Cost per API call varies by endpoint.
   - What's unclear: Exact per-call costs for Listen Notes, YouTube Data API, Twitter API, and Anthropic.
   - Recommendation: Use configurable cost-per-call estimates stored in a module constant or `system_config`. Start with reasonable defaults (Anthropic ~$0.015/call average, Listen Notes ~$0.005/call, YouTube ~$0.001/unit, others free/negligible). Allow admin to adjust.

3. **rollup_api_usage as handler vs scheduler**
   - What we know: The spec lists `rollup_api_usage` as a job type (priority 7) in the job table. The `refresh_due_sources` handler already purges old `rate_limit_usage` rows.
   - What's unclear: Whether rollup should be a jobs-table job or a scheduler like reclaim/escalation.
   - Recommendation: Implement as a jobs-table handler (consistent with the spec). The `refresh_due_sources` handler or a new scheduler creates a `rollup_api_usage` job hourly. The handler aggregates and purges.

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 8.0+ with pytest-asyncio 0.25+ |
| Config file | `pyproject.toml` `[tool.pytest.ini_options]` |
| Quick run command | `python -m pytest tests/ -x --timeout=30` |
| Full suite command | `python -m pytest tests/ -v` |

### Phase Requirements -> Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| API-01 | Thinker CRUD with filtering | contract | `python -m pytest tests/contract/test_api_thinkers.py -x` | Wave 0 |
| API-02 | Source list with filtering | contract | `python -m pytest tests/contract/test_api_sources.py -x` | Wave 0 |
| API-03 | Content list with pagination | contract | `python -m pytest tests/contract/test_api_content.py -x` | Wave 0 |
| API-04 | Job queue status endpoint | contract | `python -m pytest tests/contract/test_api_jobs.py -x` | Wave 0 |
| API-05 | System config read/write | contract | `python -m pytest tests/contract/test_api_config.py -x` | Wave 0 |
| API-06 | OpenAPI docs accessible | contract | `python -m pytest tests/contract/test_api_openapi.py -x` | Wave 0 |
| OPS-01 | Dashboard shows live data | integration | `python -m pytest tests/integration/test_admin_dashboard.py -x` | Wave 0 |
| OPS-02 | LLM panel + override | integration | `python -m pytest tests/integration/test_admin_llm_panel.py -x` | Wave 0 |
| OPS-03 | Cost rollup handler | contract | `python -m pytest tests/contract/test_rollup_handler.py -x` | Wave 0 |
| OPS-04 | Rate limit gauges | integration | Covered by dashboard tests | -- |
| OPS-05 | Category management | integration | Covered by dashboard tests | -- |
| OPS-06 | Bootstrap sequence | integration | `python -m pytest tests/integration/test_bootstrap.py -x` | Wave 0 |
| QUAL-03 | Contract tests for all endpoints | contract | `python -m pytest tests/contract/test_api_*.py -x` | Wave 0 |
| QUAL-05 | Operations runbook | manual-only | File exists at `docs/operations-runbook.md` | Wave 0 |
| QUAL-07 | Development guide | manual-only | File exists at `docs/development-guide.md` | Wave 0 |

### Sampling Rate
- **Per task commit:** `python -m pytest tests/ -x --timeout=30`
- **Per wave merge:** `python -m pytest tests/ -v`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `tests/contract/test_api_thinkers.py` -- covers API-01
- [ ] `tests/contract/test_api_sources.py` -- covers API-02
- [ ] `tests/contract/test_api_content.py` -- covers API-03
- [ ] `tests/contract/test_api_jobs.py` -- covers API-04
- [ ] `tests/contract/test_api_config.py` -- covers API-05
- [ ] `tests/contract/test_api_openapi.py` -- covers API-06
- [ ] `tests/contract/test_rollup_handler.py` -- covers OPS-03
- [ ] `tests/integration/test_admin_dashboard.py` -- covers OPS-01, OPS-04
- [ ] `tests/integration/test_admin_llm_panel.py` -- covers OPS-02
- [ ] `tests/integration/test_bootstrap.py` -- covers OPS-06

## Sources

### Primary (HIGH confidence)
- Existing codebase analysis: `src/thinktank/api/`, `src/thinktank/models/`, `tests/`, `docker/` -- all patterns, conventions, model definitions
- ThinkTank_Specification.md Sections 9, 10 -- Admin dashboard requirements, deployment, bootstrap sequence
- STANDARDS.md -- Testing pyramid, documentation requirements, deployment conventions

### Secondary (MEDIUM confidence)
- [FastAPI Templates Documentation](https://fastapi.tiangolo.com/advanced/templates/) -- Jinja2Templates integration
- [FastAPI Best Practices (zhanymkanov)](https://github.com/zhanymkanov/fastapi-best-practices) -- REST API patterns, Pydantic response models
- [TestDriven.io FastAPI HTMX Tutorial](https://testdriven.io/blog/fastapi-htmx/) -- HTMX integration patterns

### Tertiary (LOW confidence)
- Cost estimates for external APIs -- based on public pricing pages, subject to change

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- FastAPI + Jinja2 + HTMX is explicitly specified; all libraries already in use or standard
- Architecture: HIGH -- Follows existing codebase patterns exactly; admin app scaffold already defined in Dockerfile
- Pitfalls: HIGH -- All pitfalls derived from direct codebase analysis of existing conventions and known issues
- Bootstrap/seed scripts: MEDIUM -- Pattern is clear from spec, but exact seed data (thinker list, taxonomy) needs definition during implementation

**Research date:** 2026-03-08
**Valid until:** 2026-04-08 (stable -- all technologies are well-established)
