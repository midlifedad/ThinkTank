# Phase 1: Foundation Layer - Research

**Researched:** 2026-03-08
**Domain:** FastAPI + SQLAlchemy 2.0 async + PostgreSQL project scaffolding
**Confidence:** HIGH

## Summary

Phase 1 is a greenfield Python project scaffold. The ThinkTank repo is currently empty -- no pyproject.toml, no source code, no Dockerfiles. Everything must be created from scratch: project structure, package configuration with uv, 14 SQLAlchemy 2.0 async models mapping to the full database schema (categories, thinkers, sources, content, jobs, llm_reviews, system_config, rate_limit_usage, api_usage, content_thinkers, candidate_thinkers, thinker_profiles, thinker_metrics, thinker_categories), an Alembic migration system with advisory lock protection, a configuration system with env > DB > code default precedence, structured JSON logging via structlog with correlation IDs, a FastAPI application with async lifespan and health endpoint, Docker Compose for local dev with PostgreSQL 16, the full toolchain (uv, ruff, mypy, pytest with pytest-asyncio), factory functions for all models, and architecture documentation.

This is the most well-documented combination in the Python web ecosystem. FastAPI + SQLAlchemy 2.0 + Alembic + structlog is the standard async Python API stack with extensive official documentation, production reference implementations, and community patterns. The primary research risk is low -- the main challenge is getting the details right: correct pool configuration, proper async session lifecycle, Alembic async env.py configuration, and the factory-boy async integration (which requires the `async-factory-boy` wrapper or manual async helpers since factory-boy does not natively support AsyncSession).

**Primary recommendation:** Follow the standard FastAPI + SQLAlchemy 2.0 async pattern with a flat `src/` layout, Alembic migrations auto-generated from models, structlog with contextvars for correlation IDs, pydantic-settings for configuration, and Docker Compose providing PostgreSQL 16 for both local dev and testing (no testcontainers needed -- Docker Compose is simpler and matches STANDARDS.md requirement that "local dev mirrors production topology").

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| FNDTN-01 | PostgreSQL schema with all 14 tables deployed via Alembic migration | SQLAlchemy 2.0 declarative models with `Mapped`/`mapped_column`, UUID PKs via `mapped_column(default=uuid.uuid4)`, Alembic `--autogenerate` from models |
| FNDTN-02 | SQLAlchemy 2.0 async models for all tables with relationship mappings | `AsyncAttrs + DeclarativeBase`, `async_sessionmaker`, `expire_on_commit=False`, `relationship()` with lazy="selectin" for async |
| FNDTN-03 | Alembic migration system with advisory lock | Async env.py with `async_engine_from_config`, `pg_advisory_lock(1)` wrapper around migration execution, NullPool for migrations |
| FNDTN-04 | Environment-based config with DB override precedence | pydantic-settings `BaseSettings` for env vars, custom settings source for DB override via system_config table, code defaults as field defaults |
| FNDTN-05 | Structured JSON logging with correlation IDs | structlog with `merge_contextvars` as first processor, `JSONRenderer`, `add_log_level`, `TimeStamper`, middleware binds correlation ID per request |
| FNDTN-06 | Health endpoint per service | FastAPI `GET /health` checking DB connectivity via `SELECT 1`, returning 200 with JSON body |
| FNDTN-07 | FastAPI app scaffold with async lifespan, connection pool, CORS | `@asynccontextmanager` lifespan, `create_async_engine` with pool config, `CORSMiddleware` |
| FNDTN-08 | Project toolchain setup (uv, ruff, mypy, pytest, pre-commit) | uv for package management, `pyproject.toml` with `[dependency-groups]`, ruff pre-commit hook, mypy strict on public interfaces |
| FNDTN-09 | Docker configuration for all 4 Railway services | Dockerfiles for API, CPU Worker, GPU Worker, Admin; Docker Compose for local dev with PostgreSQL 16 |
| QUAL-01 | Test suite following STANDARDS.md pyramid | pytest + pytest-asyncio with `loop_scope="session"`, Docker Compose PostgreSQL for integration tests, transaction-per-test rollback |
| QUAL-02 | Factory functions for all domain objects | Factory functions (not factory-boy classes for simplicity with async) -- plain async helper functions producing valid model instances with overridable fields |
| QUAL-06 | Architecture documentation with data flow diagrams | Markdown docs in `docs/` with service architecture, data flow, and schema relationship diagrams |
</phase_requirements>

