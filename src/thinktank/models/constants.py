"""Shared enum-like status tuples used by workers, templates, and tests.

Single source of truth for Content.status health bucketing. If a new
status value is introduced it must be added here so the admin dashboard
and worker pipeline stay in sync. Adding a value in worker code without
updating these tuples means the admin dashboard silently renders it as
a neutral "unknown" state.

Values observed in the wild:
- done, cataloged: successful terminal states (healthy)
- pending: still in queue (warning / in-progress)
- error: hard failure (error)
- skipped: intentionally skipped (error-adjacent, surfaced to operator)

The ALLOWED_* tuples drive both the DB CHECK constraints (migration 006)
and runtime validation. Any new status value MUST be added here AND
introduced via a new migration -- otherwise INSERTs will be rejected by
the DB.
"""

from __future__ import annotations

HEALTHY_CONTENT_STATUSES: tuple[str, ...] = ("done", "cataloged")
WARNING_CONTENT_STATUSES: tuple[str, ...] = ("pending",)
ERROR_CONTENT_STATUSES: tuple[str, ...] = ("error", "skipped")

# --- CHECK constraint domains (DATA-REVIEW H3) ---------------------------

# Content.status -- terminal + in-flight states. Matches the plan's allowed
# set exactly.
ALLOWED_CONTENT_STATUSES: tuple[str, ...] = (
    "done",
    "cataloged",
    "pending",
    "skipped",
    "error",
)

# Source.approval_status -- superset of the plan's approved/pending/rejected
# because production code (admin/routers/sources.py, admin/routers/thinkers.py,
# llm/decisions.py, agent/tools.py) writes additional intermediate states:
# pending_llm, awaiting_llm, rejected_by_llm, pending_human. Narrowing to
# just {approved, pending, rejected} would crash the LLM approval pipeline.
ALLOWED_SOURCE_APPROVAL_STATUSES: tuple[str, ...] = (
    "approved",
    "pending",
    "rejected",
    "pending_llm",
    "awaiting_llm",
    "rejected_by_llm",
    "pending_human",
)

# Job.status -- matches both queue/claim.py transitions and the plan.
ALLOWED_JOB_STATUSES: tuple[str, ...] = (
    "pending",
    "running",
    "retrying",
    "done",
    "failed",
    "cancelled",
)
