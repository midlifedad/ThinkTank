---
phase: 6
slug: discovery-autonomous-growth
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-09
---

# Phase 6 — Validation Strategy

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
| 06-01-01 | 01 | 1 | DISC-01 | unit | `uv run pytest tests/unit/test_name_extractor.py -x` | ❌ W0 | ⬜ pending |
| 06-01-02 | 01 | 1 | DISC-01 | unit | `uv run pytest tests/unit/test_candidate_surfacer.py -x` | ❌ W0 | ⬜ pending |
| 06-01-03 | 01 | 1 | DISC-05 | unit | `uv run pytest tests/unit/test_quota_manager.py -x` | ❌ W0 | ⬜ pending |
| 06-02-01 | 02 | 2 | DISC-02 | unit | `uv run pytest tests/unit/test_podcast_api_client.py -x` | ❌ W0 | ⬜ pending |
| 06-02-02 | 02 | 2 | DISC-02 | integration | `uv run pytest tests/integration/test_guest_discovery.py -x` | ❌ W0 | ⬜ pending |
| 06-02-03 | 02 | 2 | DISC-01,DISC-05 | integration | `uv run pytest tests/integration/test_candidate_scanning.py -x` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/unit/test_name_extractor.py` — regex name extraction from episode metadata (DISC-01)
- [ ] `tests/unit/test_candidate_surfacer.py` — candidate surfacing with 3+ appearance threshold (DISC-01)
- [ ] `tests/unit/test_quota_manager.py` — daily quota enforcement (DISC-05)
- [ ] `tests/unit/test_podcast_api_client.py` — Listen Notes and Podcast Index API clients (DISC-02)
- [ ] `tests/integration/test_guest_discovery.py` — guest discovery handler with API mocking (DISC-02)
- [ ] `tests/integration/test_candidate_scanning.py` — scan_for_candidates handler lifecycle (DISC-01/05)

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Listen Notes API returns guest appearances | DISC-02 | Requires live API key and network | Call with real API key, verify response parsing |
| Podcast Index API returns guest appearances | DISC-02 | Requires live API key and network | Call with real API key, verify response parsing |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 30s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