## Standard Stack

### Core

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| Python | 3.12 | Runtime | Stable asyncio, NeMo compatibility. Avoid 3.13+ per project research. |
| FastAPI | >=0.135.1 | API framework | Async-native, auto OpenAPI, Pydantic integration. Dominant Python API framework. |
| uvicorn[standard] | >=0.41.0 | ASGI server | Standard FastAPI deployment. `[standard]` extras include uvloop + httptools. |
| Pydantic | >=2.12.5 | Data validation | FastAPI's native validation. V2 is 5-50x faster than V1. |
| pydantic-settings | >=2.13.1 | Configuration | Env var loading with type validation, `.env` support, custom sources. |
| SQLAlchemy[asyncio] | >=2.0.46 | ORM | Async support via AsyncSession, 2.0-style declarative mapping with type hints. |
| asyncpg | latest | PostgreSQL driver | Fastest async PostgreSQL driver. 3-4x lower latency than psycopg in async. |
| Alembic | >=1.17.2 | Schema migrations | Only migration tool for SQLAlchemy. Forward-only, auto-generate from models. |
| structlog | >=25.5.0 | Structured logging | JSON logging, context binding for correlation IDs, async-compatible. |

### Supporting

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| httpx | >=0.28.1 | HTTP client | Phase 1: health check testing. Later phases: all external API calls. |

### Dev/Test

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| pytest | >=8.0 | Test framework | All testing. |
| pytest-asyncio | >=1.3.0 | Async test support | All async tests. Use `loop_scope="session"` for shared event loop. |
| ruff | >=0.15.3 | Linter + formatter | Replaces Black, Flake8, isort. Single tool. |
| mypy | >=1.19.1 | Type checker | Static type checking on public interfaces. |
| pre-commit | latest | Git hooks | Runs ruff + mypy before commit. |
| uv | latest | Package manager | 10-100x faster than pip. Deterministic lockfile. |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Plain async factory functions | factory-boy + async-factory-boy | factory-boy does not natively support AsyncSession. async-factory-boy exists but adds a dependency for something achievable with simple functions. For 14 models, plain factory functions are clearer and avoid the async adaptation overhead. |
| Docker Compose PostgreSQL for tests | testcontainers[postgres] | testcontainers spins up a fresh container per session which is more isolated, but Docker Compose is simpler, matches production topology per STANDARDS.md, and avoids testcontainers' known issues with asyncpg driver selection. |
| psycopg (async) | asyncpg | psycopg 3 async is newer with less production history. asyncpg is the SQLAlchemy-recommended async PostgreSQL driver. |

**Installation:**
```bash
# Initialize project
uv init --python 3.12
uv add \
  "fastapi>=0.135.1" \
  "uvicorn[standard]>=0.41.0" \
  "pydantic>=2.12.5" \
  "pydantic-settings>=2.13.1" \
  "sqlalchemy[asyncio]>=2.0.46" \
  "asyncpg" \
  "alembic>=1.17.2" \
  "httpx>=0.28.1" \
  "structlog>=25.5.0"

# Dev dependencies
uv add --group dev \
  "pytest>=8.0" \
  "pytest-asyncio>=1.3.0" \
  "ruff>=0.15.3" \
  "mypy>=1.19.1" \
  "pre-commit"

# Lint group
uv add --group lint \
  "ruff>=0.15.3" \
  "mypy>=1.19.1"

# Test group
uv add --group test \
  "pytest>=8.0" \
  "pytest-asyncio>=1.3.0"
```

## Architecture Patterns

### Recommended Project Structure

