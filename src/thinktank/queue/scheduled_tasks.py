"""Recurring task definitions shared by the admin scheduler UI and the worker.

Source: ARCH-REVIEW 2026-05-28 (A1). These definitions previously lived in
``admin/routers/pipeline.py``, which meant the worker could not import them
without reaching into the admin layer. The worker's recurring-task scheduler
(``worker/recurring.py``) and the admin scheduler editor both consume this
module so the UI and the executor can never drift apart.

Schedule state for each task is persisted in ``system_config`` under the key
``scheduler_<key>`` as a JSONB dict::

    {
        "frequency_hours": 1,
        "enabled": true,
        "last_run_at": "2026-05-28T12:00:00+00:00",
        "next_run_at": "2026-05-28T13:00:00+00:00"
    }

Tasks with ``job_type=None`` are informational in the UI only -- they are
executed by dedicated in-process schedulers (LLM governance), not by the
recurring-task executor.
"""

from __future__ import annotations

# Configurable scheduled tasks. ``default_hours`` applies when no
# scheduler_<key> config row exists; tasks default to ENABLED so a fresh
# deployment polls feeds and sweeps transcriptions without operator setup.
SCHEDULED_TASKS: list[dict] = [
    {
        "key": "refresh_due_sources",
        "label": "Refresh Due Sources",
        "default_hours": 1,
        "job_type": "refresh_due_sources",
    },
    {
        "key": "scan_for_candidates",
        "label": "Scan for Candidates",
        "default_hours": 24,
        "job_type": "scan_for_candidates",
    },
    {
        "key": "enqueue_pending_transcriptions",
        "label": "Sweep Pending Transcriptions",
        "default_hours": 1,
        "job_type": "enqueue_pending_transcriptions",
    },
    {
        "key": "embed_pending_content",
        "label": "Embed Pending Transcripts",
        "default_hours": 1,
        "job_type": "embed_pending_content",
    },
    {
        "key": "rollup_api_usage",
        "label": "API Usage Rollup",
        "default_hours": 1,
        "job_type": "rollup_api_usage",
    },
    {"key": "llm_health_check", "label": "LLM Health Check", "default_hours": 6, "job_type": None},
    {"key": "llm_daily_digest", "label": "LLM Daily Digest", "default_hours": 24, "job_type": None},
    {"key": "llm_weekly_audit", "label": "LLM Weekly Audit", "default_hours": 168, "job_type": None},
]

# Lookup for quick validation
SCHEDULED_TASK_MAP: dict[str, dict] = {t["key"]: t for t in SCHEDULED_TASKS}
