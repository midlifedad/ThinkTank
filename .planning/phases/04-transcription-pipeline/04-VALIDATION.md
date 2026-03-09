---
phase: 4
slug: transcription-pipeline
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-09
---

# Phase 4 — Validation Strategy

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
| 04-01-01 | 01 | 1 | TRANS-01 | unit+integration | `uv run pytest tests/unit/test_caption_extractor.py tests/unit/test_transcript_checker.py -x` | ❌ W0 | ⬜ pending |
| 04-01-02 | 01 | 1 | TRANS-03 | unit | `uv run pytest tests/unit/test_audio_processor.py -x` | ❌ W0 | ⬜ pending |
| 04-01-03 | 01 | 1 | TRANS-05 | unit | `uv run pytest tests/unit/test_gpu_scaler.py -x` | ❌ W0 | ⬜ pending |
| 04-02-01 | 02 | 2 | TRANS-02 | integration | `uv run pytest tests/integration/test_process_content.py -x` | ❌ W0 | ⬜ pending |
| 04-02-02 | 02 | 2 | TRANS-04 | unit+integration | `uv run pytest tests/unit/test_gpu_client.py tests/integration/test_gpu_scaling.py -x` | ❌ W0 | ⬜ pending |
| 04-02-03 | 02 | 2 | TRANS-06 | integration | `uv run pytest tests/integration/test_process_content.py -x` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/unit/test_caption_extractor.py` — YouTube caption extraction (TRANS-01)
- [ ] `tests/unit/test_transcript_checker.py` — existing transcript check (TRANS-01)
- [ ] `tests/unit/test_audio_processor.py` — audio download/conversion (TRANS-03)
- [ ] `tests/unit/test_gpu_scaler.py` — Railway scaling logic (TRANS-05)
- [ ] `tests/unit/test_gpu_client.py` — GPU service HTTP client (TRANS-04)
- [ ] `tests/integration/test_process_content.py` — three-pass transcription lifecycle (TRANS-02)
- [ ] `tests/integration/test_gpu_scaling.py` — scale-up/down orchestration (TRANS-04)

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
