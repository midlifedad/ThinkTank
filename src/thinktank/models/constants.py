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
"""

from __future__ import annotations

HEALTHY_CONTENT_STATUSES: tuple[str, ...] = ("done", "cataloged")
WARNING_CONTENT_STATUSES: tuple[str, ...] = ("pending",)
ERROR_CONTENT_STATUSES: tuple[str, ...] = ("error", "skipped")
