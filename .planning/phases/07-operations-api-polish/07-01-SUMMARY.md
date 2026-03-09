---
phase: 07-operations-api-polish
plan: 01
subsystem: api
tags: [rest-api, fastapi, pydantic, crud, cost-tracking]
dependency_graph:
  requires: []
  provides: [rest-api-layer, api-cost-rollup]
  affects: [admin-dashboard, external-integrations]
tech_stack:
  added: [pydantic-v2-schemas, fastapi-routers]
  patterns: [paginated-response-generic, cte-idempotent-upsert, on-conflict-do-update]
key_files:
  created:
    - src/thinktank/api/schemas.py
    - src/thinktank/api/routers/__init__.py
    - src/thinktank/api/routers/thinkers.py
    - src/thinktank/api/routers/sources.py
    - src/thinktank/api/routers/content.py
    - src/thinktank/api/routers/jobs.py
    - src/thinktank/api/routers/config.py
    - src/thinktank/handlers/rollup_api_usage.py
    - tests/contract/test_api_thinkers.py
    - tests/contract/test_api_sources.py
    - tests/contract/test_api_content.py
    - tests/contract/test_api_jobs.py
    - tests/contract/test_api_config.py
    - tests/contract/test_api_openapi.py
    - tests/contract/test_rollup_handler.py
  modified:
    - src/thinktank/api/main.py
    - src/thinktank/handlers/registry.py
decisions:
  - Used PaginatedResponse[T] generic for consistent pagination across all list endpoints
  - Used CTE-based SQL with NOT EXISTS for idempotent rollup insertion instead of HAVING clause (PostgreSQL grouping restriction)
  - Applied cost estimates in Python after SQL insert to keep API_COST_MAP in application code
  - Used ON CONFLICT DO UPDATE for config upsert (key is TEXT primary key)
metrics:
  duration: 5m 1s
  completed: 2026-03-09T05:57:55Z
  tasks_completed: 2
  tasks_total: 2
  tests_added: 40
  tests_total: 638
---

# Phase 07 Plan 01: REST API Layer Summary

Complete REST API with 5 routers (thinkers, sources, content, jobs, config), Pydantic v2 schemas with PaginatedResponse[T] generic, rollup_api_usage handler aggregating rate_limit_usage into api_usage with per-API cost estimates, and OpenAPI docs at /docs and /redoc.

## Task Completion

| Task | Name | Status | Commit(s) | Key Files |
|------|------|--------|-----------|-----------|
| 1 | Pydantic schemas, API routers, and contract tests | Done | 9f7965c, 8905dd2 | schemas.py, 5 routers, 6 test files |
| 2 | rollup_api_usage handler with cost tracking | Done | 8c57ce4, 8ccfd75 | rollup_api_usage.py, registry.py, test_rollup_handler.py |

## What Was Built

### REST API Endpoints

| Router | Prefix | Endpoints | Features |
|--------|--------|-----------|----------|
| thinkers | /api/thinkers | GET list, GET by id, POST, PATCH | Pagination, filter by tier/status/category |
| sources | /api/sources | GET list | Pagination, filter by thinker_id/approval_status |
| content | /api/content | GET list | Pagination, filter by source_id/thinker_id/status |
| jobs | /api/jobs | GET /status | GROUP BY counts by type and status, recent errors |
| config | /api/config | GET list, GET by key, PUT | Upsert via ON CONFLICT DO UPDATE |

### Pydantic Schemas

- `PaginatedResponse[T]` -- Generic pagination wrapper (items, total, page, size, pages)
- `ThinkerResponse`, `ThinkerCreate`, `ThinkerUpdate`
- `SourceResponse`, `ContentResponse`
- `JobStatusResponse` (by_type, by_status, recent_errors)
- `ConfigResponse`, `ConfigUpdate`
- All response models use `model_config = ConfigDict(from_attributes=True)`

### rollup_api_usage Handler

- Aggregates `rate_limit_usage` rows older than current hour into `api_usage` hourly rollups
- Cost estimates per API: listennotes=$0.005, youtube=$0.001, podcastindex=$0.00, anthropic=$0.015
- Idempotent via CTE + NOT EXISTS (no duplicate rollups)
- Purges raw `rate_limit_usage` rows older than 2 hours
- Registered in handler registry as `"rollup_api_usage"`

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed PostgreSQL grouping error in rollup SQL**
- **Found during:** Task 2 GREEN phase
- **Issue:** `HAVING NOT EXISTS` clause with `r.called_at` reference caused `GroupingError: subquery uses ungrouped column from outer query` in PostgreSQL
- **Fix:** Restructured query to use CTE (`WITH agg AS (...)`) that aggregates first, then filters with `NOT EXISTS` in the main INSERT
- **Files modified:** src/thinktank/handlers/rollup_api_usage.py
- **Commit:** 8ccfd75

## Verification

```
uv run pytest tests/ -x
638 passed, 7 warnings in 8.18s
```

- All 40 new contract tests pass (35 API + 5 rollup handler)
- All 598 existing tests still pass (no regressions)
- OpenAPI docs accessible at /docs, /redoc, /openapi.json
- Handler registry includes rollup_api_usage

## Decisions Made

1. **PaginatedResponse[T] generic** -- Single generic wrapper for all paginated endpoints ensures consistent shape
2. **CTE for idempotent rollup** -- PostgreSQL rejects ungrouped column references in HAVING subqueries; CTE approach cleanly separates aggregation from existence check
3. **Python-side cost application** -- Cost map lives in application code, applied after raw SQL INSERT to avoid embedding business logic in SQL
4. **ON CONFLICT DO UPDATE for config** -- SystemConfig uses TEXT primary key, making PostgreSQL conflict resolution natural for upsert semantics

## Self-Check: PASSED

- All 15 created files exist on disk
- All 4 commits (9f7965c, 8905dd2, 8c57ce4, 8ccfd75) verified in git history
- Full test suite: 638 passed, 0 failed
