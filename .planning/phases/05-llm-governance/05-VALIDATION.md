---
phase: 5
slug: llm-governance
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-09
---

# Phase 5 — Validation Strategy

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
| 05-01-01 | 01 | 1 | GOV-01 | unit | `uv run pytest tests/unit/test_context_snapshot.py -x` | ❌ W0 | ⬜ pending |
| 05-01-02 | 01 | 1 | GOV-02 | unit | `uv run pytest tests/unit/test_llm_client.py -x` | ❌ W0 | ⬜ pending |
| 05-01-03 | 01 | 1 | GOV-03 | unit | `uv run pytest tests/unit/test_decision_parser.py -x` | ❌ W0 | ⬜ pending |
| 05-02-01 | 02 | 2 | GOV-04,GOV-05 | integration | `uv run pytest tests/integration/test_llm_review.py -x` | ❌ W0 | ⬜ pending |
| 05-02-02 | 02 | 2 | GOV-06 | integration | `uv run pytest tests/integration/test_llm_escalation.py -x` | ❌ W0 | ⬜ pending |
| 05-02-03 | 02 | 2 | GOV-07,GOV-08,GOV-09 | integration | `uv run pytest tests/integration/test_llm_scheduled.py -x` | ❌ W0 | ⬜ pending |
| 05-02-04 | 02 | 2 | DISC-06 | integration | `uv run pytest tests/integration/test_candidate_promotion.py -x` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/unit/test_context_snapshot.py` — bounded query logic (GOV-01)
- [ ] `tests/unit/test_llm_client.py` — Anthropic client wrapper (GOV-02)
- [ ] `tests/unit/test_decision_parser.py` — structured output parsing (GOV-03)
- [ ] `tests/integration/test_llm_review.py` — thinker/source approval flow (GOV-04/05)
- [ ] `tests/integration/test_llm_escalation.py` — timeout escalation (GOV-06)
- [ ] `tests/integration/test_llm_scheduled.py` — health checks/digests/audits (GOV-07/08/09)
- [ ] `tests/integration/test_candidate_promotion.py` — candidate promotion via LLM (DISC-06)

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
