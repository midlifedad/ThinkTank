"""Handler: discover_guests_listennotes -- Discover guest appearances via Listen Notes.

Searches the Listen Notes API for episodes featuring a given thinker,
and registers discovered podcast feeds as new Sources pending LLM approval.
Deduplicates by normalized URL to prevent duplicate source registration.

Spec reference: Section 5.4 (guest discovery), DISC-02.
"""

from __future__ import annotations

import os

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.thinktank.discovery.listennotes_client import ListenNotesClient
from src.thinktank.ingestion.url_normalizer import normalize_url
from src.thinktank.models.job import Job
from src.thinktank.models.source import Source
from src.thinktank.models.thinker import Thinker

logger = structlog.get_logger(__name__)


async def handle_discover_guests_listennotes(
    session: AsyncSession, job: Job
) -> None:
    """Discover guest appearances via Listen Notes API.

    Reads thinker_id from job.payload, searches Listen Notes for episodes
    featuring the thinker, and creates Source rows for new podcast feeds.

    Args:
        session: Active database session.
        job: The discover_guests_listennotes job with payload containing thinker_id.

    Raises:
        ValueError: If LISTENNOTES_API_KEY env var is missing, or if rate-limited.
    """
    thinker_id = job.payload.get("thinker_id")
    log = logger.bind(job_id=str(job.id), thinker_id=thinker_id)

    if not thinker_id:
        log.warning("discover_guests_listennotes_no_thinker_id")
        return

    thinker = await session.get(Thinker, thinker_id)
    if thinker is None:
        log.warning("discover_guests_listennotes_thinker_not_found")
        return

    api_key = os.environ.get("LISTENNOTES_API_KEY")
    if not api_key:
        raise ValueError("LISTENNOTES_API_KEY environment variable not set")

    client = ListenNotesClient(api_key)
    data = await client.search_episodes_by_person(
        session, worker_id=str(job.id), person_name=thinker.name
    )

    if data is None:
        raise ValueError("Rate limited by Listen Notes")

    sources_created = 0
    for result in data.get("results", []):
        podcast = result.get("podcast", {})
        rss_url = podcast.get("rss")
        if not rss_url:
            continue

        normalized = normalize_url(rss_url)

        # Check for existing source with same URL
        existing = await session.execute(
            select(Source).where(Source.url == normalized)
        )
        if existing.scalar_one_or_none() is not None:
            continue

        source = Source(
            thinker_id=thinker.id,
            source_type="podcast_rss",
            name=podcast.get("title_original", "Unknown Podcast"),
            url=normalized,
            approval_status="pending_llm",
        )
        session.add(source)
        sources_created += 1

    await session.commit()

    log.info(
        "discover_guests_listennotes_complete",
        thinker_name=thinker.name,
        sources_created=sources_created,
    )
