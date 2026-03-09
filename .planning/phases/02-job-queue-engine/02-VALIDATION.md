---
phase: 2
slug: job-queue-engine
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-09
---

# Phase 2 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 8.x + pytest-asyncio 0.25+ |
| **Config file** | `pyproject.toml` (already configured) |
| **Quick run command** | `uv run pytest tests/unit -x -q` |
| **Full suite command** | `uv run pytest tests/ -x` |
| **Estimated runtime** | ~30 seconds |

---

## Sampling Rate

- **After every task commit:** Run `uv run pytest tests/unit -x -q`
- **After every plan wave:** Run `uv run pytest tests/ -x`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 30 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 02-01-01 | 01 | 1 | QUEUE-01 | integration | `uv run pytest tests/integration/test_claim.py -x` | ❌ W0 | ⬜ pending |
| 02-01-02 | 01 | 1 | QUEUE-02 | integration | `uv run pytest tests/integration/test_worker_loop.py -x` | ❌ W0 | ⬜ pending |
| 02-01-03 | 01 | 1 | QUEUE-03 | unit+integration | `uv run pytest tests/unit/test_retry.py tests/integration/test_claim.py -x` | ❌ W0 | ⬜ pending |
| 02-01-04 | 01 | 1 | QUEUE-04 | integration | `uv run pytest tests/integration/test_reclaim.py -x` | ❌ W0 | ⬜ pending |
| 02-01-05 | 01 | 1 | QUEUE-08 | unit | `uv run pytest tests/unit/test_errors.py -x` | ❌ W0 | ⬜ pending |
| 02-02-01 | 02 | 2 | QUEUE-05 | unit+integration | `uv run pytest tests/unit/test_rate_limiter.py tests/integration/test_rate_limit.py -x` | ❌ W0 | ⬜ pending |
| 02-02-02 | 02 | 2 | QUEUE-06 | unit+integration | `uv run pytest tests/unit/test_backpressure.py tests/integration/test_backpressure.py -x` | ❌ W0 | ⬜ pending |
| 02-02-03 | 02 | 2 | QUEUE-07 | integration | `uv run pytest tests/integration/test_kill_switch.py -x` | ❌ W0 | ⬜ pending |
| 02-02-04 | 02 | 2 | QUAL-04 | unit | `uv run pytest tests/contract/ -x` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/unit/test_retry.py` — backoff math (QUEUE-03)
- [ ] `tests/unit/test_backpressure.py` — priority demotion logic (QUEUE-06)
- [ ] `tests/unit/test_errors.py` — error categorization (QUEUE-08)
- [ ] `tests/unit/test_rate_limiter.py` — sliding window math (QUEUE-05)
- [ ] `tests/integration/test_claim.py` — concurrent claim safety (QUEUE-01)
- [ ] `tests/integration/test_reclaim.py` — stale job reclamation (QUEUE-04)
- [ ] `tests/integration/test_kill_switch.py` — kill switch behavior (QUEUE-07)
- [ ] `tests/integration/test_rate_limit.py` — rate_limit_usage table (QUEUE-05)
- [ ] `tests/integration/test_backpressure.py` — queue depth queries (QUEUE-06)
- [ ] `tests/integration/test_worker_loop.py` — full loop lifecycle (QUEUE-02)
- [ ] `tests/contract/__init__.py` — new test directory
- [ ] `tests/contract/test_handler_contracts.py` — handler protocol (QUAL-04)

---

## Manual-Only Verifications

*All phase behaviors have automated verification.*

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 30s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
