---
phase: 7
slug: operations-api-polish
status: draft
nyquist_compliant: false
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
| 07-01-01 | 01 | 1 | API-01,API-02 | unit+contract | `uv run pytest tests/unit/test_api_thinkers.py tests/contract/test_api_endpoints.py -x` | ❌ W0 | ⬜ pending |
| 07-01-02 | 01 | 1 | API-03,API-04,API-05 | unit+contract | `uv run pytest tests/unit/test_api_sources.py tests/unit/test_api_content.py -x` | ❌ W0 | ⬜ pending |
| 07-01-03 | 01 | 1 | API-06 | unit | `uv run pytest tests/unit/test_api_search.py -x` | ❌ W0 | ⬜ pending |
| 07-02-01 | 02 | 1 | OPS-01 | unit | `uv run pytest tests/unit/test_admin_dashboard.py -x` | ❌ W0 | ⬜ pending |
| 07-02-02 | 02 | 1 | OPS-02 | unit | `uv run pytest tests/unit/test_admin_llm_panel.py -x` | ❌ W0 | ⬜ pending |
| 07-02-03 | 02 | 1 | OPS-03 | unit | `uv run pytest tests/unit/test_cost_tracking.py -x` | ❌ W0 | ⬜ pending |
| 07-03-01 | 03 | 2 | OPS-04,OPS-05 | integration | `uv run pytest tests/integration/test_bootstrap.py -x` | ❌ W0 | ⬜ pending |
| 07-03-02 | 03 | 2 | OPS-06,QUAL-03,QUAL-05,QUAL-07 | unit | `uv run pytest tests/unit/test_seed_scripts.py -x` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/unit/test_api_thinkers.py` — REST API endpoint tests for thinkers CRUD (API-01/02)
- [ ] `tests/unit/test_api_sources.py` — REST API endpoint tests for sources CRUD (API-03)
- [ ] `tests/unit/test_api_content.py` — REST API endpoint tests for content CRUD (API-04/05)
- [ ] `tests/unit/test_api_search.py` — Search/filter endpoint tests (API-06)
- [ ] `tests/contract/test_api_endpoints.py` — Contract tests for all API endpoints
- [ ] `tests/unit/test_admin_dashboard.py` — Admin dashboard widget tests (OPS-01)
- [ ] `tests/unit/test_admin_llm_panel.py` — LLM decision panel tests (OPS-02)
- [ ] `tests/unit/test_cost_tracking.py` — API cost tracking tests (OPS-03)
- [ ] `tests/integration/test_bootstrap.py` — Bootstrap sequence tests (OPS-04/05)
- [ ] `tests/unit/test_seed_scripts.py` — Seed script idempotency tests (OPS-06)

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| HTMX 10-second auto-refresh in browser | OPS-01 | Requires real browser | Open dashboard, verify widgets refresh every 10s |
| Operations runbook accuracy | QUAL-03 | Documentation review | Follow runbook steps on fresh deployment |
| Development guide completeness | QUAL-05 | Documentation review | Attempt to add new job type following guide |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 45s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