```
ThinkTank/
├── src/
│   └── thinktank/
│       ├── __init__.py
│       ├── config.py              # pydantic-settings configuration
│       ├── database.py            # Engine, session factory, base model
│       ├── logging.py             # structlog configuration
│       ├── models/
│       │   ├── __init__.py        # Re-exports all models
│       │   ├── base.py            # DeclarativeBase, common mixins
│       │   ├── category.py        # categories, thinker_categories
│       │   ├── thinker.py         # thinkers, thinker_profiles, thinker_metrics
│       │   ├── source.py          # sources
│       │   ├── content.py         # content, content_thinkers
│       │   ├── candidate.py       # candidate_thinkers
│       │   ├── job.py             # jobs
│       │   ├── review.py          # llm_reviews
│       │   ├── config_table.py    # system_config
│       │   ├── rate_limit.py      # rate_limit_usage
│       │   └── api_usage.py       # api_usage
│       ├── api/
│       │   ├── __init__.py
│       │   ├── main.py            # FastAPI app with lifespan
│       │   ├── health.py          # Health endpoint router
│       │   └── middleware.py      # Correlation ID, logging middleware
│       └── worker/
│           └── __init__.py        # Placeholder for Phase 2
├── alembic/
│   ├── env.py                     # Async migration runner with advisory lock
│   ├── script.py.mako             # Migration template
│   └── versions/                  # Migration files
├── tests/
│   ├── conftest.py                # Shared fixtures (engine, session, factories)
│   ├── unit/
│   │   ├── __init__.py
│   │   ├── test_config.py         # Configuration precedence tests
│   │   └── test_logging.py        # Log format tests
│   ├── integration/
│   │   ├── __init__.py
│   │   ├── test_models.py         # Model CRUD + relationship tests
│   │   ├── test_migrations.py     # Alembic up/down tests
│   │   └── test_health.py         # Health endpoint tests
│   └── factories.py               # Factory functions for all models
├── docs/
│   ├── architecture.md            # System architecture + data flow
│   └── adr/                       # Architecture Decision Records
├── docker/
│   ├── Dockerfile.api             # API service
│   ├── Dockerfile.worker-cpu      # CPU worker
│   ├── Dockerfile.worker-gpu      # GPU worker (nvcr.io/nvidia/nemo:24.05)
│   └── Dockerfile.admin           # Admin dashboard
├── docker-compose.yml             # Local dev: PostgreSQL 16 + app services
├── docker-compose.test.yml        # Test: PostgreSQL 16 only
├── pyproject.toml                 # uv project config, ruff, mypy, pytest
├── alembic.ini                    # Alembic configuration
├── .pre-commit-config.yaml        # Pre-commit hooks
├── .env.example                   # Environment variable template
├── .gitignore
└── README.md
```

### Pattern 1: SQLAlchemy 2.0 Declarative Base with UUID PKs

**What:** All models inherit from a common base with UUID primary keys and timestamp mixins.
**When to use:** Every model in the system.
**Example:**
```python
# Source: SQLAlchemy 2.0 docs - Declarative mapping
import uuid
from datetime import datetime
from typing import Annotated

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Reusable type annotations
uuid_pk = Annotated[
    uuid.UUID,
    mapped_column(primary_key=True, default=uuid.uuid4),
]
created_at_col = Annotated[
    datetime,
    mapped_column(server_default=text("NOW()")),
]

class Base(AsyncAttrs, DeclarativeBase):
    pass

class TimestampMixin:
    created_at: Mapped[created_at_col]
```

### Pattern 2: Async Engine and Session Factory

**What:** Centralized database connection management with proper pool configuration.
**When to use:** Application startup.
**Example:**
```python
# Source: SQLAlchemy 2.0 asyncio docs
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

engine = create_async_engine(
    settings.database_url,
    pool_size=10,
    max_overflow=5,
    pool_pre_ping=True,
    echo=settings.debug,
)

async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,  # CRITICAL for async
)
```

### Pattern 3: FastAPI Lifespan with Database

**What:** Async context manager handling startup/shutdown of DB connections.
**When to use:** FastAPI application entry point.
**Example:**
```python
# Source: FastAPI docs - Lifespan Events
from contextlib import asynccontextmanager
from fastapi import FastAPI

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: verify DB connection
    async with engine.begin() as conn:
        await conn.execute(text("SELECT 1"))
    yield
    # Shutdown: dispose engine
    await engine.dispose()

app = FastAPI(title="ThinkTank", lifespan=lifespan)
```

### Pattern 4: Configuration with DB Override

**What:** Three-tier configuration: env vars > system_config table > code defaults.
**When to use:** All configurable parameters.
**Example:**
```python
# Source: pydantic-settings docs
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Database
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/thinktank"

    # Application
    debug: bool = False
    service_name: str = "thinktank-api"

    # Pool
    db_pool_size: int = 10
    db_max_overflow: int = 5
```

