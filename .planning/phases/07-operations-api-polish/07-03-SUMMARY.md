---
phase: 07-operations-api-polish
plan: 03
subsystem: operations
tags: [bootstrap, seed-scripts, idempotent, operations-runbook, development-guide, documentation]
dependency_graph:
  requires:
    - 07-01 (REST API layer with config endpoints)
    - 07-02 (Admin dashboard with LLM panel and categories)
  provides:
    - Bootstrap sequence for fresh deployments
    - Operations runbook for system administration
    - Development guide for extending the system
  affects: [deployment, onboarding, maintenance]
tech_stack:
  added: []
  patterns: [uuid5-deterministic-ids, on-conflict-do-update-upsert, idempotent-seed-scripts, standalone-and-importable-scripts]
key_files:
  created:
    - scripts/__init__.py
    - scripts/seed_categories.py
    - scripts/seed_config.py
    - scripts/seed_thinkers.py
    - scripts/bootstrap.py
    - tests/integration/test_bootstrap.py
    - docs/operations-runbook.md
    - docs/development-guide.md
  modified: []
decisions:
  - "Deterministic UUIDs via uuid5(NAMESPACE_DNS, 'thinktank.category.{slug}') for repeatable category seeding"
  - "RETURNING clause on thinker upsert to get actual ID (existing or new) for job creation"
  - "JSONB astext query for duplicate job detection: Job.payload['thinker_id'].astext == str(id)"
  - "4 top-level categories (technology, science, philosophy, economics) with 11 subcategories as initial taxonomy"
metrics:
  duration: 5m 51s
  completed: 2026-03-09T06:09:44Z
  tasks_completed: 2
  tasks_total: 2
  tests_added: 10
  tests_total: 667
---

# Phase 07 Plan 03: Bootstrap and Documentation Summary

Bootstrap seed scripts with deterministic UUIDs and ON CONFLICT upserts for idempotent fresh deployments, plus operations runbook (436 lines) and development guide (669 lines) covering all system administration and extension patterns.

## Task Completion

| Task | Name | Status | Commit(s) | Key Files |
|------|------|--------|-----------|-----------|
| 1 | Bootstrap seed scripts and orchestrator with integration tests | Done | 2b2ecfa (RED), 6f760e9 (GREEN) | 4 seed scripts, test_bootstrap.py |
| 2 | Operations runbook and development guide | Done | 00820e3 | operations-runbook.md, development-guide.md |

## What Was Built

### Seed Scripts

| Script | Purpose | Count | Key Feature |
|--------|---------|-------|-------------|
| `scripts/seed_categories.py` | Category taxonomy seeder | 15 categories | uuid5 deterministic IDs, 4 top-level + 11 subcategories |
| `scripts/seed_config.py` | System config defaults | 10 entries | Raw primitives in JSONB (not nested objects) |
| `scripts/seed_thinkers.py` | Initial thinker list | 5 thinkers | pending_llm status + llm_approval_check jobs |
| `scripts/bootstrap.py` | Full sequence orchestrator | 3 steps + validation | Schema validation, prerequisite checks, worker activation |

### Bootstrap Sequence

1. Validates schema exists (SELECT 1 FROM categories)
2. Seeds 15 categories (4 top-level: technology, science, philosophy, economics)
3. Seeds 10 config defaults (workers_active=false initially)
4. Validates categories exist before thinkers
5. Seeds 5 thinkers (Lex Fridman, Andrew Huberman, Balaji Srinivasan, Tyler Cowen, Joscha Bach)
6. Creates 5 llm_approval_check jobs for LLM review
7. Activates workers (workers_active=true)

### Documentation

| Document | Lines | Sections |
|----------|-------|----------|
| `docs/operations-runbook.md` | 436 | Bootstrap, Post-Deploy Verification, Rollback, Common Problems, Operational Commands, Service Architecture |
| `docs/development-guide.md` | 669 | Project Structure, Adding Job Types, Adding API Endpoints, Adding Categories, Testing Conventions, DB Conventions, Deployment |

### Integration Tests (10 new)

- `test_seed_categories_creates_hierarchy` -- Verifies parent/child relationships
- `test_seed_categories_idempotent` -- Running twice produces same count, no errors
- `test_seed_categories_uses_deterministic_ids` -- uuid5-based IDs match expected values
- `test_seed_config_creates_defaults` -- All 10 config keys with correct values
- `test_seed_config_idempotent` -- Running twice produces no errors
- `test_seed_config_workers_active_false` -- Workers start inactive
- `test_seed_thinkers_creates_with_llm_jobs` -- 5 thinkers + 5 approval jobs
- `test_seed_thinkers_idempotent` -- No duplicate thinkers or jobs on re-run
- `test_bootstrap_full_sequence` -- Full sequence with worker activation verification
- `test_bootstrap_validates_schema` -- Schema validation succeeds on valid DB

## Deviations from Plan

None -- plan executed exactly as written.

## Decisions Made

1. **Deterministic UUIDs via uuid5** -- `uuid5(NAMESPACE_DNS, "thinktank.category.{slug}")` ensures re-running seed_categories produces the same category IDs, making foreign key references stable across environments
2. **RETURNING clause for thinker upsert** -- Using `.returning(Thinker.id)` on the upsert gets the actual row ID (whether inserted or updated), which is needed to check for existing LLM approval jobs
3. **JSONB astext query for job dedup** -- `Job.payload["thinker_id"].astext == str(actual_id)` queries JSONB text extraction to find existing approval jobs without scanning all payloads
4. **4 top-level categories** -- Technology, Science, Philosophy, Economics cover the primary domains of the initial thinker set (AI researchers, neuroscientists, economists, philosophers)

## Verification

```
uv run pytest tests/ -x
667 passed, 7 warnings in 8.58s
```

- All 10 new bootstrap integration tests pass
- All 657 existing tests still pass (no regressions)
- Seed scripts importable: `from scripts.seed_categories import seed_categories` works
- Seed scripts standalone: `python -m scripts.seed_categories` works
- Operations runbook: 436 lines with 6 major sections
- Development guide: 669 lines with 7 major sections

## Self-Check: PASSED

- All 8 created files exist on disk
- Commit 2b2ecfa (Task 1 RED) verified in git log
- Commit 6f760e9 (Task 1 GREEN) verified in git log
- Commit 00820e3 (Task 2) verified in git log
- Full test suite: 667 passed, 0 failed
