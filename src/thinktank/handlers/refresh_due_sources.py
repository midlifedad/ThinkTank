"""Handler: refresh_due_sources -- Discovery orchestration with tier-based scheduling.

Queries for sources that are due for refresh based on their tier-specific
refresh_interval_hours, then creates fetch_podcast_feed jobs for each.

Tier scheduling:
    Tier 1: 6h
    Tier 2: 24h
    Tier 3: 168h (weekly)

Sources are due when:
    - active = true AND approval_status = 'approved'
    - last_fetched + refresh_interval_hours < NOW()
    - OR last_fetched IS NULL (never fetched)

Spec reference: Section 5.6.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import structlog
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from thinktank.models.job import Job

logger = structlog.get_logger(__name__)


def _now() -> datetime:
    """Return current UTC time as timezone-aware datetime (TIMESTAMPTZ)."""
    return datetime.now(UTC)


async def handle_refresh_due_sources(session: AsyncSession, job: Job) -> None:
    """Find sources due for refresh and create fetch_podcast_feed jobs for each.

    Uses PostgreSQL MAKE_INTERVAL for interval arithmetic on the
    refresh_interval_hours column.

    Args:
        session: Active database session.
        job: The refresh_due_sources job (payload is ignored).
    """
    log = logger.bind(job_id=str(job.id))

    # a. Query for due sources (id + source_type for job-type branching)
    result = await session.execute(
        text("""
            SELECT id, source_type FROM sources
            WHERE active = true
              AND approval_status = 'approved'
              AND (
                  last_fetched IS NULL
                  OR last_fetched + MAKE_INTERVAL(hours => refresh_interval_hours) < LOCALTIMESTAMP
              )
        """)
    )
    due_sources = [(row[0], row[1]) for row in result.fetchall()]

    log.info("due_sources_found", count=len(due_sources))

    # b. Create correct fetch job for each due source, skipping any that already
    # have an in-flight job (pending/running/retrying) for the same source_id.
    now = _now()
    enqueued = 0
    skipped = 0
    for source_id, source_type in due_sources:
        job_type = "fetch_youtube_channel" if source_type == "youtube_channel" else "fetch_podcast_feed"

        existing = await session.execute(
            select(Job.id).where(
                Job.job_type == job_type,
                Job.payload["source_id"].astext == str(source_id),
                Job.status.in_(["pending", "running", "retrying"]),
            )
        )
        if existing.scalar_one_or_none() is not None:
            skipped += 1
            continue

        fetch_job = Job(
            id=uuid.uuid4(),
            job_type=job_type,
            payload={"source_id": str(source_id)},
            priority=2,
            status="pending",
            attempts=0,
            max_attempts=3,
            created_at=now,
        )
        session.add(fetch_job)
        enqueued += 1

    # d. Commit
    await session.commit()

    log.info("fetch_jobs_created", enqueued=enqueued, skipped=skipped)