For the DB override layer (system_config table), implement a custom function that queries the table at startup and merges values, rather than a custom pydantic settings source. This is simpler and avoids async-in-sync issues with pydantic-settings source loading.

### Pattern 5: Structlog with Correlation IDs

**What:** JSON structured logging with per-request correlation IDs via contextvars.
**When to use:** All logging throughout the application.
**Example:**
```python
# Source: structlog 25.5.0 docs - contextvars
import structlog
import uuid

def configure_logging(service_name: str) -> None:
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,  # MUST be first
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.UnicodeDecoder(),
            structlog.processors.JSONRenderer(),
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

# In FastAPI middleware:
async def logging_middleware(request, call_next):
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(
        correlation_id=str(uuid.uuid4()),
        service=settings.service_name,
    )
    response = await call_next(request)
    return response
```

### Pattern 6: Alembic Async with Advisory Lock

**What:** Migrations run through async engine with PostgreSQL advisory lock preventing concurrent execution.
**When to use:** Every migration run (startup, CI, manual).
**Example:**
```python
# Source: Alembic cookbook + PostgreSQL advisory lock pattern
# alembic/env.py
import asyncio
from sqlalchemy import text, pool
from sqlalchemy.ext.asyncio import async_engine_from_config

MIGRATION_LOCK_ID = 1  # Arbitrary but consistent advisory lock ID

def do_run_migrations(connection):
    # Acquire advisory lock before running migrations
    connection.execute(text("SELECT pg_advisory_lock(:lock_id)"), {"lock_id": MIGRATION_LOCK_ID})
    try:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
    finally:
        connection.execute(text("SELECT pg_advisory_unlock(:lock_id)"), {"lock_id": MIGRATION_LOCK_ID})

async def run_async_migrations():
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()

def run_migrations_online():
    asyncio.run(run_async_migrations())
```

### Pattern 7: Factory Functions for Test Data

**What:** Simple async functions producing valid model instances with overridable fields.
**When to use:** All test data generation.
**Example:**
```python
# tests/factories.py
import uuid
from datetime import datetime, timezone
from src.thinktank.models.thinker import Thinker

def make_thinker(**overrides) -> Thinker:
    """Create a Thinker with sensible defaults. Override any field."""
    defaults = {
        "id": uuid.uuid4(),
        "name": "Test Thinker",
        "slug": f"test-thinker-{uuid.uuid4().hex[:8]}",
        "tier": 2,
        "bio": "A test thinker for testing.",
        "approval_status": "approved",
        "active": True,
        "added_at": datetime.now(timezone.utc),
    }
    defaults.update(overrides)
    return Thinker(**defaults)

async def create_thinker(session, **overrides) -> Thinker:
    """Create and persist a Thinker. Returns the persisted instance."""
    thinker = make_thinker(**overrides)
    session.add(thinker)
    await session.flush()
    return thinker
```

### Anti-Patterns to Avoid

- **Using `expire_on_commit=True` with async sessions:** After commit, accessing any attribute triggers a lazy load which fails in async context. Always set `expire_on_commit=False`.
- **Sharing AsyncSession across concurrent tasks:** One AsyncSession per task. Never share between `asyncio.gather()` coroutines.
- **Using sync engine for Alembic when app uses async:** Use `async_engine_from_config` in env.py with `run_sync()` wrapper. Mixing drivers causes connection issues.
- **Holding DB connections during long operations:** Commit/release the session after claiming data, then process. Never hold a session open during network calls or heavy computation.
- **Using `pool_pre_ping` without understanding the cost:** It adds one round-trip per checkout. Acceptable for this workload volume but worth noting.
- **Running Alembic autogenerate without reviewing:** Always review generated migrations for correctness, especially with array types (TEXT[]), JSONB, and partial indexes.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Configuration loading | Custom env parser | pydantic-settings `BaseSettings` | Handles .env files, type coercion, validation, nested config. |
| Structured logging | Custom JSON formatter | structlog | Context binding, processor pipeline, proven since 2013. |
| Database migrations | Raw DDL scripts | Alembic | Auto-generate from models, version tracking, rollback support. |
| Code formatting | Custom style guides | ruff | 100x faster than Black+Flake8+isort combined. One config. |
| UUID generation | Custom ID schemes | `uuid.uuid4()` with SQLAlchemy `Uuid` type | PostgreSQL native UUID, no custom type needed. |
| Connection pooling | Custom pool manager | SQLAlchemy built-in pool | Battle-tested, configurable, pre-ping support. |
| CORS handling | Custom headers | FastAPI `CORSMiddleware` | Standard, handles preflight, configurable origins. |
| Health checks | Custom endpoint logic | FastAPI route + `SELECT 1` | Simple, standard pattern. No framework needed. |

