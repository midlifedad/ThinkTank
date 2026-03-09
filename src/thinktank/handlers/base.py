"""JobHandler Protocol defining the handler interface.

Every job handler must conform to this protocol: an async callable
that takes an AsyncSession and a Job, and returns None.

Handlers should raise on failure. The worker loop catches exceptions,
categorizes them via categorize_error(), and calls fail_job().
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from src.thinktank.models.job import Job


class JobHandler(Protocol):
    """Protocol for job handlers. Every handler must implement this.

    Handlers receive an AsyncSession and the Job being processed.
    They should raise on failure (caught and categorized by the worker loop).
    The session is a fresh session -- NOT the claim session.
    """

    async def __call__(self, session: AsyncSession, job: "Job") -> None: ...
