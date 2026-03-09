---
phase: 3
slug: content-ingestion-pipeline
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-09
---

# Phase 3 — Validation Strategy

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
| 03-01-01 | 01 | 1 | INGEST-01 | unit+integration | `uv run pytest tests/unit/test_url_normalizer.py tests/unit/test_fingerprint.py tests/integration/test_dedup.py -x` | ❌ W0 | ⬜ pending |
| 03-01-02 | 01 | 1 | INGEST-02 | unit+integration | `uv run pytest tests/unit/test_duration_parser.py tests/unit/test_content_filter.py -x` | ❌ W0 | ⬜ pending |
| 03-02-01 | 02 | 1 | INGEST-03 | unit+integration | `uv run pytest tests/unit/test_feed_parser.py tests/integration/test_feed_poll.py -x` | ❌ W0 | ⬜ pending |
| 03-02-02 | 02 | 1 | INGEST-04 | integration | `uv run pytest tests/integration/test_source_scheduling.py -x` | ❌ W0 | ⬜ pending |
| 03-02-03 | 02 | 1 | INGEST-05 | integration | `uv run pytest tests/integration/test_source_approval.py -x` | ❌ W0 | ⬜ pending |
| 03-03-01 | 03 | 2 | INGEST-06 | unit+integration | `uv run pytest tests/unit/test_name_matcher.py tests/integration/test_attribution.py -x` | ❌ W0 | ⬜ pending |
| 03-03-02 | 03 | 2 | INGEST-07 | integration | `uv run pytest tests/integration/test_discovery_orchestrator.py -x` | ❌ W0 | ⬜ pending |
| 03-03-03 | 03 | 2 | DISC-03 | integration | `uv run pytest tests/integration/test_attribution.py -x` | ❌ W0 | ⬜ pending |
| 03-03-04 | 03 | 2 | DISC-04 | unit+integration | `uv run pytest tests/unit/test_trigram_dedup.py tests/integration/test_trigram_dedup.py -x` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/unit/test_url_normalizer.py` — URL canonicalization (INGEST-01)
- [ ] `tests/unit/test_fingerprint.py` — sha256 content fingerprinting (INGEST-01)
- [ ] `tests/unit/test_duration_parser.py` — itunes:duration parsing (INGEST-02)
- [ ] `tests/unit/test_content_filter.py` — title pattern and duration filtering (INGEST-02)
- [ ] `tests/unit/test_feed_parser.py` — RSS/Atom entry extraction (INGEST-03)
- [ ] `tests/unit/test_name_matcher.py` — thinker name extraction from text (DISC-03)
- [ ] `tests/unit/test_trigram_dedup.py` — trigram similarity logic (DISC-04)
- [ ] `tests/integration/test_dedup.py` — 3-layer dedup pipeline (INGEST-01)
- [ ] `tests/integration/test_feed_poll.py` — full feed polling lifecycle (INGEST-03)
- [ ] `tests/integration/test_source_scheduling.py` — tier-based refresh scheduling (INGEST-04)
- [ ] `tests/integration/test_source_approval.py` — approval gating (INGEST-05)
- [ ] `tests/integration/test_attribution.py` — content-thinker attribution (DISC-03)
- [ ] `tests/integration/test_trigram_dedup.py` — pg_trgm similarity queries (DISC-04)
- [ ] `tests/integration/test_discovery_orchestrator.py` — discovery job creation (INGEST-07)

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
