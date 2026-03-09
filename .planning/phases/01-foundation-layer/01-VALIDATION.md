---
phase: 1
slug: foundation-layer
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-08
---

# Phase 1 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest >=8.0 + pytest-asyncio >=1.3.0 |
| **Config file** | `pyproject.toml` (Wave 0 — must be created) |
| **Quick run command** | `uv run pytest tests/unit -x --tb=short` |
| **Full suite command** | `uv run pytest tests/ -x --tb=short` |
| **Estimated runtime** | ~30 seconds |

---

## Sampling Rate

- **After every task commit:** Run `uv run pytest tests/unit -x --tb=short`
- **After every plan wave:** Run `uv run pytest tests/ -x --tb=short`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 30 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 01-01-01 | 01 | 1 | FNDTN-07 | integration | `uv run pytest tests/integration/test_health.py -x` | ❌ W0 | ⬜ pending |
| 01-01-02 | 01 | 1 | FNDTN-08 | unit | `uv run ruff check src/ && uv run mypy src/` | ❌ W0 | ⬜ pending |
| 01-02-01 | 02 | 1 | FNDTN-01 | integration | `uv run pytest tests/integration/test_migrations.py -x` | ❌ W0 | ⬜ pending |
| 01-02-02 | 02 | 1 | FNDTN-02 | integration | `uv run pytest tests/integration/test_models.py -x` | ❌ W0 | ⬜ pending |
| 01-02-03 | 02 | 1 | FNDTN-03 | integration | `uv run pytest tests/integration/test_migrations.py::test_advisory_lock -x` | ❌ W0 | ⬜ pending |
| 01-02-04 | 02 | 1 | QUAL-02 | unit | `uv run pytest tests/unit/test_factories.py -x` | ❌ W0 | ⬜ pending |
| 01-03-01 | 03 | 2 | FNDTN-04 | unit | `uv run pytest tests/unit/test_config.py -x` | ❌ W0 | ⬜ pending |
| 01-03-02 | 03 | 2 | FNDTN-05 | unit | `uv run pytest tests/unit/test_logging.py -x` | ❌ W0 | ⬜ pending |
| 01-03-03 | 03 | 2 | FNDTN-06 | integration | `uv run pytest tests/integration/test_health.py -x` | ❌ W0 | ⬜ pending |
| 01-03-04 | 03 | 2 | FNDTN-09 | integration | `docker compose -f docker-compose.yml build` | ❌ W0 | ⬜ pending |
| 01-03-05 | 03 | 2 | QUAL-01 | integration | `uv run pytest tests/ -x --tb=short` | ❌ W0 | ⬜ pending |
| 01-03-06 | 03 | 2 | QUAL-06 | manual-only | Verify `docs/architecture.md` exists and is non-empty | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `pyproject.toml` — project configuration with pytest, ruff, mypy settings
- [ ] `tests/conftest.py` — shared fixtures (async engine, session, factories)
- [ ] `tests/factories.py` — factory functions for all 14 model types
- [ ] `tests/unit/__init__.py` — unit test package
- [ ] `tests/integration/__init__.py` — integration test package
- [ ] `docker-compose.test.yml` — PostgreSQL for test runs

*(All files are Wave 0 gaps since the project is greenfield — zero existing infrastructure)*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Architecture docs exist and are accurate | QUAL-06 | Content quality requires human judgment | Verify `docs/architecture.md` exists, covers service boundaries, data flow, and component responsibilities |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 30s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