**Key insight:** Phase 1 is entirely standard infrastructure. Every component has a well-established library with extensive documentation. The value is in correct assembly, not creative solutions.

## Common Pitfalls

### Pitfall 1: Alembic Migration Autogenerate Missing Array/JSONB Columns
**What goes wrong:** Alembic autogenerate does not always correctly detect PostgreSQL-specific types like `ARRAY(TEXT)` or `JSONB`. Migrations may generate incorrect SQL or miss column type changes.
**Why it happens:** SQLAlchemy's generic type system differs from PostgreSQL-specific types.
**How to avoid:** Import and use `sqlalchemy.dialects.postgresql` types explicitly in models (`JSONB`, `ARRAY`). Review every generated migration before applying. Use `compare_type=True` in Alembic's `context.configure()`.
**Warning signs:** Migration runs but table columns have wrong types. JSONB columns appear as TEXT.

### Pitfall 2: AsyncSession Expire-on-Commit
**What goes wrong:** After `session.commit()`, accessing any model attribute triggers a synchronous lazy load, raising `MissingGreenlet` error.
**Why it happens:** SQLAlchemy defaults to `expire_on_commit=True`. In sync mode this triggers a lazy load. In async mode, lazy loads are forbidden.
**How to avoid:** Always set `expire_on_commit=False` on `async_sessionmaker`. Use `selectinload()` or `joinedload()` for relationships needed after query.
**Warning signs:** `MissingGreenlet: greenlet_spawn has not been called` errors in any code that accesses model attributes after commit.

### Pitfall 3: Connection Pool Exhaustion Under Test
**What goes wrong:** Integration tests exhaust the connection pool, causing tests to hang or timeout.
**Why it happens:** Each test creates sessions without proper cleanup. Transaction rollback tests may hold connections.
**How to avoid:** Use a single engine per test session. Use `NullPool` or small `pool_size` for tests. Ensure every test fixture properly disposes sessions. Use nested transactions (savepoints) for test isolation.
**Warning signs:** Tests pass individually but hang when run together. "QueuePool limit reached" errors.

### Pitfall 4: Alembic env.py Import Order
**What goes wrong:** Alembic cannot find models to autogenerate from because models are not imported before `target_metadata` is set.
**Why it happens:** Python's import system requires explicit imports. Just setting `target_metadata = Base.metadata` is not enough if model modules haven't been imported.
**How to avoid:** Import all model modules in `models/__init__.py` and import that package in `alembic/env.py` before using `Base.metadata`.
**Warning signs:** `alembic revision --autogenerate` produces empty migrations despite model changes.

### Pitfall 5: UUID Type Mismatch Between Python and PostgreSQL
**What goes wrong:** UUID values are stored as strings instead of native PostgreSQL UUID type, breaking indexes and comparisons.
**Why it happens:** Using `String` type instead of SQLAlchemy's `Uuid` type, or not using the PostgreSQL dialect.
**How to avoid:** Use `Mapped[uuid.UUID]` with `mapped_column()` -- SQLAlchemy 2.0 automatically maps this to PostgreSQL's native UUID type via asyncpg.
**Warning signs:** UUID columns appear as VARCHAR in the database. Comparisons require casting.

### Pitfall 6: Docker Compose PostgreSQL Not Ready
**What goes wrong:** Application starts before PostgreSQL is ready to accept connections.
**Why it happens:** Docker `depends_on` only waits for container start, not service readiness.
**How to avoid:** Use `depends_on` with `condition: service_healthy` and a `healthcheck` on the PostgreSQL service using `pg_isready`.
**Warning signs:** Connection refused errors on first startup. Works on retry.

### Pitfall 7: structlog Context Isolation in Async
**What goes wrong:** Correlation IDs from one request leak into another request's logs.
**Why it happens:** Context variables in asyncio are isolated per-task by default, but middleware must explicitly clear them.
**How to avoid:** Call `structlog.contextvars.clear_contextvars()` at the start of every request in middleware, before binding the new correlation ID.
**Warning signs:** Log entries show correlation IDs from different requests. Particularly under concurrent load.

