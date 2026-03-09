---
phase: 01-foundation-layer
plan: 02
subsystem: database
tags: [sqlalchemy, postgresql, async, models, factories, uuid, jsonb]

# Dependency graph
requires:
  - phase: 01-foundation-layer/01
    provides: "Project scaffold with pyproject.toml, uv, FastAPI app skeleton"
provides:
  - "14 SQLAlchemy 2.0 async models matching ThinkTank spec Section 3"
  - "Base class with AsyncAttrs, uuid_pk type, TimestampMixin"
  - "Factory functions (make_* and create_*) for all 14 model types"
  - "60 unit tests validating factory defaults, overrides, uniqueness"
affects: [01-foundation-layer/03, 02-job-queue-engine, 03-content-ingestion-pipeline]

# Tech tracking
tech-stack:
  added: [sqlalchemy-2.0-async, postgresql-dialect-jsonb, postgresql-dialect-array]
  patterns: [declarative-base-with-asyncattrs, uuid-pk-annotated-type, timestamp-mixin, make-create-factory-pattern, selectin-lazy-loading]

key-files:
  created:
    - src/thinktank/models/base.py
    - src/thinktank/models/__init__.py
    - src/thinktank/models/category.py
    - src/thinktank/models/thinker.py
    - src/thinktank/models/source.py
    - src/thinktank/models/content.py
    - src/thinktank/models/candidate.py
    - src/thinktank/models/job.py
    - src/thinktank/models/review.py
    - src/thinktank/models/config_table.py
    - src/thinktank/models/rate_limit.py
    - src/thinktank/models/api_usage.py
    - tests/factories.py
    - tests/unit/test_factories.py
  modified:
    - pyproject.toml

key-decisions:
  - "Used Annotated type alias (uuid_pk) for reusable UUID PK pattern across all models"
  - "Used server_default=text('NOW()') for all timestamp defaults to let PostgreSQL handle timestamps"
  - "Used JSONB and ARRAY from postgresql dialect (not generic types) to ensure correct Alembic autogenerate"
  - "Defined relationships with lazy='selectin' for async-safe eager loading"
  - "SystemConfig uses TEXT PK (config key), not UUID, matching spec exactly"
  - "Plain factory functions over factory-boy for async compatibility and simplicity"

patterns-established:
  - "uuid_pk annotated type: Mapped[uuid_pk] for all UUID primary keys"
  - "TimestampMixin: inheritable created_at with server-side NOW() default"
  - "make_*/create_* factory pattern: in-memory defaults + async persist variants"
  - "JSONB/ARRAY columns always use postgresql dialect imports"
  - "Composite PK for junction tables (thinker_categories, content_thinkers)"

requirements-completed: [FNDTN-01, FNDTN-02, QUAL-02]

# Metrics
duration: 7min
completed: 2026-03-09
---

# Phase 1 Plan 2: SQLAlchemy Models and Factory Functions Summary

**14 SQLAlchemy 2.0 async models with UUID PKs, JSONB/ARRAY dialect types, selectin relationships, and 14 pairs of make_/create_ factory functions with 60 passing unit tests**

## Performance

- **Duration:** 7 min
- **Started:** 2026-03-09T00:47:02Z
- **Completed:** 2026-03-09T00:54:30Z
- **Tasks:** 2 (1 auto + 1 TDD)
- **Files modified:** 15

## Accomplishments
- All 14 tables from ThinkTank spec Section 3 represented as SQLAlchemy 2.0 async models
- PostgreSQL-specific types (JSONB, ARRAY) used correctly from dialect, not generic types
- Composite indexes on jobs (claim query), rate_limit_usage (sliding window), api_usage (timeseries)
- Factory functions for all 14 model types with make_ (in-memory) and create_ (async persist) variants
- 60 unit tests verifying factory defaults, field overrides, and ID uniqueness

## Task Commits

Each task was committed atomically:

1. **Task 1: SQLAlchemy 2.0 async models for all 14 tables** - `36f398e` (feat)
2. **Task 2 RED: Failing tests for factory functions** - `d3b2832` (test)
3. **Task 2 GREEN: Factory functions implementation** - `2519026` (feat)

## Files Created/Modified
- `src/thinktank/models/base.py` - DeclarativeBase with AsyncAttrs, uuid_pk type, TimestampMixin
- `src/thinktank/models/__init__.py` - Re-exports all 14 model classes + Base
- `src/thinktank/models/category.py` - Category (self-referential) and ThinkerCategory junction
- `src/thinktank/models/thinker.py` - Thinker, ThinkerProfile (JSONB), ThinkerMetrics
- `src/thinktank/models/source.py` - Source with JSONB config, approval workflow
- `src/thinktank/models/content.py` - Content with dedup columns, ContentThinker junction
- `src/thinktank/models/candidate.py` - CandidateThinker with ARRAY fields
- `src/thinktank/models/job.py` - Job with composite claim index
- `src/thinktank/models/review.py` - LLMReview audit trail
- `src/thinktank/models/config_table.py` - SystemConfig with TEXT PK
- `src/thinktank/models/rate_limit.py` - RateLimitUsage with sliding-window index
- `src/thinktank/models/api_usage.py` - ApiUsage with timeseries index
- `tests/factories.py` - 14 make_* + 14 create_* factory functions (390 lines)
- `tests/unit/test_factories.py` - 60 unit tests for factory behavior
- `pyproject.toml` - Added build-system for package installation

## Decisions Made
- Used `Annotated` type alias `uuid_pk` for reusable UUID PK pattern, avoiding repetition across 12 models
- Used `server_default=text("NOW()")` for timestamps rather than Python-side defaults, ensuring DB consistency
- Used `JSONB` and `ARRAY(sa.Text)` from `sqlalchemy.dialects.postgresql` to prevent Alembic autogenerate issues with generic types
- Set `lazy="selectin"` on key relationships (thinker.sources, source.content) for async-safe eager loading
- SystemConfig uses TEXT primary key matching spec exactly (not UUID like other tables)
- Chose plain factory functions over factory-boy for async compatibility and reduced dependency surface

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Added build-system to pyproject.toml**
- **Found during:** Task 1
- **Issue:** pyproject.toml lacked `[build-system]` section, preventing `uv run` from installing the package as editable
- **Fix:** Added `[build-system] requires = ["hatchling"]` and `build-backend = "hatchling.build"`
- **Files modified:** pyproject.toml
- **Verification:** `uv run python -c "from thinktank.models import Base"` succeeds
- **Committed in:** 36f398e (part of Task 1 commit)

---

**Total deviations:** 1 auto-fixed (1 blocking)
**Impact on plan:** Build-system addition was required for package installation. No scope creep.

## Issues Encountered
None -- plan executed cleanly after resolving the build-system dependency.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- All 14 models are ready for Alembic migration generation (Plan 01-03)
- Factory functions are ready for integration tests (Plan 01-03)
- Base.metadata contains all table definitions for `create_all` in test fixtures

## Self-Check: PASSED

- All 15 files verified present on disk
- All 3 commits verified in git history (36f398e, d3b2832, 2519026)
- 14 tables confirmed in Base.metadata
- 60 unit tests passing
- All PostgreSQL-specific types verified (JSONB, ARRAY)
- All selectin relationships verified

---
*Phase: 01-foundation-layer*
*Completed: 2026-03-09*
