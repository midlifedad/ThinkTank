---
phase: 7
slug: operations-api-polish
status: draft
nyquist_compliant: true
wave_0_complete: false
created: 2026-03-09
---

# Phase 7 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 8.x + pytest-asyncio 0.25+ |
| **Config file** | `pyproject.toml` (already configured) |
| **Quick run command** | `uv run pytest tests/unit -x -q` |
| **Full suite command** | `uv run pytest tests/ -x` |
| **Estimated runtime** | ~45 seconds |

---

## Sampling Rate

- **After every task commit:** Run `uv run pytest tests/unit -x -q`
- **After every plan wave:** Run `uv run pytest tests/ -x`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 45 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 07-01-T1 | 01 | 1 | API-01,API-02,API-03,API-04,API-05,API-06,QUAL-03 | contract | `uv run pytest tests/contract/test_api_thinkers.py tests/contract/test_api_sources.py tests/contract/test_api_content.py tests/contract/test_api_jobs.py tests/contract/test_api_config.py tests/contract/test_api_openapi.py -x -v` | ❌ W0 | ⬜ pending |
| 07-01-T2 | 01 | 1 | OPS-03 | contract | `uv run pytest tests/contract/test_rollup_handler.py -x -v` | ❌ W0 | ⬜ pending |
| 07-02-T1 | 02 | 1 | OPS-01,OPS-04 | integration | `uv run pytest tests/integration/test_admin_dashboard.py -x -v` | ❌ W0 | ⬜ pending |
| 07-02-T2 | 02 | 1 | OPS-02,OPS-05 | integration | `uv run pytest tests/integration/test_admin_llm_panel.py -x -v` | ❌ W0 | ⬜ pending |
| 07-03-T1 | 03 | 2 | OPS-06 | integration | `uv run pytest tests/integration/test_bootstrap.py -x -v` | ❌ W0 | ⬜ pending |
| 07-03-T2 | 03 | 2 | QUAL-05,QUAL-07 | manual+grep | `grep -q 'Bootstrap' docs/operations-runbook.md && grep -q 'Rollback' docs/operations-runbook.md && grep -q 'Adding a New Job Type' docs/development-guide.md` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/contract/test_api_thinkers.py` — Contract tests for thinkers CRUD (API-01/02)
- [ ] `tests/contract/test_api_sources.py` — Contract tests for sources listing (API-03)
- [ ] `tests/contract/test_api_content.py` — Contract tests for content listing (API-04/05)
- [ ] `tests/contract/test_api_jobs.py` — Contract tests for job status (API-06)
- [ ] `tests/contract/test_api_config.py` — Contract tests for config CRUD
- [ ] `tests/contract/test_api_openapi.py` — OpenAPI docs availability (QUAL-03)
- [ ] `tests/contract/test_rollup_handler.py` — Cost rollup handler contract (OPS-03)
- [ ] `tests/integration/test_admin_dashboard.py` — Admin dashboard integration tests (OPS-01/04)
- [ ] `tests/integration/test_admin_llm_panel.py` — LLM panel + categories integration tests (OPS-02/05)
- [ ] `tests/integration/test_bootstrap.py` — Bootstrap sequence integration tests (OPS-06)

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| HTMX 10-second auto-refresh in browser | OPS-01 | Requires real browser | Open dashboard, verify widgets refresh every 10s |
| Operations runbook accuracy | QUAL-03 | Documentation review | Follow runbook steps on fresh deployment |
| Development guide completeness | QUAL-05 | Documentation review | Attempt to add new job type following guide |

---

## Validation Sign-Off

- [x] All tasks have `<automated>` verify or Wave 0 dependencies
- [x] Sampling continuity: no 3 consecutive tasks without automated verify
- [x] Wave 0 covers all MISSING references
- [x] No watch-mode flags
- [x] Feedback latency < 45s
- [x] `nyquist_compliant: true` set in frontmatter

**Approval:** approved