## Code Examples

Verified patterns from official sources:

### Docker Compose for Local Development
```yaml
# docker-compose.yml
services:
  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: thinktank
      POSTGRES_PASSWORD: thinktank
      POSTGRES_DB: thinktank
    ports:
      - "5432:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U thinktank"]
      interval: 5s
      timeout: 5s
      retries: 5

volumes:
  pgdata:
```

### Docker Compose for Tests
```yaml
# docker-compose.test.yml
services:
  postgres-test:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: thinktank_test
      POSTGRES_PASSWORD: thinktank_test
      POSTGRES_DB: thinktank_test
    ports:
      - "5433:5432"
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U thinktank_test"]
      interval: 2s
      timeout: 2s
      retries: 5
    tmpfs:
      - /var/lib/postgresql/data  # RAM-backed for speed
```

### pyproject.toml Configuration
```toml
[project]
name = "thinktank"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.135.1",
    "uvicorn[standard]>=0.41.0",
    "pydantic>=2.12.5",
    "pydantic-settings>=2.13.1",
    "sqlalchemy[asyncio]>=2.0.46",
    "asyncpg",
    "alembic>=1.17.2",
    "httpx>=0.28.1",
    "structlog>=25.5.0",
]

[dependency-groups]
dev = [
    {include-group = "test"},
    {include-group = "lint"},
    "pre-commit",
]
test = [
    "pytest>=8.0",
    "pytest-asyncio>=1.3.0",
]
lint = [
    "ruff>=0.15.3",
    "mypy>=1.19.1",
]

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
asyncio_default_test_loop_scope = "session"

[tool.ruff]
target-version = "py312"
line-length = 120
src = ["src"]

[tool.ruff.lint]
select = ["E", "F", "I", "N", "W", "UP", "B", "A", "SIM", "TCH"]

[tool.ruff.format]
quote-style = "double"

[tool.mypy]
python_version = "3.12"
warn_return_any = true
warn_unused_configs = true
disallow_untyped_defs = true
plugins = ["pydantic.mypy"]

[[tool.mypy.overrides]]
module = "tests.*"
disallow_untyped_defs = false
```

### Test Conftest with Real PostgreSQL
```python
# tests/conftest.py
import asyncio
import pytest
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy import event, text
from src.thinktank.models.base import Base

TEST_DATABASE_URL = "postgresql+asyncpg://thinktank_test:thinktank_test@localhost:5433/thinktank_test"

@pytest.fixture(scope="session")
def event_loop_policy():
    return asyncio.DefaultEventLoopPolicy()

@pytest.fixture(scope="session")
async def engine():
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()

@pytest.fixture
async def session(engine):
    """Per-test session with transaction rollback for isolation."""
    async with engine.connect() as conn:
        trans = await conn.begin()
        session = AsyncSession(bind=conn, expire_on_commit=False)
        # Nest a savepoint so the test can call commit()
        nested = await conn.begin_nested()

        @event.listens_for(session.sync_session, "after_transaction_end")
        def restart_savepoint(session, transaction):
            nonlocal nested
            if transaction.nested and not transaction._parent.nested:
                nested = conn.sync_connection.begin_nested()

        yield session
        await session.close()
        await trans.rollback()
```

### Dockerfile for API Service
```dockerfile
# docker/Dockerfile.api
FROM python:3.12-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy dependency files
COPY pyproject.toml uv.lock ./

# Install dependencies
RUN uv sync --frozen --no-dev

# Copy application code
COPY src/ src/
COPY alembic/ alembic/
COPY alembic.ini .

# Run migrations then start server
CMD ["sh", "-c", "uv run alembic upgrade head && uv run uvicorn src.thinktank.api.main:app --host 0.0.0.0 --port 8000"]
```

