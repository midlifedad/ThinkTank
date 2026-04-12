"""Handler: discover_thinker -- Fan-out orchestrator for thinker discovery.

When a thinker is approved, this handler fans out to guest discovery
(Podcast Index search) and kicks off RSS fetches for any pre-approved
sources the thinker already owns (e.g., their own podcast).

Spec reference: Section 5.1 (discovery orchestration).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.thinktank.models.job import Job
from src.thinktank.models.source import Source
from src.thinktank.models.thinker import Thinker

logger = structlog.get_logger(__name__)


def _now() -> datetime:
    """Return current UTC time as timezone-naive datetime."""
    return datetime.now(UTC).replace(tzinfo=None)


async def handle_discover_thinker(
    session: AsyncSession, job: Job
) -> None:
    """Orchestrate discovery for an approved thinker.

    1. Validate thinker is approved and active
    2. Create a discover_guests_podcastindex job for guest appearance search
    3. Create fetch_podcast_feed jobs for any approved, never-fetched owned sources

    Args:
        session: Active database session.
        job: The discover_thinker job with payload containing thinker_id.
    """
    thinker_id_str = job.payload.get("thinker_id")
    log = logger.bind(job_id=str(job.id), thinker_id=thinker_id_str)

    if not thinker_id_str:
        log.warning("discover_thinker_no_thinker_id")
        return

    thinker_id = uuid.UUID(thinker_id_str)
    thinker = await session.get(Thinker, thinker_id)

    if thinker is None:
        log.warning("discover_thinker_thinker_not_found")
        return

    if thinker.approval_status != "approved" or not thinker.active:
        log.info(
            "discover_thinker_not_eligible",
            approval_status=thinker.approval_status,
            active=thinker.active,
        )
        return

    now = _now()
    jobs_created = 0

    # 1. Create guest discovery job (Podcast Index search)
    guest_job = Job(
        id=uuid.uuid4(),
        job_type="discover_guests_podcastindex",
        payload={"thinker_id": str(thinker.id)},
        priority=5,
        status="pending",
        attempts=0,
        max_attempts=3,
        created_at=now,
    )
    session.add(guest_job)
    jobs_created += 1

    # 2. Create fetch jobs for approved, never-fetched owned sources
    result = await session.execute(
        select(Source).where(
            Source.thinker_id == thinker.id,
            Source.approval_status == "approved",
            Source.active == True,  # noqa: E712
            Source.last_fetched == None,  # noqa: E711
        )
    )
    unfetched_sources = result.scalars().all()

    for source in unfetched_sources:
        fetch_payload = {"source_id": str(source.id)}
        # If this is a guest source, add guest filtering
        is_guest = source.config.get("is_guest_source", False) if source.config else False
        if is_guest:
            fetch_payload["guest_filter_thinker_id"] = str(thinker.id)

        fetch_job = Job(
            id=uuid.uuid4(),
            job_type="fetch_podcast_feed",
            payload=fetch_payload,
            priority=2,
            status="pending",
            attempts=0,
            max_attempts=3,
            created_at=now,
        )
        session.add(fetch_job)
        jobs_created += 1

    await session.commit()

    log.info(
        "discover_thinker_complete",
        thinker_name=thinker.name,
        jobs_created=jobs_created,
        unfetched_sources=len(unfetched_sources),
    )