### Health Endpoint
```python
# Source: FastAPI docs, standard pattern
from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()

@router.get("/health")
async def health_check(session: AsyncSession = Depends(get_session)):
    try:
        await session.execute(text("SELECT 1"))
        return {"status": "healthy", "service": "thinktank-api"}
    except Exception:
        return JSONResponse(
            status_code=503,
            content={"status": "unhealthy", "service": "thinktank-api"},
        )
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `create_engine()` + sync Session | `create_async_engine()` + AsyncSession | SQLAlchemy 1.4 (2021), stable in 2.0 | All DB ops must be async. No sync fallback in async app. |
| `declarative_base()` function | `class Base(DeclarativeBase)` | SQLAlchemy 2.0 (2023) | Type-safe mapping with `Mapped` and `mapped_column`. |
| `Column()` definitions | `mapped_column()` with type annotations | SQLAlchemy 2.0 (2023) | Better IDE support, type inference, fewer boilerplate. |
| `on_startup`/`on_shutdown` events | `lifespan` context manager | FastAPI 0.93 (2023) | Colocated startup/shutdown logic, resource sharing via yield. |
| pip + requirements.txt | uv + pyproject.toml + uv.lock | 2024-2025 | 10-100x faster installs, deterministic lockfile, venv management. |
| Black + Flake8 + isort | ruff | 2023-2024 | Single tool, 100x faster, one config section. |
| `event_loop` fixture override | `loop_scope` parameter | pytest-asyncio 1.0 (2025) | Cleaner API, no deprecated fixture. |
| factory-boy `SQLAlchemyModelFactory` | Plain factory functions (for async) | N/A | factory-boy lacks native async support. Simple functions work. |

**Deprecated/outdated:**
- `declarative_base()` function: Use `class Base(DeclarativeBase)` instead.
- `Column()` in 2.0: Use `mapped_column()` with `Mapped[]` type hints.
- FastAPI `on_startup`/`on_shutdown`: Use `lifespan` parameter instead. Old events will be removed.
- pytest-asyncio `event_loop` fixture: Removed in 1.0. Use `loop_scope` configuration.

## Open Questions

1. **pg_trgm extension in migration**
   - What we know: The schema requires `pg_trgm` for candidate thinker dedup (Phase 3), not Phase 1. But it's cleaner to enable it in the initial migration.
   - What's unclear: Whether to enable it now or defer to Phase 3.
   - Recommendation: Enable `CREATE EXTENSION IF NOT EXISTS pg_trgm` in the initial migration. It's a one-liner and avoids a future migration just for the extension.

2. **system_config DB override timing**
   - What we know: Config precedence is env > DB > code defaults. Querying DB at startup requires the engine to exist first.
   - What's unclear: Whether to implement the full DB override in Phase 1 or stub it.
   - Recommendation: Implement the env + code defaults layer fully in Phase 1 (pydantic-settings). Stub the DB override as a function that can be called after engine creation, but mark it as "extended in Phase 2" since the system_config table is seeded during bootstrap which is Phase 7. The model and table should exist in Phase 1.

3. **Test database lifecycle**
   - What we know: STANDARDS.md requires "migrations run once per test session, each test gets a transaction that rolls back."
   - What's unclear: Whether to use `Base.metadata.create_all` (faster, no migration testing) or `alembic upgrade head` (tests migrations but slower).
   - Recommendation: Use `create_all` for the main test session fixture (fast), and have a separate dedicated `test_migrations.py` that runs `alembic upgrade head` / `alembic downgrade base` against a fresh schema to verify migration correctness.

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest >=8.0 + pytest-asyncio >=1.3.0 |
| Config file | `pyproject.toml` (Wave 0 -- must be created) |
| Quick run command | `uv run pytest tests/unit -x --tb=short` |
| Full suite command | `uv run pytest tests/ -x --tb=short` |

### Phase Requirements -> Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| FNDTN-01 | 14 tables created by migration | integration | `uv run pytest tests/integration/test_migrations.py -x` | Wave 0 |
| FNDTN-02 | All models instantiable via factories, persist to DB | integration | `uv run pytest tests/integration/test_models.py -x` | Wave 0 |
| FNDTN-03 | Advisory lock prevents concurrent migrations | integration | `uv run pytest tests/integration/test_migrations.py::test_advisory_lock -x` | Wave 0 |
| FNDTN-04 | Config precedence: env > DB > defaults | unit | `uv run pytest tests/unit/test_config.py -x` | Wave 0 |
| FNDTN-05 | Log entries are JSON with required fields | unit | `uv run pytest tests/unit/test_logging.py -x` | Wave 0 |
| FNDTN-06 | Health endpoint returns 200 when DB connected | integration | `uv run pytest tests/integration/test_health.py -x` | Wave 0 |
| FNDTN-07 | FastAPI app starts with lifespan, connects to DB | integration | `uv run pytest tests/integration/test_health.py -x` | Wave 0 |
| FNDTN-08 | ruff, mypy pass on codebase | unit | `uv run ruff check src/ && uv run mypy src/` | Wave 0 |
| FNDTN-09 | Docker images build successfully | integration | `docker compose -f docker-compose.yml build` | Wave 0 |
| QUAL-01 | Test suite runs in <60s against real Postgres | integration | `uv run pytest tests/ -x --tb=short` | Wave 0 |
| QUAL-02 | Factory functions for all models | unit | `uv run pytest tests/unit/test_factories.py -x` | Wave 0 |
| QUAL-06 | Architecture docs exist | manual-only | Verify `docs/architecture.md` exists and is non-empty | Wave 0 |

### Sampling Rate
- **Per task commit:** `uv run pytest tests/unit -x --tb=short`
- **Per wave merge:** `uv run pytest tests/ -x --tb=short`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `pyproject.toml` -- project configuration with pytest settings (does not exist yet)
- [ ] `tests/conftest.py` -- shared fixtures (engine, session, factories)
- [ ] `tests/factories.py` -- factory functions for all 14 model types
- [ ] `tests/unit/__init__.py` -- unit test package
- [ ] `tests/integration/__init__.py` -- integration test package
- [ ] `docker-compose.test.yml` -- PostgreSQL for test runs

*(All files are Wave 0 gaps since the project is greenfield -- zero existing infrastructure)*

## Sources

### Primary (HIGH confidence)
- [SQLAlchemy 2.0 Asyncio Documentation](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html) -- async engine, session, declarative patterns
- [Alembic Cookbook](https://alembic.sqlalchemy.org/en/latest/cookbook.html) -- async env.py configuration
- [FastAPI Lifespan Events](https://fastapi.tiangolo.com/advanced/events/) -- lifespan context manager pattern
- [FastAPI Settings](https://fastapi.tiangolo.com/advanced/settings/) -- pydantic-settings integration
- [structlog 25.5.0 contextvars docs](https://www.structlog.org/en/stable/contextvars.html) -- correlation ID binding
- [pydantic-settings docs](https://docs.pydantic.dev/latest/concepts/pydantic_settings/) -- environment variable loading, custom sources
- [uv dependency management docs](https://docs.astral.sh/uv/concepts/projects/dependencies/) -- dependency groups, pyproject.toml syntax
- [pytest-asyncio 1.3.0 docs](https://pytest-asyncio.readthedocs.io/en/stable/concepts.html) -- loop_scope configuration
- [ruff configuration](https://docs.astral.sh/ruff/settings/) -- pyproject.toml settings

### Secondary (MEDIUM confidence)
- [FastAPI + SQLAlchemy 2.0 async patterns](https://dev-faizan.medium.com/fastapi-sqlalchemy-2-0-modern-async-database-patterns-7879d39b6843) -- project structure conventions
- [FastAPI best practices repo](https://github.com/zhanymkanov/fastapi-best-practices) -- community conventions
- [SQLAlchemy UUID discussion #12792](https://github.com/sqlalchemy/sqlalchemy/discussions/12792) -- UUID PK patterns
- [asgi-correlation-id](https://github.com/snok/asgi-correlation-id) -- ASGI middleware for correlation IDs
- [Production logging guide](https://medium.com/@laxsuryavanshi.dev/production-grade-logging-for-fastapi-applications-a-complete-guide-f384d4b8f43b) -- structlog + FastAPI integration

### Tertiary (LOW confidence)
- [factory-boy async support discussion](https://github.com/FactoryBoy/factory_boy/issues/679) -- native async support still not available; plain factory functions recommended for async projects

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- all technologies verified against PyPI, official docs, and project research. Versions confirmed current.
- Architecture: HIGH -- FastAPI + SQLAlchemy 2.0 async is the most documented Python web stack. Project structure follows established community patterns.
- Pitfalls: HIGH -- common pitfalls documented across official issue trackers and community resources. Async session, pool exhaustion, and Alembic autogenerate issues are well-known.
- Testing: HIGH -- pytest + pytest-asyncio with real PostgreSQL is the standard pattern. Docker Compose for test database is proven.

**Research date:** 2026-03-08
**Valid until:** 2026-04-08 (stable stack, slow-moving ecosystem)
